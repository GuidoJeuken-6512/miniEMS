"""EMS decision logic – determines operating mode and triggers cost accounting."""
import asyncio
import functools
import logging
from dataclasses import dataclass, fields
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING

from battery_capability import BatteryCapabilityTracker
from battery_projection import BatteryContext
from battery_model import BatteryModel
from bridge_planner import BridgePlan, PlanSlot, plan_bridge
from const import (
    DAILY_VALUE_GRACE_SEC,
    ENERGY_PLAN_RECOMPUTE_SEC,
    FORECAST_MAX_AGE_SEC,
    INVERTER_WRITE_CONFIRM_TIMEOUT_SEC,
    PLAN_HORIZON_H,
    PLAN_SLOT_MIN,
    PLAN_STOP_BELOW_KWH,
    PRICE_MAX_AGE_SEC,
    PV_STALE_SUN_BUFFER_SEC,
    SENSOR_MAX_AGE_SEC,
    SOLCAST_DATA_MAX_AGE_SEC,
    SUN_ENTITY,
    EMSMode,
)
from battery_learning import ROUND_TRIP_BOUNDS, LearnedBattery, learn_battery
from battery_plan import Action, BatteryPlan, plan_battery
from energy_plan import EnergyPlan, compute_energy_plan
from forecast_learning import ForecastCorrections, learn_corrections
from energy_value import RATE_TOLERANCE, EnergyValue, assess_energy, should_hold
from event_log import EventLog, LogEntry
from price_classes import classes_for
from phase_b import PhaseBMixin
from phase_c import PhaseCMixin
from price_curve import PriceCurve
from sensor_availability import SensorAvailabilityMonitor

if TYPE_CHECKING:
    from config_loader import Config
    from consumption_model import ConsumptionModel, Prediction
    from cost_optimizer import CostOptimizer
    from ev_controller import EVController
    from heating_controller import HeatingController
    from overrides import OverrideStore
    from resources import ResourceCoordinator
    from store import EnergyStore
    from event_log import EventLog
    from ha_state_client import HAStateClient
    from inverter_controller import InverterController
    from heat_pump_recorder import HeatPumpRecorder
    from hot_water_controller import HotWaterController
    from load_forecast import HourlyLoadRecorder, LoadForecaster
    from solcast_client import SolcastClient
    from weather_client import WeatherClient

_LOGGER = logging.getLogger(__name__)

# Round-trip efficiency assumed by the bridging planner until the optimizer
# has measured one (fresh install).
_DEFAULT_ETA = 0.9
# Nowcasting: how far the measured/forecast PV ratio of the current slot is
# carried forward, and how far it may scale the forecast.
_NOWCAST_HORIZON_H = 2.0
_NOWCAST_MIN_FORECAST_KW = 0.2
_NOWCAST_RATIO_MIN = 0.3
# Only downwards (v4.2.2): a sunny moment says little about the next two hours
# in a broken sky, a cloud does – and a PV forecast too high defers grid
# charging into a window that ends without the sun (17.11.2026).
_NOWCAST_RATIO_MAX = 1.0


@dataclass(frozen=True)
class ModeDecision:
    """One mode proposal plus why, and whether it may skip the dwell time."""

    mode: EMSMode
    reason: str
    urgent: bool = False   # safety / data-loss transitions bypass the debounce


