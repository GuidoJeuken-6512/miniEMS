"""Inverter battery charge/discharge controller.

Calls HA services to set the Deye inverter's operating mode and
charge/discharge power limits based on the current EMS mode.

Simulation mode (battery_control_simulation=True):
  All actions are logged with [SIM] prefix but NOT executed.
  Safe for testing the control logic without touching the inverter.

Idempotent: commands are only sent when the mode actually changes.
"""
import logging
from typing import TYPE_CHECKING, Any

import aiohttp

from const import EMSMode, HA_SERVICES_URL

if TYPE_CHECKING:
    from config_loader import Config

_LOGGER = logging.getLogger(__name__)


class InverterController:
    """Controls the Deye inverter via HA service calls."""

    def __init__(self, config: "Config", supervisor_token: str, long_lived_token: str = "") -> None:
        self._cfg = config
        self._sup_token = supervisor_token
        self._llt = long_lived_token
        self._active_token = supervisor_token
        self._last_mode: EMSMode | None = None
        # Track last set values for idempotency
        self._last_charge_w: int | None = None
        self._last_discharge_w: int | None = None
        # Current limits (reported to dashboard/sensors)
        self.charge_power_limit_w: int = config.battery_max_charge_power_w
        self.discharge_power_limit_w: int = config.battery_max_discharge_power_w

    @property
    def simulation(self) -> bool:
        return self._cfg.battery_control_simulation

    async def apply_mode(self, mode: EMSMode) -> None:
        """Apply inverter settings for the given EMS mode.

        Only sends HA service calls when mode or power limits change.
        """
        if not self._cfg.battery_control_enabled:
            return

        cfg = self._cfg
        sim = self.simulation

        match mode:
            case EMSMode.GRID_CHARGING:
                # Enable grid charge switch + block discharging
                await self._enable_grid_charge(sim)
                await self._set_charge_power(cfg.battery_max_charge_power_w, sim)

            case EMSMode.PV_CHARGING:
                # Disable grid charge; allow full discharge
                await self._disable_grid_charge(sim)
                await self._set_charge_power(cfg.battery_max_charge_power_w, sim)

            case EMSMode.PROTECT_BATTERY:
                # SoC below minimum: disable grid charge, block discharging
                await self._disable_grid_charge(sim)
                await self._set_charge_power(cfg.battery_max_charge_power_w, sim)
                await self._set_discharge_power(0, sim)

            case EMSMode.IDLE:
                # Normal self-use operation
                await self._disable_grid_charge(sim)
                await self._set_charge_power(cfg.battery_max_charge_power_w, sim)

        self._last_mode = mode

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _enable_grid_charge(self, sim: bool) -> None:
        """Turn on grid charge switch and set discharge power to 0."""
        entity = self._cfg.grid_charge_switch_entity
        if entity:
            if sim:
                _LOGGER.info("[SIM] switch.turn_on(%s)", entity)
            else:
                await self._call_service("switch", "turn_on", {"entity_id": entity})
        await self._set_discharge_power(0, sim)

    async def _disable_grid_charge(self, sim: bool) -> None:
        """Turn off grid charge switch and restore default discharge power."""
        entity = self._cfg.grid_charge_switch_entity
        if entity:
            if sim:
                _LOGGER.info("[SIM] switch.turn_off(%s)", entity)
            else:
                await self._call_service("switch", "turn_off", {"entity_id": entity})
        await self._set_discharge_power(self._cfg.default_discharge_power_w, sim)

    async def _set_charge_power(self, value_w: int, sim: bool) -> None:
        if value_w == self._last_charge_w:
            return
        entity = self._cfg.inverter_charge_power_entity
        if not entity:
            return
        if sim:
            _LOGGER.info("[SIM] number.set_value(%s, %d)", entity, value_w)
        else:
            await self._call_service("number", "set_value", {
                "entity_id": entity,
                "value": value_w,
            })
        self._last_charge_w = value_w
        self.charge_power_limit_w = value_w

    async def _set_discharge_power(self, value_w: int, sim: bool) -> None:
        if value_w == self._last_discharge_w:
            return
        entity = self._cfg.battery_discharging_power_entity
        if not entity:
            return
        if sim:
            _LOGGER.info("[SIM] number.set_value(%s, %d)", entity, value_w)
        else:
            await self._call_service("number", "set_value", {
                "entity_id": entity,
                "value": value_w,
            })
        self._last_discharge_w = value_w
        self.discharge_power_limit_w = value_w

    async def _call_service(self, domain: str, service: str, data: dict[str, Any]) -> None:
        url = f"{HA_SERVICES_URL}/{domain}/{service}"
        headers = {"Authorization": f"Bearer {self._active_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 401 and self._active_token == self._sup_token and self._llt:
                        self._active_token = self._llt
                        await self._call_service(domain, service, data)
                    elif resp.status not in (200, 201):
                        _LOGGER.warning("Service call %s.%s failed: HTTP %d", domain, service, resp.status)
                    else:
                        _LOGGER.debug("Service %s.%s OK for %s", domain, service, data.get("entity_id"))
        except Exception as exc:
            _LOGGER.error("Service call error %s.%s: %s", domain, service, exc)
