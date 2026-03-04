"""EMS decision logic – determines operating mode and triggers cost accounting."""
import logging
from typing import TYPE_CHECKING

from const import EMSMode

if TYPE_CHECKING:
    from config_loader import Config
    from cost_optimizer import CostOptimizer
    from ha_ws_client import HAWebSocketClient

_LOGGER = logging.getLogger(__name__)


class EMSController:
    """Evaluates sensor readings and determines the current EMS operating mode."""

    def __init__(
        self,
        config: "Config",
        ws_client: "HAWebSocketClient",
        cost_optimizer: "CostOptimizer",
    ) -> None:
        self._cfg = config
        self._ws = ws_client
        self._optimizer = cost_optimizer
        self._mode: EMSMode = EMSMode.IDLE

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def mode(self) -> EMSMode:
        return self._mode

    def update(self) -> dict:
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
        bat_v = ws.get_state_value(cfg.battery_voltage_entity) or 0.0
        price = ws.get_state_value(cfg.electricity_price_entity)

        self._mode = self._determine_mode(pv_w, load_w, bat_soc, price)

        self._optimizer.record_tick(
            grid_power_w=grid_w,
            pv_power_w=pv_w,
            load_power_w=load_w,
            price_eur_kwh=price if price is not None else 0.0,
            interval_sec=cfg.update_interval_sec,
        )

        _LOGGER.info(
            "EMS [%s] PV=%.0fW Load=%.0fW Grid=%.0fW SoC=%s%% Price=%s€/kWh",
            self._mode.value,
            pv_w,
            load_w,
            grid_w,
            f"{bat_soc:.0f}" if bat_soc is not None else "?",
            f"{price:.4f}" if price is not None else "?",
        )

        return {
            "mode": self._mode.value,
            "pv_power_w": pv_w,
            "load_power_w": load_w,
            "grid_power_w": grid_w,
            "battery_soc_pct": bat_soc,
            "battery_power_w": bat_w,
            "battery_voltage_v": bat_v,
            "electricity_price_eur": price,
            "is_cheap_rate": self._optimizer.is_cheap_rate(price),
            **self._optimizer.summary(),
        }

    # ------------------------------------------------------------------
    # Decision logic
    # ------------------------------------------------------------------

    def _determine_mode(
        self,
        pv_w: float,
        load_w: float,
        bat_soc: float | None,
        price: float | None,
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

        # Cheap grid rate → charge battery from grid
        if self._optimizer.is_cheap_rate(price) and soc_ok:
            return EMSMode.GRID_CHARGING

        return EMSMode.IDLE
