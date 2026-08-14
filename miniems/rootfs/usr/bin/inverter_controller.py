"""Inverter battery charge/discharge controller.

Calls HA services to set the Deye inverter's grid-charge switch and its
battery charge/discharge limits based on the current EMS mode.

Units: the Deye exposes battery limits as CURRENT in amperes
(number.deye8k_battery_max_charging_current, range 0–350 A). It has no
charge/discharge *power* entity, so everything here is amperes, never watts.

Simulation mode (battery_control_simulation=True):
  All actions are logged with [SIM] prefix but NOT executed.
  Safe for testing the control logic without touching the inverter.

Idempotent per value: a service call is only sent when the *confirmed* value
differs from the desired one.  A failed write clears the confirmed value, so
the next tick retries instead of silently suppressing it.

Two fields are exposed per direction:
  *_current_target_a  – what the controller last wanted to set
  *_current_limit_a   – what Home Assistant actually confirmed (None until confirmed)
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
        # Last CONFIRMED values (None = unknown, forces a write on the next tick)
        self._last_charge_a: int | None = None
        self._last_discharge_a: int | None = None
        self._last_grid_charge: bool | None = None
        # Reported to dashboard/sensors
        self.charge_current_limit_a: int | None = None       # confirmed
        self.discharge_current_limit_a: int | None = None    # confirmed
        self.charge_current_target_a: int | None = None      # intended
        self.discharge_current_target_a: int | None = None   # intended
        self.write_errors: int = 0

    @property
    def simulation(self) -> bool:
        return self._cfg.battery_control_simulation

    async def apply_mode(self, mode: EMSMode) -> None:
        """Apply inverter settings for the given EMS mode.

        Every mode states all three settings explicitly, so the resulting
        inverter state does not depend on which mode preceded it.
        """
        if not self._cfg.battery_control_enabled:
            return

        cfg = self._cfg
        sim = self.simulation

        match mode:
            case EMSMode.GRID_CHARGING:
                # Charge from the grid at the cheap rate; block discharging so
                # the energy just bought is not immediately used up again.
                await self._set_grid_charge(True, sim)
                await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
                await self._set_discharge_current(0, sim)

            case EMSMode.PV_CHARGING:
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
                await self._set_discharge_current(cfg.battery_max_discharge_current_a, sim)

            case EMSMode.EXPORT_SURPLUS:
                # Grid-friendly hold: block charging so the surplus goes to the
                # grid. Discharging stays at full so a passing cloud is covered
                # from the battery instead of by importing from the grid.
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(cfg.export_hold_charge_current_a, sim)
                await self._set_discharge_current(cfg.battery_max_discharge_current_a, sim)

            case EMSMode.PROTECT_BATTERY:
                # SoC below minimum: no grid charge, no discharging.
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
                await self._set_discharge_current(0, sim)

            case EMSMode.IDLE:
                # Normal self-use operation
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
                await self._set_discharge_current(cfg.battery_max_discharge_current_a, sim)

    async def restore_safe_defaults(self) -> None:
        """Leave the inverter in a safe state on shutdown.

        Never leave a restricted charge or discharge limit behind: if the
        add-on stops while a limit is applied, nothing else would ever reset
        it.  Forces the writes through the dedupe.
        """
        if not self._cfg.battery_control_enabled:
            return
        cfg = self._cfg
        sim = self.simulation
        _LOGGER.info("Restoring safe inverter defaults before shutdown")
        self._last_charge_a = None
        self._last_discharge_a = None
        self._last_grid_charge = None
        await self._set_grid_charge(False, sim)
        await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
        await self._set_discharge_current(cfg.battery_max_discharge_current_a, sim)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _set_grid_charge(self, on: bool, sim: bool) -> None:
        """Turn the grid-charge switch on or off (deduped on confirmed state)."""
        entity = self._cfg.grid_charge_switch_entity
        if not entity:
            return
        if self._last_grid_charge is on:
            return

        service = "turn_on" if on else "turn_off"
        if sim:
            _LOGGER.info("[SIM] switch.%s(%s)", service, entity)
            ok = True
        else:
            ok = await self._call_service("switch", service, {"entity_id": entity})

        if ok:
            self._last_grid_charge = on
        else:
            self._last_grid_charge = None   # unknown → retry next tick
            self.write_errors += 1
            _LOGGER.error(
                "Grid charge switch write FAILED (%s → %s) – retrying next tick",
                entity, service,
            )

    async def _set_charge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.inverter_charge_current_entity
        if not entity:
            return
        self.charge_current_target_a = value_a
        if value_a == self._last_charge_a:
            return

        if sim:
            _LOGGER.info("[SIM] number.set_value(%s, %d)", entity, value_a)
            ok = True
        else:
            ok = await self._call_service("number", "set_value", {
                "entity_id": entity,
                "value": value_a,
            })

        if ok:
            self._last_charge_a = value_a
            self.charge_current_limit_a = value_a
        else:
            self._last_charge_a = None       # unknown → retry next tick
            self.charge_current_limit_a = None  # stop reporting it as live
            self.write_errors += 1
            _LOGGER.error(
                "Charge current write FAILED (%s = %d A) – retrying next tick",
                entity, value_a,
            )

    async def _set_discharge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.battery_discharging_current_entity
        if not entity:
            return
        self.discharge_current_target_a = value_a
        if value_a == self._last_discharge_a:
            return

        if sim:
            _LOGGER.info("[SIM] number.set_value(%s, %d)", entity, value_a)
            ok = True
        else:
            ok = await self._call_service("number", "set_value", {
                "entity_id": entity,
                "value": value_a,
            })

        if ok:
            self._last_discharge_a = value_a
            self.discharge_current_limit_a = value_a
        else:
            self._last_discharge_a = None
            self.discharge_current_limit_a = None
            self.write_errors += 1
            _LOGGER.error(
                "Discharge current write FAILED (%s = %d A) – retrying next tick",
                entity, value_a,
            )

    async def _call_service(self, domain: str, service: str, data: dict[str, Any], *, _retry: bool = False) -> bool:
        """Call an HA service. Returns True only when HA accepted the call."""
        url = f"{HA_SERVICES_URL}/{domain}/{service}"
        headers = {"Authorization": f"Bearer {self._active_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 401 and not _retry and self._active_token == self._sup_token and self._llt:
                        self._active_token = self._llt
                        return await self._call_service(domain, service, data, _retry=True)
                    if resp.status not in (200, 201):
                        _LOGGER.warning("Service call %s.%s failed: HTTP %d", domain, service, resp.status)
                        return False
                    _LOGGER.debug("Service %s.%s OK for %s", domain, service, data.get("entity_id"))
                    return True
        except Exception as exc:
            _LOGGER.error("Service call error %s.%s: %s", domain, service, exc)
        return False
