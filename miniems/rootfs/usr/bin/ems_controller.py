"""EMS decision logic – determines operating mode and triggers cost accounting."""
import logging
from datetime import datetime
from typing import TYPE_CHECKING

from battery_model import BatteryModel
from const import EMSMode
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

        pv_w = ws.get_state_value(cfg.pv_power_entity) or 0.0
        load_w = ws.get_state_value(cfg.load_power_entity) or 0.0
        grid_w = ws.get_state_value(cfg.grid_power_entity) or 0.0
        bat_soc = ws.get_state_value(cfg.battery_soc_entity)
        bat_w = ws.get_state_value(cfg.battery_power_entity) or 0.0
        price = ws.get_state_value(cfg.electricity_price_entity)
        outdoor_temp = None  # temperature now comes from weather.get_forecasts

        # Compute battery kWh values
        bat_kwh_free = self._battery_model.free_to_charge_kwh(bat_soc) if bat_soc is not None else None
        bat_kwh_use  = self._battery_model.useable_kwh(bat_soc) if bat_soc is not None else None

        # Update prediction before mode decision so it influences GRID_CHARGING
        if self._model:
            self._prediction = await self._model.predict(bat_soc)

        prev_mode = self._mode
        self._mode = self._determine_mode(pv_w, load_w, bat_soc, price, bat_kwh_free)

        # Log mode changes to event log
        if self._mode != prev_mode:
            entry = LogEntry(
                timestamp=datetime.now().isoformat(timespec="seconds"),
                state="on" if self._mode == EMSMode.GRID_CHARGING else "off",
                battery_kwh_freetochange=round(bat_kwh_free, 3) if bat_kwh_free is not None else 0.0,
                battery_kwh_useable=round(bat_kwh_use, 3) if bat_kwh_use is not None else 0.0,
                predicted_load_kwh=(
                    self._prediction.predicted_load_kwh if self._prediction else None
                ),
            )
            self._event_log.append(entry)

        # Apply inverter control (simulation or real)
        if self._inverter:
            await self._inverter.apply_mode(self._mode)

        self._optimizer.record_tick(
            grid_power_w=grid_w,
            pv_power_w=pv_w,
            load_power_w=load_w,
            battery_power_w=bat_w,
            price_eur_kwh=price if price is not None else 0.0,
            interval_sec=cfg.update_interval_sec,
            outdoor_temp_c=outdoor_temp,
        )
        await self._optimizer.flush_to_db()

        sim_label = ""
        if self._inverter and self._inverter.simulation and cfg.battery_control_enabled:
            sim_label = " [SIM]"

        _LOGGER.info(
            "EMS [%s%s] PV=%.0fW Load=%.0fW Grid=%.0fW SoC=%s%% Price=%s€/kWh",
            self._mode.value,
            sim_label,
            pv_w,
            load_w,
            grid_w,
            f"{bat_soc:.0f}" if bat_soc is not None else "?",
            f"{price:.4f}" if price is not None else "?",
        )

        summary = await self._optimizer.summary_with_db()

        # Build warnings list
        warnings = list(self._optimizer.get_startup_warnings())
        warnings.extend(self._build_sensor_warnings())

        result = {
            "mode": self._mode.value,
            "pv_power_w": pv_w,
            "load_power_w": load_w,
            "grid_power_w": grid_w,
            "battery_soc_pct": bat_soc,
            "battery_power_w": bat_w,
            "electricity_price_eur": price,
            "is_cheap_rate": self._optimizer.is_cheap_rate(price),
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
        result["today_cost_without_grid_charge"] = round(max(0.0, grid_cost - gc_cost), 6)
        result["today_cost_fix_price_tarif"] = round(load_kwh * cfg.fix_price, 6)

        # Solcast values
        if self._solcast:
            result["solcast_remaining_today_kwh"] = self._solcast.remaining_today_kwh
            result["solcast_today_kwh"] = self._solcast.today_kwh
            result["solcast_tomorrow_kwh"] = self._solcast.tomorrow_kwh

        if self._inverter:
            result["charge_power_limit_w"] = self._inverter.charge_power_limit_w
            result["discharge_power_limit_w"] = self._inverter.discharge_power_limit_w
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
        pv_w: float,
        load_w: float,
        bat_soc: float | None,
        price: float | None,
        bat_kwh_free: float | None = None,
    ) -> EMSMode:
        cfg = self._cfg

        # Battery protection always takes priority
        if bat_soc is not None and bat_soc < cfg.battery_min_soc:
            return EMSMode.PROTECT_BATTERY

        soc_ok = bat_soc is None or bat_soc < cfg.battery_max_soc

        # PV surplus → charge battery from PV
        surplus_w = pv_w - load_w
        if surplus_w > cfg.pv_surplus_threshold_w and soc_ok:
            return EMSMode.PV_CHARGING

        # Cheap grid rate → grid charge if battery has more room than expected PV
        if self._optimizer.is_cheap_rate(price) and soc_ok:
            solcast_remaining = (
                self._solcast.remaining_today_kwh
                if self._solcast is not None
                else None
            )
            if solcast_remaining is not None and bat_kwh_free is not None:
                # Plan logic: grid-charge only when battery has more room than PV will deliver
                if bat_kwh_free > solcast_remaining:
                    return EMSMode.GRID_CHARGING
            else:
                # Fallback: use prediction-based logic when Solcast unavailable
                pred = self._prediction
                if pred is None or pred.confidence == "none" or pred.should_grid_charge:
                    return EMSMode.GRID_CHARGING

        return EMSMode.IDLE

    # ------------------------------------------------------------------
    # Warnings
    # ------------------------------------------------------------------

    def _build_sensor_warnings(self) -> list[str]:
        """Return warnings for any required sensors that are unavailable."""
        ws = self._ws
        cfg = self._cfg
        warnings = []

        required = [
            (cfg.pv_power_entity, "PV power"),
            (cfg.battery_soc_entity, "Battery SoC"),
            (cfg.battery_power_entity, "Battery power"),
            (cfg.grid_power_entity, "Grid power"),
            (cfg.load_power_entity, "Load power"),
            (cfg.electricity_price_entity, "Electricity price"),
        ]
        if cfg.solcast_remaining_today_entity:
            required.append((cfg.solcast_remaining_today_entity, "Solcast remaining today"))
        if cfg.solcast_today_entity:
            required.append((cfg.solcast_today_entity, "Solcast today"))

        for entity, label in required:
            if not entity:
                warnings.append(f"Config missing: {label} entity not set")
            elif ws.get_state_value(entity) is None:
                warnings.append(f"Sensor unavailable: {label} ({entity})")

        return warnings
