"""EMS decision logic – determines operating mode and triggers cost accounting."""
import logging
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import TYPE_CHECKING

from battery_model import BatteryModel
from const import (
    DAILY_VALUE_GRACE_SEC,
    FORECAST_MAX_AGE_SEC,
    PRICE_MAX_AGE_SEC,
    SENSOR_MAX_AGE_SEC,
    EMSMode,
)
from event_log import EventLog, LogEntry

if TYPE_CHECKING:
    from config_loader import Config
    from consumption_model import ConsumptionModel, Prediction
    from cost_optimizer import CostOptimizer
    from event_log import EventLog
    from ha_ws_client import HAWebSocketClient
    from inverter_controller import InverterController
    from solcast_client import SolcastClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class ModeDecision:
    """One mode proposal plus why, and whether it may skip the dwell time."""

    mode: EMSMode
    reason: str
    urgent: bool = False   # safety / data-loss transitions bypass the debounce


class EMSController:
    """Evaluates sensor readings and determines the current EMS operating mode."""

    def __init__(
        self,
        config: "Config",
        ws_client: "HAWebSocketClient",
        cost_optimizer: "CostOptimizer",
        inverter: "InverterController | None" = None,
        consumption_model: "ConsumptionModel | None" = None,
        solcast: "SolcastClient | None" = None,
        event_log: "EventLog | None" = None,
    ) -> None:
        self._cfg = config
        self._ws = ws_client
        self._optimizer = cost_optimizer
        self._inverter = inverter
        self._model = consumption_model
        self._solcast = solcast
        self._event_log = event_log if event_log is not None else EventLog()
        self._battery_model = BatteryModel(config)
        self._mode: EMSMode = EMSMode.IDLE
        self._prediction: "Prediction | None" = None
        self._last_price: float | None = None
        self._last_event_log_cleanup: date | None = None
        # Debounce state for _commit(): (proposed mode, first proposed at)
        self._pending: tuple[EMSMode, datetime] | None = None
        self._mode_reason: str = "startup"

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> EMSMode:
        return self._mode

    async def update(self) -> dict:
        """Read current sensor values, determine mode, record costs.

        Returns a dict of all live values for the dashboard.
        """
        cfg = self._cfg
        ws = self._ws

        # Read raw – None means "no reading", which the decision logic must see.
        # Do NOT collapse to 0.0 here: an unavailable load sensor would otherwise
        # make surplus == pv and trigger charging on phantom surplus.
        pv_w = ws.get_state_value(cfg.pv_power_entity)
        load_w = ws.get_state_value(cfg.load_power_entity)
        grid_w = ws.get_state_value(cfg.grid_power_entity)
        bat_soc = ws.get_state_value(cfg.battery_soc_entity)
        bat_w = ws.get_state_value(cfg.battery_power_entity)
        price = ws.get_state_value(cfg.electricity_price_entity)

        # Prefer the live capacity sensor so the decision and the dashboard agree.
        # Reject implausible scales (e.g. an Ah reading) via a plausibility band.
        cap_ha = (
            ws.get_state_value(cfg.battery_capacity_entity)
            if cfg.battery_capacity_entity
            else None
        )
        if cap_ha is not None and 0.5 * cfg.battery_capacity_kwh <= cap_ha <= 2.0 * cfg.battery_capacity_kwh:
            self._battery_model.capacity_kwh = cap_ha
        else:
            self._battery_model.capacity_kwh = cfg.battery_capacity_kwh

        # Compute battery kWh values
        bat_kwh_free = self._battery_model.free_to_charge_kwh(bat_soc) if bat_soc is not None else None
        bat_kwh_use  = self._battery_model.useable_kwh(bat_soc) if bat_soc is not None else None

        # Update prediction before mode decision so it influences GRID_CHARGING
        if self._model:
            self._prediction = await self._model.predict(bat_soc)

        # Use today's temperature from weather forecast (provided by WeatherClient via ConsumptionModel)
        outdoor_temp = self._prediction.temp_today_c if self._prediction else None

        prev_mode = self._mode
        self._mode = self._determine_mode(pv_w, load_w, bat_soc, price, bat_kwh_free)

        # Log mode changes to event log
        if self._mode != prev_mode:
            entry = LogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                state="on" if self._mode == EMSMode.GRID_CHARGING else "off",
                battery_kwh_freetochange=round(bat_kwh_free, 3) if bat_kwh_free is not None else 0.0,
                battery_kwh_useable=round(bat_kwh_use, 3) if bat_kwh_use is not None else 0.0,
                predicted_load_kwh=(
                    self._prediction.predicted_load_kwh if self._prediction else None
                ),
                mode=self._mode.value,
                reason=self._mode_reason,
            )
            await self._event_log.append(entry)

        # Log electricity price changes to event log
        if price is not None and self._last_price is not None and price != self._last_price:
            price_entry = LogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                state="price_change",
                battery_kwh_freetochange=round(bat_kwh_free, 3) if bat_kwh_free is not None else 0.0,
                battery_kwh_useable=round(bat_kwh_use, 3) if bat_kwh_use is not None else 0.0,
                predicted_load_kwh=(
                    self._prediction.predicted_load_kwh if self._prediction else None
                ),
                entry_type="price_change",
                price_eur_kwh=round(price, 4),
                mode=self._mode.value,
                reason=self._mode_reason,
            )
            await self._event_log.append(price_entry)
        if price is not None:
            self._last_price = price

        # Daily event log cleanup
        today = date.today()
        if self._last_event_log_cleanup != today:
            await self._event_log.cleanup_old_entries(cfg.event_log_retention_days)
            self._last_event_log_cleanup = today

        # Apply inverter control (simulation or real)
        if self._inverter:
            await self._inverter.apply_mode(self._mode)

        feed_in_kwh_ha = (
            ws.get_state_value(cfg.feed_in_energy_entity)
            if cfg.feed_in_energy_entity
            else None
        )
        grid_import_kwh_ha = (
            ws.get_state_value(cfg.grid_import_energy_entity)
            if cfg.grid_import_energy_entity
            else None
        )
        load_total_kwh_ha = (
            ws.get_state_value(cfg.load_consumption_entity)
            if cfg.load_consumption_entity
            else None
        )
        # Lifetime counters – preferred over the daily sensors above, because the
        # day is then cut on our clock rather than the inverter's.
        grid_import_total_kwh_ha = (
            ws.get_state_value(cfg.grid_import_total_entity)
            if cfg.grid_import_total_entity
            else None
        )
        feed_in_total_kwh_ha = (
            ws.get_state_value(cfg.feed_in_total_entity)
            if cfg.feed_in_total_entity
            else None
        )
        load_total_lifetime_kwh_ha = (
            ws.get_state_value(cfg.load_consumption_total_entity)
            if cfg.load_consumption_total_entity
            else None
        )
        # Scenario 2 sensors (optional – None when entity not configured or unavailable)
        bat_charge_kwh_ha = (
            ws.get_state_value(cfg.battery_charge_entity)
            if cfg.battery_charge_entity
            else None
        )
        bat_discharge_kwh_ha = (
            ws.get_state_value(cfg.battery_discharge_entity)
            if cfg.battery_discharge_entity
            else None
        )
        bat_capacity_kwh_ha = (
            ws.get_state_value(cfg.battery_capacity_entity)
            if cfg.battery_capacity_entity
            else None
        )
        today_production_kwh_ha = (
            ws.get_state_value(cfg.today_production_entity)
            if cfg.today_production_entity
            else None
        )
        today_losses_kwh_ha = (
            ws.get_state_value(cfg.today_losses_entity)
            if cfg.today_losses_entity
            else None
        )
        self._optimizer.record_tick(
            # Accounting keeps counting with 0 W for a missing reading; only the
            # control path above must distinguish None from zero.
            grid_power_w=grid_w or 0.0,
            pv_power_w=pv_w or 0.0,
            load_power_w=load_w or 0.0,
            battery_power_w=bat_w or 0.0,
            price_eur_kwh=price if price is not None else 0.0,
            interval_sec=cfg.update_interval_sec,
            outdoor_temp_c=outdoor_temp,
            feed_in_kwh_ha=feed_in_kwh_ha,
            grid_import_kwh_ha=grid_import_kwh_ha,
            load_total_kwh_ha=load_total_kwh_ha,
            bat_charge_kwh_ha=bat_charge_kwh_ha,
            bat_discharge_kwh_ha=bat_discharge_kwh_ha,
            today_production_kwh_ha=today_production_kwh_ha,
            today_losses_kwh_ha=today_losses_kwh_ha,
            grid_import_total_kwh_ha=grid_import_total_kwh_ha,
            feed_in_total_kwh_ha=feed_in_total_kwh_ha,
            load_total_lifetime_kwh_ha=load_total_lifetime_kwh_ha,
        )
        await self._optimizer.flush_to_db()

        sim_label = ""
        if self._inverter and self._inverter.simulation and cfg.battery_control_enabled:
            sim_label = " [SIM]"

        def _fmt(value: float | None, digits: int = 0) -> str:
            return f"{value:.{digits}f}" if value is not None else "?"

        # A mode change waits out cfg.mode_dwell_sec before it is applied. While
        # it waits, _mode_reason still holds the *old* reason, so without this
        # the tick line reads like nothing is happening – which is exactly how a
        # correct, merely-debounced transition gets mistaken for a stuck EMS.
        _LOGGER.info(
            "EMS [%s%s] PV=%sW Load=%sW Grid=%sW SoC=%s%% Price=%s€/kWh (%s)%s",
            self._mode.value,
            sim_label,
            _fmt(pv_w),
            _fmt(load_w),
            _fmt(grid_w),
            _fmt(bat_soc),
            _fmt(price, 4),
            self._mode_reason,
            self._pending_suffix(),
        )

        summary = await self._optimizer.summary_with_db()

        # Build warnings list
        warnings = list(self._optimizer.get_startup_warnings())
        warnings.extend(self._build_sensor_warnings())

        result = {
            "mode": self._mode.value,
            "mode_reason": self._mode_reason,
            "pv_export_priority_enabled": cfg.pv_export_priority_enabled,
            "solcast_remaining_used_kwh": self._forecast_remaining_kwh(),
            "pv_power_w": pv_w,
            "load_power_w": load_w,
            "grid_power_w": grid_w,
            "battery_soc_pct": bat_soc,
            "battery_power_w": bat_w,
            "electricity_price_eur": price,
            "is_cheap_rate": self._optimizer.is_cheap_rate(price),
            "price_tier": self._optimizer.price_tier(price),
            "battery_control_active": cfg.battery_control_enabled,
            "battery_control_simulation": cfg.battery_control_simulation,
            "battery_kwh_freetochange": round(bat_kwh_free, 3) if bat_kwh_free is not None else None,
            "battery_kwh_useable": round(bat_kwh_use, 3) if bat_kwh_use is not None else None,
            "warnings": warnings,
            "log": self._event_log.to_list(),
            **summary,
        }

        # Derived cost sensors
        grid_cost = summary.get("today_grid_cost_eur", 0.0)
        gc_cost = summary.get("today_grid_charge_cost_eur", 0.0)
        load_kwh = summary.get("today_load_total_kwh", 0.0)
        gc_cost_bilanz = summary.get("today_grid_charge_cost_bilanz_eur")
        gc_cost_for_subtraction = gc_cost_bilanz if gc_cost_bilanz is not None else gc_cost
        result["today_cost_without_grid_charge"] = round(max(0.0, grid_cost - gc_cost_for_subtraction), 6)
        result["today_cost_fix_price_tariff"] = round(load_kwh * cfg.fix_price + cfg.daily_base_price_eur, 6)

        # Scenario 2: battery capacity from sensor (fallback to config value)
        if bat_capacity_kwh_ha is not None:
            result["battery_capacity_kwh"] = round(bat_capacity_kwh_ha, 2)
        else:
            result["battery_capacity_kwh"] = cfg.battery_capacity_kwh

        # Battery state string from sensor
        bat_state_ha = (
            ws.get_state_value(cfg.battery_state_entity)
            if cfg.battery_state_entity
            else None
        )
        if bat_state_ha is not None:
            result["battery_state"] = bat_state_ha

        # Solcast values
        if self._solcast:
            result["solcast_remaining_today_kwh"] = self._solcast.remaining_today_kwh
            result["solcast_today_kwh"] = self._solcast.today_kwh
            result["solcast_tomorrow_kwh"] = self._solcast.tomorrow_kwh

        if self._inverter:
            # *_limit_w = confirmed by HA (None until a write succeeded)
            # *_target_w = what the controller last asked for
            result["charge_current_limit_a"] = self._inverter.charge_current_limit_a
            result["discharge_current_limit_a"] = self._inverter.discharge_current_limit_a
            result["charge_current_target_a"] = self._inverter.charge_current_target_a
            result["discharge_current_target_a"] = self._inverter.discharge_current_target_a
            result["inverter_write_errors"] = self._inverter.write_errors
            result["inverter_write_unconfirmed"] = self._inverter.write_unconfirmed
        if self._prediction:
            result["predicted_load_kwh"] = self._prediction.predicted_load_kwh
            result["predicted_pv_kwh"] = self._prediction.predicted_pv_kwh
            result["prediction_confidence"] = self._prediction.confidence
            result["prediction_source"] = self._prediction.source
            result["temp_today_c"] = self._prediction.temp_today_c
            result["temp_tomorrow_c"] = self._prediction.temp_tomorrow_c
        return result

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    def _determine_mode(
        self,
        pv_w: float | None,
        load_w: float | None,
        bat_soc: float | None,
        price: float | None,
        bat_kwh_free: float | None = None,
        now: datetime | None = None,
    ) -> EMSMode:
        """Pick the operating mode, then apply the dwell time before committing."""
        now = now or datetime.now().astimezone()
        decision = self._decide(pv_w, load_w, bat_soc, price, bat_kwh_free, now)
        return self._commit(decision, now)

    def _decide(
        self,
        pv_w: float | None,
        load_w: float | None,
        bat_soc: float | None,
        price: float | None,
        bat_kwh_free: float | None,
        now: datetime,
    ) -> ModeDecision:
        """Pure decision. Every missing/stale input fails CLOSED (no action)."""
        cfg = self._cfg

        # 1. No usable SoC → hand control back to the inverter's own self-use logic.
        #
        # Staleness is checked against battery_power_entity, NOT battery_soc_entity
        # itself: SoC is a coarse percentage that can legitimately sit on the exact
        # same value for hours or, in winter with grid charging off, for days – a
        # timeout on "time since battery_soc_entity last changed" would eventually
        # be wrong for every possible value. battery_power_entity is read from the
        # same BMS/inverter connection and fluctuates continuously whenever that
        # connection is actually alive, so its freshness is what proves the SoC
        # reading is current rather than stuck on a dead link.
        if bat_soc is None or self._is_stale(cfg.battery_power_entity, cfg.sensor_max_age_sec):
            return ModeDecision(EMSMode.IDLE, "soc unavailable", urgent=True)

        # 2. Battery protection, with hysteresis on the way out so the mode
        #    cannot flip back and forth at exactly min_soc.
        if bat_soc < cfg.battery_min_soc:
            return ModeDecision(EMSMode.PROTECT_BATTERY, "soc below min", urgent=True)
        if (self._mode is EMSMode.PROTECT_BATTERY
                and bat_soc < cfg.battery_min_soc + cfg.battery_soc_hysteresis_pct):
            return ModeDecision(EMSMode.PROTECT_BATTERY, "protect hysteresis", urgent=True)

        if bat_soc >= cfg.battery_max_soc:
            # Full: nothing to charge. Surplus exports on its own.
            return ModeDecision(EMSMode.IDLE, "battery full")

        # 3. PV surplus – only when BOTH power readings are live.
        surplus_w: float | None = None
        if (pv_w is not None and load_w is not None
                and not self._is_stale(cfg.pv_power_entity, cfg.sensor_max_age_sec)
                and not self._is_stale(cfg.load_power_entity, cfg.sensor_max_age_sec)):
            surplus_w = pv_w - load_w

        if surplus_w is not None and surplus_w > cfg.pv_surplus_threshold_w:
            hold, reason = self._should_hold_pv_charge(bat_soc, bat_kwh_free, now)
            if hold:
                return ModeDecision(EMSMode.EXPORT_SURPLUS, reason)
            # A hold released by the forecast is a normal, slow transition.
            # A hold released by a guard (low SoC, stale forecast, backstop)
            # must take effect immediately.
            return ModeDecision(
                EMSMode.PV_CHARGING, reason,
                urgent=(reason != "forecast above battery need"),
            )

        # 4. Cheap dynamic tariff → charge from the grid.
        if self._should_grid_charge(price, bat_kwh_free, now):
            return ModeDecision(EMSMode.GRID_CHARGING, "cheap rate, pv cannot fill battery")

        return ModeDecision(EMSMode.IDLE, "no action")

    # ------------------------------------------------------------------

    def _should_hold_pv_charge(
        self, bat_soc: float, bat_kwh_free: float | None, now: datetime
    ) -> tuple[bool, str]:
        """Grid-friendly gate: export the PV surplus instead of storing it?

        Returns (hold, reason). EVERY failure path returns hold=False, so a
        missing or stale input can never leave the battery empty at nightfall.
        """
        cfg = self._cfg

        if not cfg.pv_export_priority_enabled:
            return False, "export priority off"

        # Hard time-of-day backstop – from here on the battery always charges.
        if now.hour >= cfg.pv_charge_backstop_hour:
            return False, "time backstop"

        # Never withhold charging from a nearly empty battery.
        if bat_soc < cfg.pv_export_min_soc_pct:
            return False, "soc below export floor"

        if bat_kwh_free is None or bat_kwh_free <= 0.05:
            return False, "no free capacity"

        remaining = self._forecast_remaining_kwh()
        if remaining is None:
            return False, "forecast unavailable"

        # Charge once the remaining forecast has fallen to what the battery
        # still needs. Asymmetric threshold: harder to enter the hold than to
        # leave it, so the mode cannot flap around the trigger point.
        target = bat_kwh_free * cfg.pv_charge_margin_factor
        hyst = max(0.0, min(0.5, cfg.pv_charge_hysteresis_frac))
        threshold = target * ((1.0 - hyst) if self._mode is EMSMode.EXPORT_SURPLUS
                              else (1.0 + hyst))

        if remaining > threshold:
            return True, "forecast above battery need"
        return False, "forecast below battery need"

    def _should_grid_charge(
        self, price: float | None, bat_kwh_free: float | None, now: datetime
    ) -> bool:
        """Cheap-rate grid charging. Fails CLOSED on every missing input."""
        cfg = self._cfg

        if price is None or self._is_stale(cfg.electricity_price_entity, cfg.price_max_age_sec):
            return False                                   # no price → no charge
        if not self._optimizer.is_cheap_rate(price):
            return False
        if bat_kwh_free is None or bat_kwh_free <= cfg.grid_charge_min_free_kwh:
            return False

        remaining = self._forecast_remaining_kwh()
        if remaining is None or remaining <= cfg.grid_charge_min_free_kwh:
            # Today's PV is spent (evening, legitimately ~0) or its sensor is
            # stale. Before falling back to a blind time-of-day charge, check
            # tomorrow: if tomorrow's forecast alone can refill the battery,
            # there is no need to buy grid energy tonight.
            tomorrow = self._solcast.tomorrow_kwh if self._solcast else None
            # Date check, not age check: tomorrow's forecast total is written
            # once per day, so under the old forecast_max_age_sec (8h) it counted
            # as stale from ~8h after the last Solcast fetch onwards – i.e. for
            # the whole dark charging window, every night. That silently disabled
            # this very fallback, which exists to avoid buying grid energy that
            # tomorrow's sun would have supplied.
            tomorrow_stale = self._is_stale_daily(cfg.solcast_tomorrow_entity)
            if tomorrow is not None and tomorrow >= 0 and not tomorrow_stale:
                if bat_kwh_free <= tomorrow * cfg.pv_charge_margin_factor + cfg.grid_charge_min_free_kwh:
                    return False   # tomorrow's PV will refill it – skip grid charging

            # No usable forecast at all (today spent/stale AND tomorrow
            # missing/insufficient/stale): only charge in the dark window,
            # where "PV may still arrive today" cannot be true. Replaces the
            # old fail-OPEN branch that charged whenever the prediction was
            # missing or unconfident.
            if cfg.grid_charge_dark_start_hour <= cfg.grid_charge_dark_end_hour:
                return cfg.grid_charge_dark_start_hour <= now.hour < cfg.grid_charge_dark_end_hour
            return (now.hour >= cfg.grid_charge_dark_start_hour
                    or now.hour < cfg.grid_charge_dark_end_hour)

        return bat_kwh_free > remaining * cfg.pv_charge_margin_factor + cfg.grid_charge_min_free_kwh

    def _forecast_remaining_kwh(self) -> float | None:
        """Remaining PV forecast for today – None when missing, stale or absurd."""
        cfg = self._cfg
        if self._solcast is None or not cfg.solcast_remaining_today_entity:
            return None
        if self._is_stale(cfg.solcast_remaining_today_entity, cfg.forecast_max_age_sec):
            return None
        value = self._solcast.remaining_today_kwh
        if value is None or value < 0:
            return None
        return value

    def _is_stale(self, entity_id: str, max_age_sec: float) -> bool:
        if not entity_id:
            return True
        return self._ws.is_stale(entity_id, max_age_sec)

    def _is_stale_daily(self, entity_id: str) -> bool:
        """Staleness for a once-per-day value – asks for the date, not the age."""
        if not entity_id:
            return True
        return self._ws.is_stale_daily(entity_id, DAILY_VALUE_GRACE_SEC)

    # ------------------------------------------------------------------

    def _pending_suffix(self) -> str:
        """" → Mode pending 120/300s" while a debounced change waits, else ""."""
        if self._pending is None:
            return ""
        mode, since = self._pending
        waited = (datetime.now().astimezone() - since).total_seconds()
        return f" → {mode.value} pending {waited:.0f}/{self._cfg.mode_dwell_sec}s"

    def _commit(self, decision: ModeDecision, now: datetime) -> EMSMode:
        """Debounce: a new mode must be requested continuously for
        cfg.mode_dwell_sec before it is applied. Urgent decisions bypass it."""
        cfg = self._cfg

        if decision.mode is self._mode:
            self._pending = None
            self._mode_reason = decision.reason
            return self._mode

        if decision.urgent or cfg.mode_dwell_sec <= 0:
            self._pending = None
            self._mode_reason = decision.reason
            _LOGGER.info("Mode %s → %s (%s, immediate)",
                         self._mode.value, decision.mode.value, decision.reason)
            return decision.mode

        if self._pending is None or self._pending[0] is not decision.mode:
            self._pending = (decision.mode, now)
            _LOGGER.debug("Mode change to %s pending (%s)",
                          decision.mode.value, decision.reason)
            return self._mode

        waited = (now - self._pending[1]).total_seconds()
        if waited < cfg.mode_dwell_sec:
            return self._mode

        self._pending = None
        self._mode_reason = decision.reason
        _LOGGER.info("Mode %s → %s (%s, stable for %.0fs)",
                     self._mode.value, decision.mode.value, decision.reason, waited)
        return decision.mode

    # ------------------------------------------------------------------
    # Warnings
    # ------------------------------------------------------------------

    def _build_sensor_warnings(self) -> list[str]:
        """Return warnings for required sensors that are unavailable or stale."""
        ws = self._ws
        cfg = self._cfg
        warnings = []

        # Battery SoC is checked for presence only, not staleness: it's a coarse
        # percentage that can legitimately sit on the same value for hours or
        # (winter, grid charging off) for days, so "time since last change" is
        # never a meaningful timeout for it. Battery power – checked below –
        # comes from the same BMS connection and fluctuates continuously
        # whenever that connection is alive, so its own staleness warning
        # already covers "the SoC reading might be stuck on a dead link".
        if not cfg.battery_soc_entity:
            warnings.append("Config missing: Battery SoC entity not set")
        elif ws.get_state_value(cfg.battery_soc_entity) is None:
            warnings.append(f"Sensor unavailable: Battery SoC ({cfg.battery_soc_entity})")

        # (entity, label, max age in seconds before the value counts as stale)
        required = [
            (cfg.pv_power_entity, "PV power", SENSOR_MAX_AGE_SEC),
            (cfg.battery_power_entity, "Battery power", SENSOR_MAX_AGE_SEC),
            (cfg.grid_power_entity, "Grid power", SENSOR_MAX_AGE_SEC),
            (cfg.load_power_entity, "Load power", SENSOR_MAX_AGE_SEC),
            (cfg.electricity_price_entity, "Electricity price", PRICE_MAX_AGE_SEC),
        ]
        if cfg.solcast_remaining_today_entity:
            required.append(
                (cfg.solcast_remaining_today_entity, "Solcast remaining today", FORECAST_MAX_AGE_SEC)
            )
        for entity, label, max_age in required:
            if not entity:
                warnings.append(f"Config missing: {label} entity not set")
            elif ws.get_state_value(entity) is None:
                warnings.append(f"Sensor unavailable: {label} ({entity})")
            else:
                age = ws.get_state_age_sec(entity)
                if age is not None and age > max_age:
                    warnings.append(
                        f"Sensor stale: {label} – last update {age / 3600:.1f} h ago ({entity})"
                    )

        # Once-per-day values are checked by date, not by age: the same daily
        # forecast total is fresh in the morning and still correct at night, so
        # every age threshold is wrong for one of the two. Under the previous
        # 8h limit these were flagged stale every single day.
        for entity, label in ((cfg.solcast_today_entity, "Solcast today"),
                              (cfg.solcast_tomorrow_entity, "Solcast tomorrow")):
            if not entity:
                continue
            if ws.get_state_value(entity) is None:
                warnings.append(f"Sensor unavailable: {label} ({entity})")
            elif self._is_stale_daily(entity):
                warnings.append(
                    f"Sensor stale: {label} – no update today ({entity})"
                )

        # Inverter service calls that Home Assistant rejected outright
        if self._inverter is not None and self._inverter.write_errors > 0:
            warnings.append(
                f"Inverter control: {self._inverter.write_errors} failed write(s) – "
                "the inverter may not be in the requested state"
            )
        # Inverter service calls HA accepted, but the entity never reflected
        # the new value (retried every tick until it does)
        if self._inverter is not None and self._inverter.write_unconfirmed > 0:
            warnings.append(
                f"Inverter control: {self._inverter.write_unconfirmed} unconfirmed write(s) – "
                "HA accepted the command but the inverter has not reported the new value yet"
            )

        return warnings
