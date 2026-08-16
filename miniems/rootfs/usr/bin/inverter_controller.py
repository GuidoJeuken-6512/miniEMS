"""Inverter battery charge/discharge controller.

Calls HA services to set the Deye inverter's grid-charge switch and its
battery charge/discharge limits based on the current EMS mode.

Units: the Deye exposes battery limits as CURRENT in amperes
(number.deye8k_battery_max_charging_current, range 0–350 A). It has no
charge/discharge *power* entity, so everything here is amperes, never watts.

Simulation mode (battery_control_simulation=True):
  All actions are logged with [SIM] prefix but NOT executed.
  Safe for testing the control logic without touching the inverter.

Write confirmation: an HTTP 200 from HA only means the service call was
accepted, not that the inverter applied it – some Deye/Solarman bridges only
reflect a written number/switch on their next poll, which can lag by many
minutes, and a call can also silently target a since-renamed entity and do
nothing at all. Every write is therefore re-checked against the *real* state
HAWebSocketClient already caches, every tick, and re-sent until it matches
(see INVERTER_WRITE_CONFIRM_TIMEOUT_SEC in const.py – one EMS tick, so a
stuck write is retried continuously rather than assumed done).

Two fields are exposed per direction:
  *_current_target_a  – what the controller last wanted to set
  *_current_limit_a   – what HA's live state confirms (None until confirmed)
"""
import asyncio
import logging
import time
from typing import TYPE_CHECKING, Any

import aiohttp

from const import (
    EMSMode,
    HA_SERVICES_URL,
    INVERTER_SERVICE_CALL_TIMEOUT_SEC,
    INVERTER_WRITE_CONFIRM_TIMEOUT_SEC,
)

if TYPE_CHECKING:
    from config_loader import Config
    from ha_ws_client import HAWebSocketClient

_LOGGER = logging.getLogger(__name__)


class _WriteChannel:
    """Tracks one inverter write's target and confirmation state."""
    __slots__ = ("target", "sent_at", "confirmed")

    def __init__(self) -> None:
        self.target: Any = None
        self.sent_at: float | None = None   # time.monotonic() of the last send
        self.confirmed: bool = True         # nothing pending yet