class EMSController(PhaseBMixin, PhaseCMixin):
    """Evaluates sensor readings and determines the current EMS operating mode."""

    def __init__(
        self,
        config: "Config",
        ws_client: "HAStateClient",
        cost_optimizer: "CostOptimizer",
        inverter: "InverterController | None" = None,
        consumption_model: "ConsumptionModel | None" = None,
        solcast: "SolcastClient | None" = None,
        event_log: "EventLog | None" = None,
        capability: "BatteryCapabilityTracker | None" = None,
        load_forecaster: "LoadForecaster | None" = None,
        hourly_recorder: "HourlyLoadRecorder | None" = None,
        weather: "WeatherClient | None" = None,
        heat_pump_recorder: "HeatPumpRecorder | None" = None,
        hot_water: "HotWaterController | None" = None,
        ev: "EVController | None" = None,
        heating: "HeatingController | None" = None,
        resources: "ResourceCoordinator | None" = None,
        store: "EnergyStore | None" = None,
        overrides: "OverrideStore | None" = None,
    ) -> None:
        self._cfg = config
        self._ws = ws_client
        self._optimizer = cost_optimizer
        self._inverter = inverter
        self._model = consumption_model
        self._solcast = solcast
        self._event_log = event_log if event_log is not None else EventLog()
        self._capability = capability if capability is not None else BatteryCapabilityTracker(None)
        self._battery_model = BatteryModel(config)
        self._mode: EMSMode = EMSMode.IDLE
        self._prediction: "Prediction | None" = None
        # Estimated remaining household consumption for the rest of today,
        # from ConsumptionModel.remaining_load_kwh(). None until history
        # exists or the model is disabled – _should_hold_pv_charge() then
        # degrades to comparing the forecast against battery need alone.
        self._remaining_load_kwh: float | None = None
        self._last_price: float | None = None
        self._last_event_log_cleanup: date | None = None
        # Debounce state for _commit(): (proposed mode, first proposed at)
        self._pending: tuple[EMSMode, datetime] | None = None
        self._mode_reason: str = "startup"
        # History-derived value of a stored kWh; refreshed once per tick from the
        # optimizer (which memoises it per day). None until history exists.
        self._discharge_tariff: float | None = None
        # Learned SoC-bucketed charge power (kW); refreshed once per tick from
        # self._capability (async), then read synchronously by
        # _charge_power_kw() inside the (sync) decision path. None until the
        # lookup has run at least once, or when it fell back to the
        # config-derived estimate itself.
        self._learned_charge_kw: float | None = None
        self._last_capability_rollover: date | None = None
        # Der Energiefahrplan – recomputed at most every ENERGY_PLAN_RECOMPUTE_SEC,
        # display-only (see energy_plan.py). None until the first tick has run.
        self._energy_plan: "EnergyPlan | None" = None
        self._last_energy_plan_at: datetime | None = None
        # Stündliche Bedarfsprognose / Überbrückungsplanung (v3.3.0). The plan
        # is recomputed every tick (cheap: ~72 slots); it only drives the
        # decision when load_forecast_enabled and the model is ready.
        self._forecaster = load_forecaster
        self._recorder = hourly_recorder
        self._weather = weather
        self._hp_recorder = heat_pump_recorder
        self._hot_water = hot_water
        self._availability = SensorAvailabilityMonitor()
        self._bridge_plan: BridgePlan | None = None
        self._bridge_status: str = "disabled"
        # A6: battery day plan – shadow mode unless battery_plan_enabled.
        self._battery_plan: BatteryPlan | None = None
        self._battery_plan_status = "not computed"
        self._shadow_logged_slot: datetime | None = None
        # A7: learned battery values and forecast corrections, refreshed hourly.
        self._learned_battery: LearnedBattery | None = None
        self._corrections = ForecastCorrections()
        self._learning_at: datetime | None = None
        # A11: maintenance runs – refreshed at most hourly in update().
        self._full_charge_due = False
        self._maintenance_checked_at: datetime | None = None
        # A1: last (relative, fixed) cheap verdict, to log a disagreement once.
        self._price_class_diff: tuple[bool, bool] | None = None
        # A2–A4: planning slots with load (and PV, when known) from now on,
        # refreshed once per tick in update() for the synchronous _decide().
        # None when no A2/A3/A4 switch is on or the load model is missing.
        self._value_slots: list[PlanSlot] | None = None
        self._buy_value: EnergyValue | None = None       # A3
        self._hold: tuple[bool, str] = (False, "")       # A4 verdict
        self._hold_value: EnergyValue | None = None
        self._gate_diff: tuple[bool, bool] | None = None
        # Phase B (v4.1.0): fuse guard, EV, heating, start advice – phase_b.py.
        self._init_phase_b(ev, heating, resources, store)
        # Phase C (v4.2.0): overrides, plain text, timeline, fidelity, savings.
        self._init_phase_c(overrides, store)

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
        price = self._sensor_price()
        self._check_sensor_availability()

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
            self._remaining_load_kwh = await self._model.remaining_load_kwh(
                self._optimizer.today_load_total_kwh()
            )
        else:
            self._remaining_load_kwh = None
        if self._cfg.load_profile_enabled:
            # A2: the hourly model (temperature, hour of day) instead of the
            # day median minus what was measured so far.
            now_l = datetime.now().astimezone()
            if self._forecaster is not None:
                await self._forecaster.refresh(now_l)
            rest = self._load_between(now_l, self._next_midnight(now_l))
            if rest is not None:
                self._remaining_load_kwh = rest

        # Refresh before deciding: _grid_charge_pays() needs it, and _decide()
        # is synchronous. Memoised per day inside the optimizer, so this is a DB
        # query once a day rather than once a tick.
        self._discharge_tariff = await self._optimizer.avg_discharge_tariff_eur_kwh()

        # Same reason: _should_hold_for_peak_time() (called from the sync
        # _decide() path) needs the learned-or-config charge power, but the
        # history lookup is async – refresh it here, once per tick.
        config_charge_kw = self._config_charge_power_kw()
        if config_charge_kw is not None and bat_soc is not None:
            self._learned_charge_kw = await self._capability.charge_power_kw(bat_soc, config_charge_kw)
        else:
            self._learned_charge_kw = None

        now = datetime.now().astimezone()
        self._update_price_classes(now, price)
        if self._weather is not None:
            # Hourly temperatures feed both the load forecast and – without an
            # outdoor sensor – the hourly history. Cached for 30 min inside.
            await self._weather.fetch_hourly_temps()
        # Hot-water plan first: with control live, its planned electricity
        # replaces the flat hot-water share in the bridge plan below.
        await self._update_maintenance(now, bat_soc)
        await self._update_learning(now)
        # A5 (3): negative price with a full battery – store heat instead.
        unpaid_full = self._update_negative_price_actuators(price, bat_soc)
        if self._hot_water is not None and cfg.negative_price_enabled:
            self._hot_water.set_negative_price(unpaid_full)
        await self._update_hot_water(now, pv_w, grid_w, price, bat_kwh_use, bat_kwh_free)
        await self._update_bridge_plan(pv_w, price, bat_kwh_free, bat_kwh_use, now)
        await self._update_energy_value(now, price, pv_w, bat_kwh_use, bat_kwh_free)
        await self._update_battery_plan(now, price, pv_w, bat_kwh_use, bat_kwh_free)
        # C2: active overrides (EV override goes to the EV controller).
        await self._update_phase_c_pre(now)
        # Phase B: fuse guard and EV before the battery decision (B1–B5).
        await self._update_phase_b_pre(now, price, pv_w, load_w, grid_w, bat_w, bat_soc)
        prev_mode = self._mode
        self._mode = self._determine_mode(pv_w, load_w, bat_soc, price, bat_kwh_free, now)
        self._log_plan_shadow(now)
        await self._update_phase_b_post(now, price, grid_w)
        await self._update_phase_c_post(now, price, pv_w, bat_kwh_free)
        await self._record_hourly(now, load_w)
        await self._record_heat_pump(now)

        today = date.today()

        # Gelernte Ladeleistung: record this tick against the *committed*
        # mode (post-debounce), then persist and roll the previous day into
        # permanent history once, right after local midnight – same trigger
        # as the event-log cleanup below. See battery_capability.py.
        # battery_power is positive while DISCHARGING – the tracker wants the
        # power flowing into the battery.
        # What was asked for this tick (V1 stretching, B1 fuse cap; None = full
        # current) – a tick that delivered the request is not a battery limit.
        requested_w = (self._phase_b_grid_charge_cap(getattr(self, "_v1_requested_w", None))
                       if self._mode is EMSMode.GRID_CHARGING else None)
        self._capability.record_tick(self._mode, bat_soc, -(bat_w or 0.0), requested_w)
        await self._capability.flush_to_db()
        if self._last_capability_rollover is not None and self._last_capability_rollover != today:
            await self._capability.rollover_day(self._last_capability_rollover)
        self._last_capability_rollover = today

        self._update_energy_plan(bat_kwh_free, now)

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
        if self._last_event_log_cleanup != today:
            await self._event_log.cleanup_old_entries(cfg.event_log_retention_days)
            self._last_event_log_cleanup = today

        # A5 (4): negative price with a full battery – stop exporting.
        if self._inverter and cfg.negative_price_enabled:
            await self._inverter.apply_export_limit(unpaid_full)

        # Apply inverter control (simulation or real)
        if self._inverter:
            grid_charge_power_w = (
                self._grid_charge_power_w(bat_kwh_free, now)
                if self._mode is EMSMode.GRID_CHARGING else None
            )
            # B1: within the fuse and §14a limits (None = no guard).
            grid_charge_power_w = self._phase_b_grid_charge_cap(grid_charge_power_w)
            async with self._actuator_lock:
                await self._inverter.apply_mode(self._mode, grid_charge_power_w)
            # Persist write-confirm lifecycle events durably (SQLite survives
            # restarts and days; the add-on's own log buffer does not) – see
            # InverterController.pop_write_events().
            for wevent in self._inverter.pop_write_events():
                await self._log_write_event(wevent)
        if self._hot_water is not None:
            for wevent in self._hot_water.pop_write_events():
                await self._log_write_event(wevent)
        for wevent in self._phase_b_write_events():
            await self._log_write_event(wevent)

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
            # Measured/hourly value, averaged into daily_stats.avg_outdoor_temp_c
            # – the same daily mean the load forecast matches tomorrow against.
            outdoor_temp_c=self._outdoor_temp_c(now),
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
        # The downtime-gap warning describes the past: shown once, then dropped
        # as soon as this tick has been accounted with real readings again.
        if None not in (pv_w, load_w, grid_w, bat_w):
            self._optimizer.clear_startup_warnings()

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
            "price_classes": self._price_classes_dict(),
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
            result["grid_charge_current_target_a"] = getattr(
                self._inverter, "grid_charge_current_target_a", None)
            result["grid_charge_current_limit_a"] = getattr(
                self._inverter, "grid_charge_current_limit_a", None)
            result["inverter_write_errors"] = self._inverter.write_errors
            result["inverter_write_unconfirmed"] = self._inverter.write_unconfirmed
            result["inverter_write_stuck_channels"] = self._inverter.stuck_channel_labels
            result["inverter_write_status"] = self._inverter_write_status()
        if self._prediction:
            result["predicted_load_kwh"] = self._prediction.predicted_load_kwh
            result["predicted_pv_kwh"] = self._prediction.predicted_pv_kwh
            result["prediction_confidence"] = self._prediction.confidence
            result["prediction_source"] = self._prediction.source
            result["temp_today_c"] = self._prediction.temp_today_c
            result["temp_tomorrow_c"] = self._prediction.temp_tomorrow_c
        result["remaining_load_kwh"] = self._remaining_load_kwh
        result["failsafe"] = self._failsafe_spec()
        result["learning"] = self._learning_dict()
        result["battery_plan"] = self._battery_plan_dict()
        result["maintenance_full_charge_due"] = self._full_charge_due
        if self._inverter is not None:
            result["write_changes_today"] = self._inverter.target_changes_today
        result["bridge_plan"] = self._bridge_plan_dict()
        result["energy_value"] = self._energy_value_dict()
        if self._hp_recorder is not None and self._hp_recorder.enabled:
            result["heat_pump"] = self._hp_recorder.status()
        if self._hot_water is not None and self._hot_water.enabled:
            result["hot_water"] = self._hot_water.status(now)
        result.update(self._phase_b_status(now))

        if self._energy_plan is not None:
            plan = self._energy_plan
            result["energy_plan"] = {
                "computed_at": plan.computed_at.isoformat(),
                "deficit_kwh": round(plan.deficit_kwh, 3) if plan.deficit_kwh is not None else None,
                "feasible": plan.feasible,
                "reason": plan.reason,
                "estimated_cost_eur": round(plan.estimated_cost_eur, 4),
                "windows": [
                    {
                        "start": w.start.isoformat(),
                        "end": w.end.isoformat(),
                        "rate_eur_kwh": round(w.rate_eur_kwh, 4),
                        "energy_kwh": round(w.energy_kwh, 3),
                    }
                    for w in plan.windows
                ],
            }
        # Phase C: plain text, timeline, next action, fidelity, savings, alerts.
        result.update(self._phase_c_status(result, now))
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
        # C2 (rank 3): a manual override, never over a hard limit.
        decision = self._apply_override(decision, bat_soc, now)
        # B2 coupling rule: the battery does not discharge into the EV.
        decision = self._couple_ev(decision)
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

        # 3. (A11) Maintenance full charge: from the grid only in a cheap hour
        #    and only when today's PV will not fill the battery anyway.
        if self._full_charge_from_grid(price, bat_kwh_free, now):
            return ModeDecision(EMSMode.GRID_CHARGING, "maintenance full charge")

        # 3. (A5) Negative price: buying is paid for – charge without the
        #    economic gate, but leave room for the PV still to come.
        if self._negative_price_charge(price, bat_kwh_free, now):
            return ModeDecision(EMSMode.GRID_CHARGING, "negative price")

        # 3b. (A6) Battery day plan in control: slot 0 of the plan decides.
        planned = self._plan_decision(surplus_w, now)
        if planned is not None:
            return planned

        # 3a. Überbrückungsplanung: a small PV surplus must not block a grid
        #     charge the plan needs (winter noon: 300 W surplus, 15 kWh
        #     evening deficit). GRID_CHARGING still stores the PV as well.
        if (self._bridge_in_control()
                and self._should_grid_charge(price, bat_kwh_free, now)):
            plan = self._bridge_plan
            until = plan.cheap_until.strftime("%H:%M") if plan.cheap_until else "?"
            return ModeDecision(EMSMode.GRID_CHARGING,
                                f"bridge plan: {plan.need_kwh:.1f} kWh by {until}")

        if surplus_w is not None and surplus_w > cfg.pv_surplus_threshold_w:
            hold, reason = self._should_hold_pv_charge(bat_soc, bat_kwh_free, now,
                                                       price=price, surplus_w=surplus_w)
            if hold:
                return ModeDecision(EMSMode.EXPORT_SURPLUS, reason)
            # A hold released by the forecast is a normal, slow transition.
            # A hold released by a guard (low SoC, stale forecast, backstop)
            # must take effect immediately.
            return ModeDecision(
                EMSMode.PV_CHARGING, reason,
                urgent=(reason not in ("forecast above battery+load need", "feed-in limit")),
            )

        # 4. Cheap dynamic tariff → charge from the grid.
        if self._should_grid_charge(price, bat_kwh_free, now):
            if self._bridge_in_control():
                plan = self._bridge_plan
                until = plan.cheap_until.strftime("%H:%M") if plan.cheap_until else "?"
                return ModeDecision(
                    EMSMode.GRID_CHARGING,
                    f"bridge plan: {plan.need_kwh:.1f} kWh by {until}",
                )
            return ModeDecision(EMSMode.GRID_CHARGING, "cheap rate, pv cannot fill battery")

        # 5. A4 – keep the stored energy for dearer hours. After the grid-
        #    charge check on purpose: in a cheap window both would apply, and
        #    the hold must never crowd out a charge (GRID_CHARGING blocks
        #    discharging anyway).
        if cfg.discharge_hold_enabled and self._hold[0]:
            return ModeDecision(EMSMode.HOLD_FOR_PEAK, self._hold[1])

        return ModeDecision(EMSMode.IDLE, "no action")

    # ------------------------------------------------------------------

    def _should_hold_pv_charge(
        self,
        bat_soc: float,
        bat_kwh_free: float | None,
        now: datetime,
        price: float | None = None,
        surplus_w: float | None = None,
    ) -> tuple[bool, str]:
        """Grid-friendly gate: export the PV surplus instead of storing it?

        Returns (hold, reason). EVERY failure path returns hold=False, so a
        missing or stale input can never leave the battery empty at nightfall.
        """
        cfg = self._cfg

        if not cfg.pv_export_priority_enabled:
            return False, "export priority off"

        # A11: a maintenance full charge is due – store all PV.
        if self._full_charge_due:
            return False, "maintenance full charge"
        # A5 (1): exporting earns nothing (or costs) – store the PV instead.
        unpaid = self._export_unpaid(price)
        if unpaid:
            return False, unpaid
        # A5 (6): above the feed-in limit the surplus would be curtailed.
        if cfg.feed_in_limit_w > 0 and surplus_w is not None and surplus_w > cfg.feed_in_limit_w:
            return False, "feed-in limit"

        # Hard time-of-day backstop – from here on the battery always charges.
        if now.hour >= cfg.pv_charge_backstop_hour:
            return False, "time backstop"

        # Never withhold charging from a nearly empty battery.
        if bat_soc < cfg.pv_export_min_soc_pct:
            return False, "soc below export floor"

        if bat_kwh_free is None or bat_kwh_free <= 0.05:
            return False, "no free capacity"

        # V3a (docs/technical/energiefahrplan.md): hold exactly until the real
        # PV peak, sized by how long the battery actually needs to charge –
        # takes over whenever a confident answer is possible, ahead of the
        # coarser remaining-forecast comparison below. But V3a's time budget
        # (bat_kwh_free / charge_kw) assumes the full configured/learned
        # charge rate is actually achievable, and stays silent about the one
        # thing the remaining-forecast comparison DOES know: whether today's
        # PV has already fallen to what the battery+house still need. Cross-
        # check it and release as soon as EITHER signal says so – observed
        # live 2026-09-13: V3a held until ~17:00 with only ~2 kWh free
        # (T_needed_h a mere 15-30 min at the nameplate rate), afternoon PV
        # never reached that rate, and the battery topped out at 88% instead
        # of 95% before sunset with the window already gone.
        forecast_result = self._should_hold_by_forecast(bat_kwh_free, now)

        if cfg.solcast_peak_time_today_entity:
            peak_result = self._should_hold_for_peak_time(bat_kwh_free, now)
            if peak_result is not None:
                # Only a genuine "remaining has fallen below need" verdict
                # overrides V3a's hold – "forecast unavailable" (missing/
                # stale data, itself a deliberate fail-open default) must
                # never masquerade as this stronger signal.
                if peak_result[0] and forecast_result == (False, "forecast below battery+load need"):
                    return forecast_result
                return peak_result

        return forecast_result

    def _should_hold_for_peak_time(
        self, bat_kwh_free: float, now: datetime
    ) -> tuple[bool, str] | None:
        """V3a: hold until the PV peak minus the time needed to charge.

        Returns None when no confident answer is possible – peak-time sensor
        missing/stale, or no usable charge-power estimate (battery_voltage
        unavailable) – so the caller falls back to _should_hold_by_forecast().
        In practice the peak-time and remaining-forecast sensors come from
        the same Solcast integration and fail together, so this fallback
        degrades to the same "forecast unavailable" -> hold=False result
        either way.

        The one case that is NOT a fallback: once the peak has already
        passed (e.g. a restart in the afternoon), holding any longer serves
        no purpose and must not be overridden by a stale, over-optimistic
        remaining-forecast estimate – this is the "hold survived from
        sunrise to 10:55" case the roadmap doc records.
        """
        cfg = self._cfg
        entity = cfg.solcast_peak_time_today_entity
        peak_today = self._ws.get_state_datetime(entity)
        if peak_today is None or self._is_stale_daily(entity):
            return None

        peak_local = peak_today.astimezone(now.tzinfo)
        if now >= peak_local:
            return False, "peak time passed"

        charge_kw = self._charge_power_kw()
        if charge_kw is None or charge_kw <= 0:
            return None

        t_needed_h = bat_kwh_free / charge_kw
        # deadline = pv_charge_backstop_hour, reinterpreted here as "battery
        # must be full again by this local hour" rather than "always charge
        # from here on" – see docs/technical/energiefahrplan.md, V3a.
        deadline = now.replace(hour=cfg.pv_charge_backstop_hour, minute=0, second=0, microsecond=0)
        if deadline <= now:
            deadline += timedelta(days=1)
        latest_start = deadline - timedelta(hours=t_needed_h)
        actual_start = min(peak_local, latest_start)

        if now < actual_start:
            return True, "peak-time hold"
        return False, "peak-time window reached"

    def _charge_power_kw(self) -> float | None:
        """Assumed charging power, for T_needed_h = bat_kwh_free / charge_kw.

        The SoC-bucketed learned value from history takes precedence
        whenever enough qualifying history exists (refreshed once per tick
        into self._learned_charge_kw – see update() and
        battery_capability.py, "Gelernte Ladeleistung"); otherwise the
        config-derived current × voltage estimate.

        None when neither is available (battery_voltage unavailable, and no
        learned value either) – callers then fall back to the older,
        voltage-independent remaining-forecast estimate instead of guessing.
        """
        if self._learned_charge_kw is not None:
            return self._learned_charge_kw
        return self._config_charge_power_kw()

    def _config_charge_power_kw(self) -> float | None:
        """Config-derived charging power: current × voltage.

        None when battery_voltage is unavailable.
        """
        cfg = self._cfg
        if not cfg.battery_voltage_entity:
            return None
        voltage = self._ws.get_state_value(cfg.battery_voltage_entity)
        if voltage is None or voltage <= 0:
            return None
        return cfg.battery_max_charge_current_a * voltage / 1000.0

    def _should_hold_by_forecast(self, bat_kwh_free: float, now: datetime) -> tuple[bool, str]:
        """Menge-based fallback: compare the remaining PV forecast to the need.

        Pre-V3a logic, kept as the fallback for installs without a usable
        peak-time sensor or battery voltage reading.
        """
        remaining = self._forecast_remaining_kwh()
        if remaining is None:
            return False, "forecast unavailable"

        # Charge once the remaining PV forecast has fallen to what the
        # battery still needs *plus* what the house is still expected to
        # consume today. PV covers the house first – only the leftover ever
        # reaches the battery – so comparing the forecast against
        # bat_kwh_free alone overstates the spare PV and holds the export
        # too long. Observed live: the hold survived from sunrise to 10:55
        # while SoC sat flat, and only released because a live Solcast
        # refresh happened to cut the forecast, not because the need
        # estimate ever caught up with reality.
        # self._remaining_load_kwh is None on a fresh install (no history
        # yet) – degrades to the old battery-only comparison.
        need = bat_kwh_free + (self._remaining_load_kwh or 0.0)
        target = need * self._cfg.pv_charge_margin_factor

        # Asymmetric threshold: harder to enter the hold than to leave it,
        # so the mode cannot flap around the trigger point.
        hyst = max(0.0, min(0.5, self._cfg.pv_charge_hysteresis_frac))
        threshold = target * ((1.0 - hyst) if self._mode is EMSMode.EXPORT_SURPLUS
                              else (1.0 + hyst))

        if remaining > threshold:
            return True, "forecast above battery+load need"
        return False, "forecast below battery+load need"

    def _should_grid_charge(
        self, price: float | None, bat_kwh_free: float | None, now: datetime
    ) -> bool:
        """Cheap-rate grid charging. Fails CLOSED on every missing input."""
        cfg = self._cfg

        if price is None or self._is_stale(cfg.electricity_price_entity, cfg.price_max_age_sec):
            return False                                   # no price → no charge

        # Überbrückungsplanung (v3.3.0): when active, the plan alone decides –
        # it already contains the economics (per slot), PV, the household
        # load and the deferral to a cheaper window. Start above
        # grid_charge_min_free_kwh, keep going until the remaining need is
        # down to PLAN_STOP_BELOW_KWH (hysteresis against flapping).
        if self._bridge_in_control():
            if bat_kwh_free is None or bat_kwh_free <= 0:
                return False
            threshold = (PLAN_STOP_BELOW_KWH if self._mode is EMSMode.GRID_CHARGING
                         else cfg.grid_charge_min_free_kwh)
            return self._bridge_plan.need_kwh > threshold   # type: ignore[union-attr]

        if not self._optimizer.is_cheap_rate(price):
            return False
        if not self._grid_charge_pays(price):
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
                ww_pv = (self._hot_water.planned_pv_kwh_before(self._plan_deadline(now))
                         if self._hot_water is not None else 0.0)
                # PV covers the house first: tonight's rest and tomorrow's load
                # come off before anything reaches the battery (v3.7.1).
                tomorrow_load = (self._prediction.predicted_load_kwh
                                 if self._prediction is not None
                                 and self._prediction.predicted_load_kwh > 0 else 0.0)
                if cfg.load_profile_enabled:
                    # A2: tomorrow from the hourly model and tomorrow's temperatures.
                    midnight = self._next_midnight(now)
                    modelled = self._load_between(midnight, midnight + timedelta(days=1))
                    if modelled is not None:
                        tomorrow_load = modelled
                need = bat_kwh_free + ww_pv + tomorrow_load + (self._remaining_load_kwh or 0.0)
                if tomorrow > need * cfg.pv_charge_margin_factor:
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

        # Asymmetric hysteresis, mirroring _should_hold_pv_charge: harder to
        # start grid charging than to keep it running. Without it this was a bare
        # threshold, and `remaining` moves by up to 0.47 kWh per 5-minute Solcast
        # interval (measured) – a battery sitting near the trigger point would
        # oscillate, with only mode_dwell_sec damping it.
        #
        # v3.7.1: PV covers the house first, so today's remaining load comes off
        # before anything reaches the battery – same balance as
        # _should_hold_by_forecast(). The old check compared the forecast with
        # the free capacity alone (free ≤ 1.2 × PV + 1 → skip): on 14.11.2026 a
        # 27 kWh forecast "covered" 10 kWh of free capacity, while the house
        # used 17 of the 18 kWh actually produced – four nights without a grid
        # charge although the energy plan saw an 8–11 kWh deficit.
        # remaining_load is None on a fresh install – the battery alone counts.
        need = bat_kwh_free + (self._remaining_load_kwh or 0.0)
        threshold = need * cfg.pv_charge_margin_factor      # PV needed to cover it
        hyst = max(0.0, min(0.5, cfg.pv_charge_hysteresis_frac))
        if self._mode is EMSMode.GRID_CHARGING:
            threshold *= 1.0 + hyst      # keep charging until PV clearly suffices
        else:
            threshold *= 1.0 - hyst      # start only when PV clearly falls short
        if remaining >= threshold:
            return False                 # today's PV fills the battery after the house

        # V2 (docs/technical/energiefahrplan.md): this is the branch the 12-16
        # Uhr NIEDRIG window falls into on a mediocre-PV day – forecast not
        # yet exhausted, price cheap, but PV is objectively running right
        # now. Defer if a later, equally-cheap window still leaves enough
        # time to finish before the next PV peak.
        return not self._should_defer_grid_charge(bat_kwh_free, now)

    def _grid_charge_power_w(self, bat_kwh_free: float | None, now: datetime) -> float | None:
        """V1: stretch the grid-charge power across the remaining price
        window instead of always requesting full current.

        docs/technical/energiefahrplan.md, V1 ("Ladeleistung strecken"): within
        a window of constant price, spreading the same energy over more time
        is cost-neutral – a pure tie-break that also avoids the load spike a
        full-current charge creates. Recomputed fresh every tick rather than
        planned once: if the window is cut short or bat_kwh_free jumps, the
        very next tick already corrects for it – no separate state that
        could go stale.

        Returns Watts, not amps – as of v2.4.0 this function no longer reads
        battery_voltage at all (docs/technical/geraeteprofile.md, "Die
        Watt-Abstraktion": battery_voltage is now consumed at exactly one
        place, InverterController's actuator conversion). Returns `None`
        whenever a precise stretched target can't be computed – the caller
        (InverterController.apply_mode) then uses the actuator's own
        configured max, exactly like the old amp-based fallback to
        `battery_max_charge_current_a` did, just expressed as "defer to
        max" instead of a guessed number.
        """
        cfg = self._cfg
        if bat_kwh_free is None or bat_kwh_free <= 0:
            return None

        step = self._plan_step(now)
        if step is not None and step.action is Action.CHARGE and step.grid_charge_kwh > 0:
            # A6: the energy the plan buys in this slot, over the slot as the
            # plan saw it (plan time → slot end). Constant until the next plan.
            # Before v4.2.4 it was divided by the time *left*, while the energy
            # stayed that of the plan: the request climbed every tick (82 →
            # 172 A in 15 min on 20.11.2026) – ~460 writes in four hours for a
            # battery that took ~80 A anyway.
            slot_h = (step.end - step.start).total_seconds() / 3600.0
            if slot_h > 0 and now < step.end:
                return step.grid_charge_kwh / slot_h * 1000.0

        if self._bridge_in_control() and self._bridge_plan.cheap_until is not None:  # type: ignore[union-attr]
            # Only the planned energy, over the rest of the cheap window.
            plan = self._bridge_plan
            remaining_h = (plan.cheap_until - now).total_seconds() / 3600.0 * 0.8  # type: ignore[union-attr]
            if remaining_h <= 0:
                return None
            energy = min(plan.need_kwh, bat_kwh_free)  # type: ignore[union-attr]
            return energy / remaining_h * 1000.0

        curve = self._price_curve()
        if curve is None:
            return None
        window_end = curve.window_end(now)
        if cfg.day_ahead_enabled:
            # A9: stretch over the whole cheap run, not one 15-min slot.
            window_end = curve.run_end(now, self._rate_tolerance(), timedelta(hours=12)) or window_end
        if window_end is None:
            return None

        # Target finishing at ~80% of the remaining window, not 100%: a
        # safety margin against the window ending slightly early or the
        # inverter taking a tick or two to apply the new current.
        remaining_h = (window_end - now).total_seconds() / 3600.0 * 0.8
        if remaining_h <= 0:
            return None

        energy = bat_kwh_free
        if self._cfg.soc_aware_gate_enabled and self._buy_value is not None:
            energy = min(bat_kwh_free, self._buy_value.buy_kwh)   # A3: only what pays
        p_soll_kw = energy / remaining_h
        return p_soll_kw * 1000.0

    # ------------------------------------------------------------------
    # A1 – relative price classes
    # ------------------------------------------------------------------

    def _price_curve(self) -> PriceCurve | None:
        """The tariff calendar of the price entity, in effective €/kWh."""
        cfg = self._cfg
        return PriceCurve.from_entity(
            self._ws, cfg.electricity_price_entity, cfg.tariff_source,
            unit=cfg.price_unit, adjust=self._price_adjust(),
        )

    def _price_adjust(self):
        """A5 (5): spot price → effective purchase price, or None (as is)."""
        cfg = self._cfg
        if not cfg.price_is_spot:
            return None
        fee, vat = cfg.price_fee_eur_kwh, cfg.price_vat_pct / 100.0
        return lambda spot: (spot + fee) * (1.0 + vat)

    def _sensor_price(self) -> float | None:
        """Current price in effective €/kWh.

        An explicit price_unit converts the sensor as well ("auto" leaves it
        as is – the sensor has always been read as €/kWh); price_is_spot adds
        fees and VAT (A5).
        """
        cfg = self._cfg
        raw = self._ws.get_state_value(cfg.electricity_price_entity)
        if raw is None:
            return None
        divisor = {"ct": 100.0, "eur_mwh": 1000.0}.get(cfg.price_unit)
        if divisor:
            raw /= divisor
        adjust = self._price_adjust()
        return adjust(raw) if adjust is not None else raw

    def _price_classes_dict(self) -> dict | None:
        classes = getattr(self._optimizer, "price_classes", None) if self._cfg.price_classes_enabled else None
        return classes.as_dict() if classes is not None else None

    def _update_price_classes(self, now: datetime, price: float | None) -> None:
        """Today's relative price classes → optimizer and hot water.

        Off, or no tariff calendar → None, and the fixed thresholds decide as
        before. Every change of the cheap/not-cheap verdict against the fixed
        threshold is logged once, so the two can be compared in the log.
        """
        cfg = self._cfg
        if not cfg.price_classes_enabled:
            return   # optimizer and hot water keep the fixed thresholds
        classes = classes_for(
            self._price_curve(),
            now, cfg.price_cheap_hours, cfg.price_hysteresis_min_eur,
            cfg.price_hysteresis_max_eur, cfg.price_flat_spread_eur,
        )
        self._optimizer.set_price_classes(classes)
        if self._hot_water is not None:
            # Nothing is low on a flat day: no slot may count as cheap.
            self._hot_water.set_cheap_rate(
                None if classes is None
                else (float("-inf") if classes.flat else classes.limit_eur + 1e-6)
            )
        if classes is not None and price is not None:
            relative = classes.is_low(price)
            fixed = price < cfg.cheap_rate_threshold_eur
            verdict = (relative, fixed)
            if relative != fixed and verdict != self._price_class_diff:
                _LOGGER.info(
                    "Price classes: %.4f €/kWh is %s relative to the day (limit %.4f) but %s "
                    "against cheap_rate_threshold_eur %.4f – the relative class decides",
                    price, "low" if relative else "not low", classes.limit_eur,
                    "low" if fixed else "not low", cfg.cheap_rate_threshold_eur,
                )
            self._price_class_diff = verdict

    # ------------------------------------------------------------------
    # A2–A4 – load per window, value of a kWh, discharge hold
    # ------------------------------------------------------------------

    @staticmethod
    def _next_midnight(now: datetime) -> datetime:
        return (now + timedelta(days=1)).replace(hour=0, minute=0, second=0, microsecond=0)

    def _load_between(self, start: datetime, end: datetime) -> float | None:
        """A2: expected household load in [start, end) from the hourly model.

        Walked in 30-min steps with the hourly temperature forecast, like the
        planning slots. None without a fitted model.
        """
        fc = self._forecaster
        if fc is None or fc.model is None:
            return None
        step = timedelta(minutes=PLAN_SLOT_MIN)
        total = 0.0
        t = start
        while t < end:
            nxt = min(end, t + step)
            temp = self._weather.hourly_temp_at(t) if self._weather is not None else None
            total += fc.predict_kwh(t, (nxt - t).total_seconds() / 60.0, temp) or 0.0
            t = nxt
        return total * self._load_factor()

    def _round_trip_eta(self) -> float:
        """Round-trip efficiency a bought kWh suffers (A7a: learned, when on)."""
        lb = self._learned_battery
        if self._cfg.battery_learned_values_enabled and lb is not None and lb.round_trip:
            return lb.round_trip
        return self._measured_eta() or _DEFAULT_ETA

    def _measured_eta(self) -> float | None:
        """Today's measured inverter efficiency – None unless plausible.

        The value is a running ratio of today's counters, (production − losses)
        ÷ production. In the morning production is tiny and the losses dominate:
        on 17.11.2026 it read −1000 % at 09:39, 11 % at 13:49 and 44 % at 15:00.
        Taken as round trip, 11 % made one bought kWh cost 27.4 ct ÷ 0.11 – the
        day plan stopped grid charging in the middle of the low-price window and
        then held the battery through the evening peak. Outside the bounds a
        learned round trip may take (ROUND_TRIP_BOUNDS) the caller uses its
        default instead.
        """
        eta = self._optimizer.today_efficiency()
        if eta is None or not ROUND_TRIP_BOUNDS[0] <= eta <= ROUND_TRIP_BOUNDS[1]:
            return None
        return eta

    def _discharge_eta(self) -> float:
        """Share of a stored kWh that reaches the house (A7a: learned, when on)."""
        lb = self._learned_battery
        if self._cfg.battery_learned_values_enabled and lb is not None and lb.eta_discharge:
            return lb.eta_discharge
        return self._round_trip_eta() ** 0.5

    async def _update_learning(self, now: datetime) -> None:
        """A7: relearn battery values and forecast corrections at most hourly.

        Recorded and shown always; used only behind their switches.
        """
        if self._learning_at is not None and (now - self._learning_at).total_seconds() < 3600:
            return
        self._learning_at = now
        recent = getattr(self._optimizer, "recent_complete_days", None)
        if recent is not None:
            try:
                rows = await recent(14)
                self._learned_battery = learn_battery(rows, self._cfg.battery_capacity_kwh)
            except Exception as exc:   # learning is advice, never block control
                _LOGGER.warning("Battery learning failed: %s", exc)
        if self._recorder is not None:
            try:
                current = now.replace(minute=0, second=0, microsecond=0).isoformat()
                rows = [r for r in await self._recorder.history_since((now - timedelta(days=14)).date())
                        if r.get("hour_start") != current]
                self._corrections = learn_corrections(rows)
            except Exception as exc:
                _LOGGER.warning("Forecast learning failed: %s", exc)

    def _learning_dict(self) -> dict:
        return {
            "battery": self._learned_battery.as_dict() if self._learned_battery else None,
            "battery_in_use": self._cfg.battery_learned_values_enabled,
            "forecast": self._corrections.as_dict(),
            "pv_correction_in_use": self._cfg.pv_correction_enabled,
            "plan_feedback_in_use": self._cfg.plan_feedback_enabled,
        }

    def _pv_factor(self, moment: datetime) -> float:
        cfg = self._cfg
        return self._corrections.pv_factor(moment.hour, cfg.pv_correction_enabled,
                                           cfg.plan_feedback_enabled)

    def _load_factor(self) -> float:
        return self._corrections.load_total if self._cfg.plan_feedback_enabled else 1.0

    def _rate_tolerance(self) -> float:
        """Band within which two rates count as equally cheap.

        A9: with day-ahead prices "equal" is a band – the hysteresis of the
        day's price classes (A1) when known, else 1 ct.
        """
        if not self._cfg.day_ahead_enabled:
            return RATE_TOLERANCE
        classes = getattr(self._optimizer, "price_classes", None)
        return max(RATE_TOLERANCE, classes.hysteresis_eur if classes is not None else 0.01)

    async def _update_maintenance(self, now: datetime, bat_soc: float | None) -> None:
        """A11: record today's maxima, refresh the due flags at most hourly."""
        cfg = self._cfg
        boil = self._ws.get_state_value(cfg.boil_temp_entity) if cfg.boil_temp_entity else None
        note = getattr(self._optimizer, "note_maxima", None)
        if note is None:
            return
        note(bat_soc, boil)
        if (self._maintenance_checked_at is not None
                and (now - self._maintenance_checked_at).total_seconds() < 3600
                and not (self._full_charge_due and bat_soc is not None
                         and bat_soc >= cfg.battery_max_soc - 1)):
            return
        self._maintenance_checked_at = now
        due = False
        if cfg.battery_full_charge_days > 0:
            days = await self._optimizer.days_since("max_soc_pct", cfg.battery_max_soc - 1)
            due = days is not None and days >= cfg.battery_full_charge_days
            if due and not self._full_charge_due:
                _LOGGER.info("Maintenance: battery not full for %d day(s) – full charge due", days)
        self._full_charge_due = due
        if self._hot_water is not None and cfg.hw_hygiene_enabled:
            days = await self._optimizer.days_since("max_boil_temp_c", cfg.hw_hygiene_temp_c - 0.5)
            self._hot_water.set_hygiene_due(days is not None and days >= cfg.hw_hygiene_interval_days)

    def _full_charge_from_grid(
        self, price: float | None, bat_kwh_free: float | None, now: datetime
    ) -> bool:
        """A11: grid-charge for the maintenance full charge right now?"""
        cfg = self._cfg
        if not self._full_charge_due or not cfg.battery_control_enabled:
            return False
        if price is None or bat_kwh_free is None or bat_kwh_free <= PLAN_STOP_BELOW_KWH:
            return False
        if self._is_stale(cfg.electricity_price_entity, cfg.price_max_age_sec):
            return False
        if not self._optimizer.is_cheap_rate(price):
            return False
        remaining = self._forecast_remaining_kwh()
        if remaining is None:
            return True
        return remaining - (self._remaining_load_kwh or 0.0) < bat_kwh_free * cfg.pv_charge_margin_factor

    # ------------------------------------------------------------------
    # A6 – battery day plan
    # ------------------------------------------------------------------

    _PLAN_RECOMPUTE_SEC = 900
    _PLAN_MAX_AGE_SEC = 1800
    _MODE_ACTION = {
        EMSMode.GRID_CHARGING: Action.CHARGE,
        EMSMode.HOLD_FOR_PEAK: Action.HOLD,
        EMSMode.PROTECT_BATTERY: Action.HOLD,
        EMSMode.EXPORT_SURPLUS: Action.EXPORT,
    }

    async def _update_battery_plan(
        self,
        now: datetime,
        price: float | None,
        pv_w: float | None,
        bat_kwh_use: float | None,
        bat_kwh_free: float | None,
    ) -> None:
        """Recompute the plan every 15 min and at every slot boundary.

        Always computed when the inputs exist (shadow mode); it decides only
        with battery_plan_enabled. Every missing input → no plan, status says why.
        """
        cfg = self._cfg
        plan = self._battery_plan
        if (plan is not None and plan.first is not None and now < plan.first.end
                and (now - plan.computed_at).total_seconds() < self._PLAN_RECOMPUTE_SEC):
            return
        self._battery_plan = None
        if bat_kwh_use is None or bat_kwh_free is None:
            self._battery_plan_status = "soc unavailable"
            return
        slots, status, _, _ = self._plan_slots(now, price, pv_w, need_pv=True, need_load=True)
        if slots is None:
            self._battery_plan_status = status
            return
        # Hot-water plan (live) and EV plan (B2) as load per slot.
        slots = self._with_planned_loads(slots)
        charge_kw = self._charge_power_kw()
        if charge_kw is None or charge_kw <= 0:
            self._battery_plan_status = "no charge power estimate"
            return
        voltage = (self._ws.get_state_value(cfg.battery_voltage_entity)
                   if cfg.battery_voltage_entity else None)
        discharge_kw = (cfg.battery_max_discharge_current_a * voltage / 1000.0
                        if voltage and voltage > 0 else charge_kw)
        eta = self._round_trip_eta() ** 0.5
        feed_in = 0.0 if self._export_unpaid(price) else cfg.feed_in_tariff_eur_kwh
        # ~0.1 s on a PC, more on a Raspberry Pi – off the event loop.
        self._battery_plan = await asyncio.to_thread(functools.partial(
            plan_battery,
            now, slots, stored_kwh=bat_kwh_use, capacity_kwh=bat_kwh_use + bat_kwh_free,
            charge_kw=charge_kw, discharge_kw=discharge_kw, eta_charge=eta, eta_discharge=eta,
            feed_in_eur_kwh=feed_in, wear_eur_kwh=cfg.battery_wear_cost_eur_kwh,
            switch_cost_eur=cfg.battery_plan_switch_cost_eur,
            feed_in_limit_kw=cfg.feed_in_limit_w / 1000.0, style=cfg.battery_plan_style,
            last_action=self._MODE_ACTION.get(self._mode, Action.AUTO),
        ))
        self._battery_plan_status = ("in control" if cfg.battery_plan_enabled else "shadow")
        if self._battery_plan.reason:
            self._battery_plan_status = self._battery_plan.reason

    def _plan_step(self, now: datetime):
        """Slot 0 of a fresh plan that is allowed to decide, or None."""
        plan = self._battery_plan
        if not self._cfg.battery_plan_enabled or plan is None or plan.first is None:
            return None
        if (now - plan.computed_at).total_seconds() > self._PLAN_MAX_AGE_SEC:
            return None
        first = plan.first
        return first if first.start <= now < first.end else None

    def _plan_decision(self, surplus_w: float | None, now: datetime) -> ModeDecision | None:
        """A6: the mode for the plan's action in this slot, or None."""
        step = self._plan_step(now)
        if step is None:
            return None
        surplus = surplus_w is not None and surplus_w > self._cfg.pv_surplus_threshold_w
        if step.action is Action.CHARGE and step.grid_charge_kwh > PLAN_STOP_BELOW_KWH:
            return ModeDecision(EMSMode.GRID_CHARGING,
                                f"battery plan: charge {step.grid_charge_kwh:.1f} kWh "
                                f"until {step.end.strftime('%H:%M')}")
        if step.action in (Action.HOLD, Action.CHARGE):
            if surplus:
                return ModeDecision(EMSMode.PV_CHARGING, "battery plan: hold, pv charges")
            return ModeDecision(EMSMode.HOLD_FOR_PEAK, "battery plan: hold for later")
        if step.action is Action.EXPORT and surplus:
            return ModeDecision(EMSMode.EXPORT_SURPLUS, "battery plan: export now, charge later")
        if surplus:
            return ModeDecision(EMSMode.PV_CHARGING, "battery plan: self-use")
        return ModeDecision(EMSMode.IDLE, "battery plan: self-use")

    def _log_plan_shadow(self, now: datetime) -> None:
        """Shadow mode: log once per slot where plan and decision differ."""
        plan = self._battery_plan
        if self._cfg.battery_plan_enabled or plan is None or plan.first is None:
            return
        first = plan.first
        if self._shadow_logged_slot == first.start:
            return
        self._shadow_logged_slot = first.start
        actual = self._MODE_ACTION.get(self._mode, Action.AUTO)
        if actual is not first.action:
            _LOGGER.info(
                "Battery plan (shadow) %s: plan %s%s, decided %s – plan saves %.2f € vs. self-use",
                first.start.strftime("%H:%M"), first.action.value,
                f" {first.grid_charge_kwh:.1f} kWh" if first.grid_charge_kwh else "",
                self._mode.value, plan.savings_eur,
            )

    def _battery_plan_dict(self) -> dict:
        return {
            "enabled": self._cfg.battery_plan_enabled,
            "status": self._battery_plan_status,
            "plan": self._battery_plan.as_dict() if self._battery_plan is not None else None,
        }

    def _export_unpaid(self, price: float | None) -> str:
        """A5: why exporting earns nothing right now, or "" when it does."""
        cfg = self._cfg
        if not cfg.negative_price_enabled:
            return ""
        if price is not None and price <= 0:
            return "negative price"
        if cfg.feed_in_price_entity:
            feed = self._ws.get_state_value(cfg.feed_in_price_entity)
            if feed is not None and feed <= 0:
                return "feed-in not remunerated"
        return ""

    def _negative_price_charge(
        self, price: float | None, bat_kwh_free: float | None, now: datetime
    ) -> bool:
        """A5 (2): grid charge at a price ≤ 0, but not into room the PV needs.

        Without this the battery would be full from a negative night hour
        when the negative midday peak arrives – and then exports into it.
        """
        cfg = self._cfg
        if not cfg.negative_price_enabled or not cfg.battery_control_enabled:
            return False
        if price is None or price > 0 or bat_kwh_free is None:
            return False
        if self._is_stale(cfg.electricity_price_entity, cfg.price_max_age_sec):
            return False
        threshold = (PLAN_STOP_BELOW_KWH if self._mode is EMSMode.GRID_CHARGING
                     else cfg.grid_charge_min_free_kwh)
        if bat_kwh_free <= threshold:
            return False
        remaining = self._forecast_remaining_kwh()
        if remaining is None:
            return True
        pv_for_battery = max(0.0, remaining - (self._remaining_load_kwh or 0.0))
        return bat_kwh_free - pv_for_battery > threshold

    def _update_negative_price_actuators(self, price: float | None, bat_soc: float | None) -> bool:
        """A5 (3, 4): exporting costs money and the battery is full?"""
        cfg = self._cfg
        if not cfg.negative_price_enabled:
            return False
        full = bat_soc is not None and bat_soc >= cfg.battery_max_soc - 1
        return full and bool(self._export_unpaid(price))

    async def _update_energy_value(
        self,
        now: datetime,
        price: float | None,
        pv_w: float | None,
        bat_kwh_use: float | None,
        bat_kwh_free: float | None,
    ) -> None:
        """Refresh the slots, the A3 buy value and the A4 hold verdict.

        Everything stays None / "no hold" whenever an input is missing: the
        gates then fall back to the v3.7.2 behaviour, the hold never starts.
        """
        cfg = self._cfg
        self._value_slots = None
        self._buy_value = None
        self._hold_value = None
        self._hold = (False, "")
        if not (cfg.load_profile_enabled or cfg.soc_aware_gate_enabled
                or cfg.discharge_hold_enabled):
            return
        if self._forecaster is not None:
            await self._forecaster.refresh(now)
        slots, _, _, load_known = self._plan_slots(now, price, pv_w, need_pv=False, need_load=True)
        if slots is None or not load_known:
            return
        # Hot-water plan (live) and EV plan (B2) as load per slot.
        slots = self._with_planned_loads(slots)
        self._value_slots = slots
        if bat_kwh_use is None or bat_kwh_free is None:
            return
        if cfg.soc_aware_gate_enabled:
            self._buy_value = assess_energy(
                slots, bat_kwh_use, bat_kwh_free,
                eta=self._round_trip_eta(), eta_discharge=self._discharge_eta(),
                wear_eur_kwh=cfg.battery_wear_cost_eur_kwh,
                min_margin_eur_kwh=cfg.grid_charge_min_margin_eur_kwh,
                hold=cfg.discharge_hold_enabled, tolerance_eur=self._rate_tolerance(),
            )
        if cfg.discharge_hold_enabled:
            hold, value = should_hold(
                slots, bat_kwh_use, bat_kwh_free, eta_discharge=self._discharge_eta(),
                min_spread_eur_kwh=cfg.discharge_hold_min_spread_eur,
                holding=self._mode is EMSMode.HOLD_FOR_PEAK,
                tolerance_eur=self._rate_tolerance(),
            )
            self._hold_value = value
            if hold and value is not None:
                self._hold = (True, (
                    f"hold for peak: {value.held_for_kwh:.1f} kWh needed later at >= "
                    f"{(value.rate_now + cfg.discharge_hold_min_spread_eur) * 100:.0f} ct, "
                    f"{value.available_kwh:.1f} kWh available"
                ))

    def _failsafe_spec(self) -> dict | None:
        """A10: what the HA integration resets if this add-on goes quiet.

        None when the timeout is off or nothing is live – the integration
        then has nothing to do and drops any copy it held.
        """
        timeout = self._cfg.failsafe_timeout_min
        if timeout <= 0:
            return None
        actions: list[dict] = []
        if self._inverter is not None:
            actions += self._inverter.failsafe_actions()
        if self._hot_water is not None:
            actions += self._hot_water.failsafe_actions()
        actions += self._phase_b_failsafe()
        if not actions:
            return None
        return {"timeout_min": timeout, "actions": actions}

    def _energy_value_dict(self) -> dict | None:
        if self._buy_value is None and self._hold_value is None:
            return None
        return {
            "buy": self._buy_value.as_dict() if self._buy_value is not None else None,
            "hold": self._hold_value.as_dict() if self._hold_value is not None else None,
            "holding": self._hold[0],
        }

    # ------------------------------------------------------------------
    # Überbrückungsplanung (v3.3.0)
    # ------------------------------------------------------------------

    def _bridge_in_control(self) -> bool:
        """Does the bridging plan decide grid charging right now?"""
        return (self._cfg.load_forecast_enabled
                and self._bridge_plan is not None
                and self._forecaster is not None
                and self._forecaster.ready)

    def _bridge_plan_dict(self) -> dict:
        model = self._forecaster.model if self._forecaster is not None else None
        return {
            "enabled": self._cfg.load_forecast_enabled,
            "status": self._bridge_status,
            "in_control": self._bridge_in_control(),
            "min_days": self._cfg.load_forecast_min_days,
            "model": model.as_dict() if model is not None else None,
            "plan": self._bridge_plan.as_dict() if self._bridge_plan is not None else None,
        }

    async def _update_bridge_plan(
        self,
        pv_w: float | None,
        price: float | None,
        bat_kwh_free: float | None,
        bat_kwh_use: float | None,
        now: datetime,
    ) -> None:
        """Recompute the bridging plan. Every missing input → no plan, and
        _should_grid_charge() falls back to the previous logic."""
        self._bridge_plan = None
        if not self._cfg.load_forecast_enabled or self._forecaster is None:
            self._bridge_status = "disabled"
            return
        await self._forecaster.refresh(now)
        if self._forecaster.model is None:
            self._bridge_status = "no load history yet"
            return
        if bat_kwh_free is None or bat_kwh_use is None:
            self._bridge_status = "soc unavailable"
            return
        if price is None:
            self._bridge_status = "price unavailable"
            return
        slots = self._build_plan_slots(now, price, pv_w)
        if slots is None:
            return   # _build_plan_slots set the status
        eta = self._measured_eta() or _DEFAULT_ETA
        self._bridge_plan = plan_bridge(
            slots,
            stored_kwh=bat_kwh_use,
            free_kwh=bat_kwh_free,
            eta=eta,
            min_margin_eur_kwh=self._cfg.grid_charge_min_margin_eur_kwh
            + self._cfg.battery_wear_cost_eur_kwh,
            charge_kw=self._charge_power_kw(),
            tolerance_eur=self._rate_tolerance(),
            check_capacity=self._cfg.day_ahead_enabled,
        )
        self._bridge_status = "active" if self._forecaster.ready else (
            f"learning ({self._forecaster.model.days}/{self._cfg.load_forecast_min_days} days)"
        )

    def _build_plan_slots(
        self, now: datetime, price: float, pv_w: float | None
    ) -> list[PlanSlot] | None:
        """Slots for the bridging plan: PV forecast and load model required.

        With hot-water control live, each slot's load swaps the flat hot-water
        share for the hot-water plan's electricity in that slot.
        """
        slots, status, _, _ = self._plan_slots(now, price, pv_w, need_pv=True, need_load=True)
        if slots is None:
            self._bridge_status = status
            return None
        # Hot-water plan (live) and EV plan (B2) as load per slot.
        slots = self._with_planned_loads(slots)
        return slots

    def _plan_slots(
        self,
        now: datetime,
        price: float | None,
        pv_w: float | None,
        *,
        need_pv: bool,
        need_load: bool,
    ) -> tuple[list[PlanSlot] | None, str, bool, bool]:
        """30-min planning slots → (slots | None, status, pv_known, load_known).

        Without need_pv / need_load a missing PV forecast or load model leaves
        pv_kwh / load_kwh at 0 and reports it via pv_known / load_known.
        """
        cfg = self._cfg
        if price is None:
            return None, "price unavailable", False, False
        curve = self._price_curve()
        if curve is None:
            return None, "no tariff calendar on price entity", False, False

        pv_status = ""
        pv: dict = {}
        if self._solcast is None:
            pv_status = "solcast not configured"
        elif self._solcast_data_stale():
            pv_status = "solcast data stale"
        else:
            pv = self._solcast.pv_slots(cfg.pv_forecast_scenario, cfg.pv_forecast_blend_weight) or {}
            if not pv:
                pv_status = "solcast detailedForecast missing"
        if pv_status and need_pv:
            return None, pv_status, False, False
        pv_known = not pv_status

        load_known = self._forecaster is not None and self._forecaster.model is not None
        if need_load and not load_known:
            return None, "no load history yet", pv_known, False

        pv_items = sorted((s, e, kwh) for s, (e, kwh) in pv.items())

        def pv_between(a: datetime, b: datetime) -> float:
            total = 0.0
            for s, e, kwh in pv_items:
                if e <= a or s >= b:
                    continue
                overlap = (min(b, e) - max(a, s)).total_seconds()
                total += kwh * overlap / (e - s).total_seconds()
            return total

        step = timedelta(minutes=PLAN_SLOT_MIN)
        slot_start = now.replace(second=0, microsecond=0)
        slot_start -= timedelta(minutes=slot_start.minute % PLAN_SLOT_MIN)

        # Nowcasting: scale the next hours by measured / forecast PV now.
        ratio = 1.0
        fc_kw = pv_between(slot_start, slot_start + step) / (step.total_seconds() / 3600.0)
        if (pv_w is not None and fc_kw >= _NOWCAST_MIN_FORECAST_KW
                and not self._is_stale(cfg.pv_power_entity, cfg.sensor_max_age_sec)):
            ratio = max(_NOWCAST_RATIO_MIN, min(_NOWCAST_RATIO_MAX, (pv_w / 1000.0) / fc_kw))

        horizon = now + timedelta(hours=PLAN_HORIZON_H)
        if pv_items:
            horizon = min(horizon, pv_items[-1][1])
        slots: list[PlanSlot] = []
        start = slot_start
        while start < horizon:
            end = start + step
            a = max(start, now)
            if end <= a:
                start = end
                continue
            minutes = (end - a).total_seconds() / 60.0
            load = 0.0
            if load_known:
                temp = self._weather.hourly_temp_at(a) if self._weather is not None else None
                load = self._forecaster.predict_kwh(a, minutes, temp) or 0.0  # type: ignore[union-attr]
                load *= self._load_factor()
            pv_kwh = pv_between(a, end) * self._pv_factor(a)
            ahead_h = (a - now).total_seconds() / 3600.0
            if ahead_h < _NOWCAST_HORIZON_H:
                pv_kwh *= 1.0 + (ratio - 1.0) * (1.0 - ahead_h / _NOWCAST_HORIZON_H)
            if not slots:
                rate = price
            elif cfg.day_ahead_enabled:
                rate = curve.mean_rate(a, end)     # A9: the whole slot, not its start
            else:
                rate = curve.rate_at(a)
            slots.append(PlanSlot(a, end, rate, load, pv_kwh))
            start = end
        if not slots:
            return None, "no planning horizon", pv_known, load_known
        return slots, "", pv_known, load_known

    async def _record_hourly(self, now: datetime, load_w: float | None) -> None:
        """Feed the hourly history (always on – the model needs history
        before the user switches the forecast on)."""
        if self._recorder is None:
            return
        cfg = self._cfg
        if self._is_stale(cfg.load_power_entity, cfg.sensor_max_age_sec):
            load_w = None
        pv_w = self._ws.get_state_value(cfg.pv_power_entity)
        if self._is_stale(cfg.pv_power_entity, cfg.sensor_max_age_sec) and not self._pv_dark(now):
            pv_w = None
        hour = now.replace(minute=0, second=0, microsecond=0)
        if self._recorder.hour != hour or not self._recorder.has_forecasts:
            pv_fc, load_pred = self._hour_forecasts(hour)
        else:
            pv_fc = load_pred = None
        try:
            await self._recorder.record_tick(
                now, load_w, self._heat_pump_power_w(), self._outdoor_temp_c(now), pv_w=pv_w
            )
            if pv_fc is not None or load_pred is not None:
                self._recorder.set_forecasts(pv_fc, load_pred)
        except Exception as exc:   # history is best-effort, never block control
            _LOGGER.warning("Hourly history write failed: %s", exc)

    def _hour_forecasts(self, hour: datetime) -> tuple[float | None, float | None]:
        """A7b/c: uncorrected PV and load forecast for [hour, hour + 1 h)."""
        cfg = self._cfg
        pv_fc = None
        if self._solcast is not None and not self._solcast_data_stale():
            slots = self._solcast.pv_slots(cfg.pv_forecast_scenario, cfg.pv_forecast_blend_weight) or {}
            end = hour + timedelta(hours=1)
            if slots:
                pv_fc = 0.0
                for s, (e, kwh) in slots.items():
                    if e <= hour or s >= end:
                        continue
                    overlap = (min(end, e) - max(hour, s)).total_seconds()
                    pv_fc += kwh * overlap / (e - s).total_seconds()
        load_pred = None
        fc = self._forecaster
        if fc is not None and fc.model is not None:
            temp = self._weather.hourly_temp_at(hour) if self._weather is not None else None
            load_pred = fc.predict_kwh(hour, 60.0, temp)
        return pv_fc, load_pred

    async def _log_write_event(self, wevent: dict) -> None:
        await self._event_log.append(LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            state="write_confirm",
            entry_type="write_confirm",
            battery_kwh_freetochange=0.0,
            battery_kwh_useable=0.0,
            predicted_load_kwh=None,
            write_channel=wevent.get("channel"),
            write_latency_sec=wevent.get("latency_sec"),
            write_outcome=wevent.get("outcome"),
        ))

    async def _update_hot_water(
        self, now: datetime, pv_w: float | None, grid_w: float | None, price: float | None,
        bat_kwh_use: float | None = None, bat_kwh_free: float | None = None,
    ) -> None:
        hw = self._hot_water
        if hw is None or not hw.enabled:
            return
        # The load model is otherwise only (re)fitted by the bridge plan, which
        # runs after this – right after a start the hot-water plan would then
        # see no load forecast, hence no PV surplus and no battery (all "grid").
        if self._forecaster is not None:
            try:
                await self._forecaster.refresh(now)   # memoised: refits at most hourly
            except Exception as exc:
                _LOGGER.warning("Load model refresh for hot water failed: %s", exc)
        slots, _, pv_known, load_known = self._plan_slots(
            now, price, pv_w, need_pv=False, need_load=False
        )
        # The battery covers the house (heat pump included) whether or not
        # miniEMS steers it; grid-charge windows exist only with battery control.
        battery = None
        if bat_kwh_use is not None and bat_kwh_free is not None:
            eta = self._measured_eta()
            # Grid-charge windows the energy plan will buy: a battery kWh used
            # for hot water before them is bought there, not refilled by PV.
            ep = self._energy_plan
            windows = (tuple((w.start, w.end) for w in ep.windows if w.energy_kwh > 0)
                       if ep is not None and self._cfg.battery_control_enabled else ())
            battery = BatteryContext(
                stored_kwh=bat_kwh_use, free_kwh=bat_kwh_free,
                eta=eta or _DEFAULT_ETA,
                grid_charging=self._cfg.battery_control_enabled,
                grid_charge_windows=windows,
            )
        try:
            await hw.update(now, slots, pv_known, load_known, grid_w, price, battery)
        except Exception as exc:   # never let hot water block battery control
            _LOGGER.warning("Hot-water control failed: %s", exc, exc_info=True)

    async def _record_heat_pump(self, now: datetime) -> None:
        """Feed the heat-pump / hot-water history (docs/technical/warmwasser-steuerung.md)."""
        rec = self._hp_recorder
        if rec is None or not rec.enabled:
            return
        cfg = self._cfg

        def value(entity: str) -> float | None:
            return self._ws.get_state_value(entity) if entity else None

        def mode_code(entity: str) -> float | None:
            """Like value(), but resolves an enum-valued sensor's string
            state to its ordinal via the entity's `options` attribute first.

            Needed for lambda_heat_pumps' operating_state sensor: its state
            is the translated string ("STBY", "DHW", ...), not the raw
            Modbus register code value() expects - plain float(raw) always
            failed for it, silently poisoning every tick's mode (get_state_
            value() catches the ValueError and returns None), which in turn
            made _is_heating() return None unconditionally whenever
            heat_pump_mode_entity was configured (regardless of what
            heat_pump_request_type_entity said) - no hot-water-loss segment
            or draw was ever recorded, and every hourly row fell into the
            single UNKNOWN_MODE bucket instead of being split per mode.
            `options`' list order is exactly the register's own enum, so its
            index is the same code the config's hp_mode_hot_water_code
            compares against - confirmed live: DHW sits at index 2, matching
            the default hp_mode_hot_water_code=2.
            """
            numeric = value(entity)
            if numeric is not None or not entity:
                return numeric
            raw = self._ws.get_state_raw(entity)
            if raw is None or raw in ("unavailable", "unknown", ""):
                return None
            options = self._ws.get_state_attribute(entity, "options")
            if isinstance(options, list) and raw in options:
                return float(options.index(raw))
            return None

        try:
            await rec.record_tick(
                now,
                cop=value(cfg.heat_pump_cop_entity),
                mode=mode_code(cfg.heat_pump_mode_entity),
                request_type=value(cfg.heat_pump_request_type_entity),
                boil_temp_c=value(cfg.boil_temp_entity),
                # Measured sensor only – a forecast value would distort COP(outdoor).
                outdoor_temp_c=value(cfg.outdoor_temp_entity),
                heat_pump_w=self._heat_pump_power_w(),
            )
        except Exception as exc:   # history is best-effort, never block control
            _LOGGER.warning("Heat-pump history write failed: %s", exc)
        for ev in rec.pop_events():
            if ev.get("type") == "draw_start" and self._hot_water is not None:
                self._hot_water.note_draw()     # A8: this charge's balance is void
            await self._log_draw_event(ev)

    async def _log_draw_event(self, ev: dict) -> None:
        """Hot-water draw detected / finished → event log (shown as "Hot water")."""
        before = ev["temp_before_c"]
        since = ev["ts"].astimezone().strftime("%H:%M")
        if ev["type"] == "draw_start":
            reason = (f"hot-water draw detected since {since}: {before:.1f} → "
                      f"{ev['temp_now_c']:.1f} °C (−{before - ev['temp_now_c']:.1f} K so far)")
            state = "draw_start"
        else:
            minutes = (ev["last_drop_ts"] - ev["ts"]).total_seconds() / 60.0
            after = ev["temp_after_c"]
            reason = (f"hot-water draw finished: {since}, {before:.1f} → {after:.1f} °C "
                      f"(−{before - after:.1f} K) over {minutes:.0f} min")
            state = "draw_end"
        hw_mode = self._hot_water.mode.value if self._hot_water is not None else ""
        try:
            await self._event_log.append(LogEntry(
                timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                state=state, battery_kwh_freetochange=0.0, battery_kwh_useable=0.0,
                predicted_load_kwh=None, entry_type="hw_decision", mode=hw_mode, reason=reason,
            ))
        except Exception as exc:
            _LOGGER.warning("Draw event log write failed: %s", exc)

    def _heat_pump_power_w(self) -> float | None:
        """Sum of the configured heat-pump power entities; None if any is missing."""
        entities = self._cfg.heat_pump_entities
        if not entities:
            return None
        values = [self._ws.get_state_value(e) for e in entities]
        if any(v is None for v in values):
            return None
        return sum(values)   # type: ignore[arg-type]

    def _outdoor_temp_c(self, now: datetime) -> float | None:
        if self._cfg.outdoor_temp_entity:
            return self._ws.get_state_value(self._cfg.outdoor_temp_entity)
        if self._weather is not None:
            return self._weather.hourly_temp_at(now)
        return None

    def _plan_deadline(self, now: datetime) -> datetime:
        """When the battery must be full again by, for the Energiefahrplan.

        Tomorrow's PV peak (V3a) when available, else tomorrow's dark-window
        end as an absolute datetime – matches the doc's "deadline = V3as
        peak_time_tomorrow (oder, ohne V3a, grid_charge_dark_end_hour)".
        Unlike _next_peak_time() (which picks whichever of today's/
        tomorrow's peak is soonest, for the live grid-charge decision), this
        is always about *tomorrow* specifically – the plan is a tomorrow-
        deficit calculation by definition.
        """
        cfg = self._cfg
        entity = cfg.solcast_peak_time_tomorrow_entity
        if entity and not self._is_stale_daily(entity):
            peak = self._ws.get_state_datetime(entity)
            if peak is not None:
                return peak.astimezone(now.tzinfo)
        tomorrow = now + timedelta(days=1)
        return tomorrow.replace(hour=cfg.grid_charge_dark_end_hour, minute=0, second=0, microsecond=0)

    def _update_energy_plan(self, bat_kwh_free: float | None, now: datetime) -> None:
        """Recompute the Energiefahrplan at most every ENERGY_PLAN_RECOMPUTE_SEC.

        Display-only (see energy_plan.py) – never influences the actual
        charge decision, which stays the tick-level reactive logic above.
        """
        if (self._last_energy_plan_at is not None
                and (now - self._last_energy_plan_at).total_seconds() < ENERGY_PLAN_RECOMPUTE_SEC):
            return
        cfg = self._cfg

        tomorrow_pv = None
        if (self._solcast is not None and cfg.solcast_tomorrow_entity
                and not self._is_stale_daily(cfg.solcast_tomorrow_entity)):
            tomorrow_pv = self._solcast.tomorrow_kwh

        curve = self._price_curve()
        self._energy_plan = compute_energy_plan(
            now=now,
            deadline=self._plan_deadline(now),
            bat_kwh_free=bat_kwh_free,
            predicted_pv_tomorrow_kwh=tomorrow_pv,
            margin_factor=cfg.pv_charge_margin_factor,
            charge_kw=self._charge_power_kw(),
            curve=curve,
            extra_deficit_kwh=(self._hot_water.planned_pv_kwh_before(self._plan_deadline(now))
                               if self._hot_water is not None else 0.0),
            baseline_mean=cfg.day_ahead_enabled,
        )
        self._last_energy_plan_at = now

    def _should_defer_grid_charge(self, bat_kwh_free: float, now: datetime) -> bool:
        """Is postponing this grid charge to a later, equally-cheap window safe?

        False (never defer) whenever the tariff calendar, the deadline, or
        the charge duration cannot be determined – deferring on a guess
        could strand the battery empty; not deferring only ever means
        charging somewhat earlier than strictly necessary.
        """
        curve = self._price_curve()
        if curve is None:
            return False
        deadline = self._next_peak_time(now)
        if deadline is None:
            return False
        charge_kw = self._charge_power_kw()
        if charge_kw is None or charge_kw <= 0:
            return False
        cfg = self._cfg
        t_needed_h = bat_kwh_free / charge_kw
        duration = timedelta(hours=t_needed_h)
        if not (cfg.load_profile_enabled or cfg.day_ahead_enabled):
            return curve.later_window_as_cheap(now, deadline, duration)
        later = curve.later_window_as_cheap_start(now, deadline, duration)
        if later is None:
            return False
        if cfg.day_ahead_enabled:
            # A9: the later cheap hours must be long enough for the whole charge.
            current = curve.rate_at(now)
            if current is None or curve.cheap_hours_between(
                    later, deadline, current + self._rate_tolerance()) < t_needed_h:
                return False
        if cfg.load_profile_enabled:
            # A2: deferring is only free when the battery is not drawn on until
            # then – a charge pushed past the evening is bought in the evening.
            if self._value_slots is None:
                return False
            draw = sum(max(0.0, s.load_kwh - s.pv_kwh) for s in self._value_slots
                       if s.start < later)
            if draw > cfg.grid_charge_min_free_kwh:
                _LOGGER.debug("V2: not deferring – %.1f kWh expected from the battery before %s",
                              draw, later.strftime("%H:%M"))
                return False
        return True

    def _next_peak_time(self, now: datetime) -> datetime | None:
        """The next upcoming Solcast peak-power time – today's if still
        ahead, else tomorrow's. None when neither is usable (same
        is_stale_daily() freshness check as V3a).

        The "deadline" this feeds is deliberately the *next* peak, not
        always tomorrow's: charging that is triggered at 02:00 must still be
        measured against today's own peak, a few hours away, not against a
        peak more than a day out.
        """
        cfg = self._cfg
        for entity in (cfg.solcast_peak_time_today_entity, cfg.solcast_peak_time_tomorrow_entity):
            if not entity or self._is_stale_daily(entity):
                continue
            peak = self._ws.get_state_datetime(entity)
            if peak is None:
                continue
            peak_local = peak.astimezone(now.tzinfo)
            if peak_local > now:
                return peak_local
        return None

    def _forecast_remaining_kwh(self) -> float | None:
        """Remaining PV forecast for today – None when missing, stale or absurd."""
        cfg = self._cfg
        if self._solcast is None or not cfg.solcast_remaining_today_entity:
            return None
        if self._is_stale(cfg.solcast_remaining_today_entity, cfg.forecast_max_age_sec):
            return None
        if self._solcast_data_stale():
            # The sensor is being written every 5 minutes, but out of a cache the
            # API stopped refreshing days ago. Its age check can never see that.
            return None
        value = self._solcast.remaining_today_kwh
        if value is None or value < 0:
            return None
        return value

    def _is_stale(self, entity_id: str, max_age_sec: float) -> bool:
        if not entity_id:
            return True
        return self._ws.is_stale(entity_id, max_age_sec)

    def _inverter_write_status(self) -> str:
        """"ok" | "warning" | "error" for sensor.miniems_inverter_write_status.

        "error" only once a channel has been unconfirmed long enough that the
        inverter demonstrably isn't doing what the app decided – a single
        write that hasn't confirmed yet, or one that just failed and is being
        retried, is "warning": normal, usually self-resolving, not yet worth
        an alarm. Worst case wins.
        """
        if self._inverter is None:
            return "ok"
        if self._inverter.longest_pending_sec > self._cfg.inverter_write_stuck_threshold_sec:
            return "error"
        if self._inverter.write_errors > 0 or self._write_overdue():
            return "warning"
        return "ok"

    def _write_overdue(self) -> bool:
        """A write still unconfirmed after the confirmation timeout.

        A write sent this tick is unconfirmed until the next one by nature;
        before v4.2.4 that alone raised "warning". With V1 retargeting the
        current every tick, the status sat on "warning" for 15 min of every
        half hour although the Deye reported each value within seconds.
        """
        inv = self._inverter
        return (inv is not None and inv.write_unconfirmed > 0
                and inv.longest_pending_sec > INVERTER_WRITE_CONFIRM_TIMEOUT_SEC)

    def _grid_charge_pays(self, price: float) -> bool:
        """Does buying this kWh for the battery actually earn anything?

        A stored kWh is only worth buying when discharging it later displaces a
        more expensive purchase than the round trip cost:

            η × discharge_tariff − price > margin

        Until now nothing checked this. `is_cheap_rate()` compares against a
        configured threshold, which says nothing about whether the spread covers
        the losses – with this installation's numbers the margin is thin (+4.1
        ct/kWh at 91.6% efficiency, −2.2 ct at 85%), so a badly set threshold
        could buy at a loss indefinitely.

        Skips the check – rather than failing closed – when efficiency or
        discharge tariff are not yet known. Both come from accumulated history,
        so failing closed would leave a fresh installation unable to grid charge
        at all until a week of data existed. That would be a regression, and
        this gate is an economic refinement, not a safety interlock.
        """
        legacy = self._grid_charge_pays_legacy(price)
        if not self._cfg.soc_aware_gate_enabled or self._buy_value is None:
            return legacy

        # A3: what the dearest hour the battery cannot already cover is
        # worth. Start only for a real amount, keep going down to the stop
        # threshold – the same hysteresis as the bridging plan.
        threshold = (PLAN_STOP_BELOW_KWH if self._mode is EMSMode.GRID_CHARGING
                     else max(0.5, self._cfg.grid_charge_min_free_kwh / 2.0))
        pays = self._buy_value.buy_kwh > threshold
        verdict = (pays, legacy)
        if pays != legacy and verdict != self._gate_diff:
            v = self._buy_value
            _LOGGER.info(
                "SoC-aware gate: %s (buy %.1f kWh, dearest uncovered %s €/kWh, %.1f kWh "
                "available) – the historical gate would say %s",
                "charge" if pays else "no charge", v.buy_kwh,
                f"{v.first_value_eur_kwh:.4f}" if v.first_value_eur_kwh is not None else "-",
                v.available_kwh, "charge" if legacy else "no charge",
            )
        self._gate_diff = verdict
        return pays

    def _grid_charge_pays_legacy(self, price: float) -> bool:
        """The pre-A3 gate: η × historical discharge tariff − price > margin."""
        tariff = self._discharge_tariff
        eff = self._measured_eta()
        if tariff is None or eff is None:
            return True

        margin = eff * tariff - price
        if margin > self._cfg.grid_charge_min_margin_eur_kwh:
            return True
        _LOGGER.debug(
            "Grid charge skipped – does not pay: %.1f%% × %.4f − %.4f = %+.4f €/kWh "
            "(needs > %.4f)", eff * 100, tariff, price, margin,
            self._cfg.grid_charge_min_margin_eur_kwh,
        )
        return False

    def _solcast_data_age_sec(self) -> float | None:
        """Age of the forecast *data*, from the last-fetch entity's value.

        None when no such entity is configured or it carries no usable
        timestamp – the check then simply does not apply.
        """
        entity = self._cfg.solcast_last_fetch_entity
        if not entity:
            return None
        fetched = self._ws.get_state_datetime(entity)
        if fetched is None:
            return None
        return (datetime.now(timezone.utc) - fetched.astimezone(timezone.utc)).total_seconds()

    def _solcast_data_stale(self) -> bool:
        """True when the forecast data is too old to base decisions on.

        This is the one failure mode neither `unavailable` nor any timestamp
        catches: the Solcast API is unreachable (quota exhausted, no internet)
        while the integration keeps serving its disk cache. Everything looks
        healthy and the numbers are days old.
        """
        age = self._solcast_data_age_sec()
        return age is not None and age > SOLCAST_DATA_MAX_AGE_SEC

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

    def _check_sensor_availability(self) -> None:
        """Log every monitored sensor that drops out or comes back."""
        cfg = self._cfg
        monitored = set(cfg.monitored_entities)
        labels: dict[str, str] = {}
        for f in fields(cfg):
            value = getattr(cfg, f.name)
            if not f.name.endswith("_entity") or not isinstance(value, str):
                continue
            label = f.name[: -len("_entity")].replace("_", " ")
            for entity in (e.strip() for e in value.split(",")):
                if entity in monitored:
                    labels.setdefault(entity, label)
        counted_as_zero = "energy accounting counts 0 W meanwhile"
        impact = {
            e: counted_as_zero
            for e in (cfg.pv_power_entity, cfg.load_power_entity,
                      cfg.grid_power_entity, cfg.battery_power_entity)
            if e
        }
        self._availability.update(
            datetime.now().astimezone(), labels, self._ws.get_state_raw, impact
        )

    def _pv_dark(self, now: datetime | None = None) -> bool:
        """Is it too dark for PV – sun below the horizon, or within
        PV_STALE_SUN_BUFFER_SEC after sunrise / before sunset?

        Read from HA's sun.sun. False when that entity is missing or its
        attributes unparsable, so the staleness warning stays as before.
        """
        state = self._ws.get_state_raw(SUN_ENTITY)
        if state == "below_horizon":
            return True
        if state != "above_horizon":
            return False
        now = now or datetime.now(timezone.utc)

        def ts(attr: str) -> datetime | None:
            raw = self._ws.get_state_attribute(SUN_ENTITY, attr)
            try:
                return datetime.fromisoformat(str(raw)) if raw else None
            except ValueError:
                return None

        setting, rising = ts("next_setting"), ts("next_rising")
        if setting is not None and (setting - now).total_seconds() < PV_STALE_SUN_BUFFER_SEC:
            return True
        # Above the horizon next_rising is tomorrow's – today's sunrise ≈ one day earlier.
        if rising is not None:
            since_rise = (now - (rising - timedelta(days=1))).total_seconds()
            if 0 <= since_rise < PV_STALE_SUN_BUFFER_SEC:
                return True
        return False

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
        pv_dark = self._pv_dark()
        for entity, label, max_age in required:
            if not entity:
                warnings.append(f"Config missing: {label} entity not set")
            elif ws.get_state_value(entity) is None:
                warnings.append(f"Sensor unavailable: {label} ({entity})")
            elif entity == cfg.pv_power_entity and pv_dark:
                continue   # 0 W all night is not a frozen sensor
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

        # Same once-per-day write policy as today/tomorrow above, but the state
        # itself is a timestamp (peak-power time of day), not a number –
        # get_state_value() ends in float(raw) and would call a healthy sensor
        # "unavailable". get_state_datetime() is the presence check that
        # actually parses these; staleness is still by date, same as above.
        for entity, label in ((cfg.solcast_peak_time_today_entity, "Solcast peak time today"),
                              (cfg.solcast_peak_time_tomorrow_entity, "Solcast peak time tomorrow")):
            if not entity:
                continue
            if ws.get_state_datetime(entity) is None:
                warnings.append(f"Sensor unavailable: {label} ({entity})")
            elif self._is_stale_daily(entity):
                warnings.append(
                    f"Sensor stale: {label} – no update today ({entity})"
                )

        # A9: a price list was asked for but the entity carries none – say so
        # instead of silently falling back to the fixed thresholds.
        if cfg.tariff_source == "rates" and cfg.electricity_price_entity and self._price_curve() is None:
            warnings.append(
                f"Tariff: no dated price list on {cfg.electricity_price_entity} "
                "(tariff_source = rates) – deciding with the fixed thresholds"
            )
        if cfg.battery_plan_enabled and self._battery_plan is None:
            warnings.append(f"Battery plan enabled but not computed: {self._battery_plan_status}")
        warnings.extend(self._phase_b_warnings())
        warnings.extend(self._phase_c_warnings())

        # Forecast *data* age – independent of how often HA rewrites the sensors.
        if self._solcast_data_stale():
            age_h = (self._solcast_data_age_sec() or 0) / 3600
            warnings.append(
                f"Solcast data stale: last successful API fetch {age_h:.1f} h ago – "
                "the forecast is being served from cache and may be days old"
            )

        # Inverter service calls that Home Assistant rejected outright
        if self._inverter is not None and self._inverter.write_errors > 0:
            warnings.append(
                f"Inverter control: {self._inverter.write_errors} failed write(s) – "
                "the inverter may not be in the requested state"
            )
        # Inverter service calls HA accepted, but the entity never reflected
        # the new value (retried every tick until it does)
        if self._write_overdue():
            warnings.append(
                f"Inverter control: {self._inverter.write_unconfirmed} unconfirmed write(s) – "
                "HA accepted the command but the inverter has not reported the new value yet"
            )
        # Escalated: a channel has been unconfirmed long enough that it is no
        # longer a normal, self-resolving confirmation cycle – see
        # _inverter_write_status() / inverter_write_stuck_threshold_sec.
        if self._inverter is not None and self._inverter_write_status() == "error":
            channels = ", ".join(self._inverter.stuck_channel_labels)
            warnings.append(
                f"Inverter control: {channels} unconfirmed for "
                f"{self._inverter.longest_pending_sec:.0f}s (> "
                f"{self._cfg.inverter_write_stuck_threshold_sec}s) – "
                "check inverter/Solarman connectivity"
            )

        return warnings