class InverterController:
    """Controls the Deye inverter via HA service calls."""

    def __init__(
        self,
        config: "Config",
        supervisor_token: str,
        long_lived_token: str = "",
        ws_client: "HAWebSocketClient | None" = None,
    ) -> None:
        self._cfg = config
        self._sup_token = supervisor_token
        self._llt = long_lived_token
        self._active_token = supervisor_token
        self._ws = ws_client
        self._charge_ch = _WriteChannel()
        self._discharge_ch = _WriteChannel()
        self._grid_ch = _WriteChannel()
        # Reported to dashboard/sensors
        self.charge_current_limit_a: int | None = None       # confirmed
        self.discharge_current_limit_a: int | None = None    # confirmed
        self.charge_current_target_a: int | None = None      # intended
        self.discharge_current_target_a: int | None = None   # intended
        self.write_errors: int = 0

    @property
    def simulation(self) -> bool:
        return self._cfg.battery_control_simulation

    @property
    def write_unconfirmed(self) -> int:
        """How many of the three controls (charge/discharge/grid-switch) are
        currently pending confirmation. Falls back to 0 once all match."""
        return sum(
            0 if ch.confirmed else 1
            for ch in (self._charge_ch, self._discharge_ch, self._grid_ch)
        )

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
        it. Resetting the write channels forces fresh sends regardless of
        whatever target/confirmation state they were last left in.
        """
        if not self._cfg.battery_control_enabled:
            return
        cfg = self._cfg
        sim = self.simulation
        _LOGGER.info("Restoring safe inverter defaults before shutdown")
        self._charge_ch = _WriteChannel()
        self._discharge_ch = _WriteChannel()
        self._grid_ch = _WriteChannel()
        await self._set_grid_charge(False, sim)
        await self._set_charge_current(cfg.battery_max_charge_current_a, sim)
        await self._set_discharge_current(cfg.battery_max_discharge_current_a, sim)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _set_grid_charge(self, on: bool, sim: bool) -> None:
        """Turn the grid-charge switch on or off, confirmed against the real state."""
        entity = self._cfg.grid_charge_switch_entity
        if not entity:
            return
        raw = self._ws.state_cache.get(entity, {}).get("state") if self._ws else None
        matched = raw == ("on" if on else "off")

        await self._write_confirmed(
            self._grid_ch, on, matched, sim,
            "switch", "turn_on" if on else "turn_off", {"entity_id": entity},
            f"Grid charge switch ({entity})",
        )

    async def _set_charge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.inverter_charge_current_entity
        if not entity:
            return
        self.charge_current_target_a = value_a
        actual = self._ws.get_state_value(entity) if self._ws else None
        matched = actual is not None and abs(actual - value_a) < 0.5

        await self._write_confirmed(
            self._charge_ch, value_a, matched, sim,
            "number", "set_value", {"entity_id": entity, "value": value_a},
            f"Charge current ({entity})",
        )
        self.charge_current_limit_a = value_a if self._charge_ch.confirmed else None

    async def _set_discharge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.battery_discharging_current_entity
        if not entity:
            return
        self.discharge_current_target_a = value_a
        actual = self._ws.get_state_value(entity) if self._ws else None
        matched = actual is not None and abs(actual - value_a) < 0.5

        await self._write_confirmed(
            self._discharge_ch, value_a, matched, sim,
            "number", "set_value", {"entity_id": entity, "value": value_a},
            f"Discharge current ({entity})",
        )
        self.discharge_current_limit_a = value_a if self._discharge_ch.confirmed else None

    async def _write_confirmed(
        self,
        ch: _WriteChannel,
        target: Any,
        matched: bool,
        sim: bool,
        domain: str,
        service: str,
        data: dict[str, Any],
        label: str,
    ) -> None:
        """Send `domain.service(data)` for `target`, deduped on the target.

        `matched` is the caller's fresh comparison of the *real* HA state
        against `target`. HTTP success alone never marks a write confirmed –
        only `matched` does. While unconfirmed, the call is re-sent every
        INVERTER_WRITE_CONFIRM_TIMEOUT_SEC (one EMS tick), so a write that HA
        silently swallowed or that a slow bridge hasn't applied yet keeps
        being retried instead of being assumed done after a single 200.
        """
        now = time.monotonic()

        if ch.target != target:
            ch.target = target
            ch.confirmed = False
            ch.sent_at = None

        if ch.confirmed:
            return

        if matched:
            ch.confirmed = True
            ch.sent_at = None
            return

        if ch.sent_at is not None and (now - ch.sent_at) < INVERTER_WRITE_CONFIRM_TIMEOUT_SEC:
            return   # write in flight – give this cycle a chance to land

        if sim:
            # Nothing is actually sent to the inverter, so there is nothing
            # for the real HA state to ever confirm – treat it as done right
            # away (matches pre-v2.0.1 behaviour: log once per target change).
            _LOGGER.info("[SIM] %s.%s(%s)", domain, service, data)
            ch.confirmed = True
            ch.sent_at = None
            return

        ok = await self._call_service(domain, service, data)
        ch.sent_at = now
        if ok is False:
            # HA actively rejected the call (non-2xx) – that is a real failure.
            self.write_errors += 1
            _LOGGER.error("%s write FAILED (target=%s) – retrying next tick", label, target)
        elif ok is None:
            # No verdict: the request timed out. HA very likely applied it
            # anyway (the Modbus round-trip just outlasts our HTTP timeout),
            # so counting this as an error would raise a false alarm on every
            # single write. The entity-state check next tick decides.
            _LOGGER.info(
                "%s write timed out after %ds (target=%s) – HA may well have applied it; "
                "confirming against the entity state next tick",
                label, INVERTER_SERVICE_CALL_TIMEOUT_SEC, target,
            )
        else:
            _LOGGER.debug("%s write sent (target=%s), awaiting confirmation", label, target)

    async def _call_service(
        self, domain: str, service: str, data: dict[str, Any], *, _retry: bool = False
    ) -> bool | None:
        """Call an HA service.

        Returns True when HA accepted the call, False when it actively rejected
        it, and None when the outcome is unknown (timeout / transport error).
        None is deliberately distinct from False: a write whose response never
        arrived has very likely still been applied, and the caller confirms it
        against the real entity state rather than guessing from the HTTP result.
        """
        url = f"{HA_SERVICES_URL}/{domain}/{service}"
        headers = {"Authorization": f"Bearer {self._active_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=INVERTER_SERVICE_CALL_TIMEOUT_SEC),
                ) as resp:
                    if resp.status == 401 and not _retry and self._active_token == self._sup_token and self._llt:
                        self._active_token = self._llt
                        return await self._call_service(domain, service, data, _retry=True)
                    if resp.status not in (200, 201):
                        _LOGGER.warning("Service call %s.%s failed: HTTP %d", domain, service, resp.status)
                        return False
                    _LOGGER.debug("Service %s.%s OK for %s", domain, service, data.get("entity_id"))
                    return True
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
            # str() on a TimeoutError is empty, which used to produce a log line
            # ending in a bare colon and gave no hint what had gone wrong.
            return None
        except Exception as exc:
            _LOGGER.error(
                "Service call error %s.%s [%s]: %s", domain, service, type(exc).__name__, exc
            )
            return None
