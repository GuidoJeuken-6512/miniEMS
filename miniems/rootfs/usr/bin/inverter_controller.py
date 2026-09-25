"""Inverter battery charge/discharge controller.

Calls HA services to set the Deye inverter's grid-charge switch and its
battery charge/discharge limits based on the current EMS mode.

Units: the Deye exposes battery limits as CURRENT in amperes
(number.deye8k_battery_max_charging_current, range 0–350 A). It has no
charge/discharge *power* entity. Since v2.4.0 the GRID_CHARGING charge
target arrives as Watts from EMSController (V1 "Ladeleistung strecken") and
is converted to amps here, at the actuator boundary – see
device_control.py and docs/roadmap/v3.0-geraeteprofile.md, "Die
Watt-Abstraktion". Discharge, and every other mode's charge current, are
still set directly in amps (always either the configured max or 0/blocked
– never a computed intermediate value, so there is nothing to convert).

Simulation mode (battery_control_simulation=True):
  All actions are logged with [SIM] prefix but NOT executed.
  Safe for testing the control logic without touching the inverter.

Write confirmation: an HTTP 200 from HA only means the service call was
accepted, not that the inverter applied it – some Deye/Solarman bridges only
reflect a written number/switch on their next poll, which can lag by many
minutes, and a call can also silently target a since-renamed entity and do
nothing at all. Every write is therefore re-checked against the *real* state
HAStateClient already caches, every tick, and re-sent until it matches
(see INVERTER_WRITE_CONFIRM_TIMEOUT_SEC in const.py – one EMS tick, so a
stuck write is retried continuously rather than assumed done).

Two fields are exposed per direction:
  *_current_target_a  – what the controller last wanted to set
  *_current_limit_a   – what HA's live state confirms (None until confirmed)
"""
import asyncio
import logging
import time
from collections import deque
from typing import TYPE_CHECKING, Any

import aiohttp

from const import (
    EMSMode,
    HA_SERVICES_URL,
    INVERTER_SERVICE_CALL_TIMEOUT_SEC,
    INVERTER_WRITE_CONFIRM_TIMEOUT_SEC,
    INVERTER_WRITE_ERROR_WINDOW_SEC,
)
from device_control import ActuatorLimits, LiveLimitsCache, clamp_to_limits, to_actuator_value
from device_profile import ActuatorControl
from write_channel import WriteChannel, WriteChannelSet, WriteSpec, matches

if TYPE_CHECKING:
    from config_loader import Config
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)


class InverterController:
    """Controls the Deye inverter via HA service calls."""

    def __init__(
        self,
        config: "Config",
        supervisor_token: str,
        long_lived_token: str = "",
        ws_client: "HAStateClient | None" = None,
    ) -> None:
        self._cfg = config
        self._sup_token = supervisor_token
        self._llt = long_lived_token
        self._active_token = supervisor_token
        self._ws = ws_client
        self._channels = WriteChannelSet(["charge", "discharge", "grid"])
        # docs/roadmap/v3.0-geraeteprofile.md, "Die Watt-Abstraktion": tracks
        # each actuator's live max across ticks, so a momentarily unavailable
        # attribute read never silently drops the ceiling – see
        # device_control.LiveLimitsCache. Charge and discharge are tracked
        # independently (a BMS may impose different limits per direction).
        self._charge_limits_cache = LiveLimitsCache()
        self._discharge_limits_cache = LiveLimitsCache()
        # Reported to dashboard/sensors
        self.charge_current_limit_a: int | None = None       # confirmed
        self.discharge_current_limit_a: int | None = None    # confirmed
        self.charge_current_target_a: int | None = None      # intended
        self.discharge_current_target_a: int | None = None   # intended
        # Monotonic timestamps of real (HTTP-rejected) write failures, pruned
        # to INVERTER_WRITE_ERROR_WINDOW_SEC on every read – see write_errors.
        self._write_error_times: deque[float] = deque()
        # Write-confirm lifecycle events (confirmed/failed/unconfirmed_at_shutdown)
        # since the last pop_write_events() call – see that method.
        self._pending_events: list[dict[str, Any]] = []

    @property
    def simulation(self) -> bool:
        return self._cfg.battery_control_simulation

    @property
    def write_errors(self) -> int:
        """Count of real write failures within the last
        INVERTER_WRITE_ERROR_WINDOW_SEC, not a lifetime total. A lifetime
        counter never clears once any failure has occurred since the last
        add-on restart, so the warning banner would keep alarming about
        failures that resolved hours or days ago, indistinguishable from an
        ongoing problem.
        """
        cutoff = time.monotonic() - INVERTER_WRITE_ERROR_WINDOW_SEC
        while self._write_error_times and self._write_error_times[0] < cutoff:
            self._write_error_times.popleft()
        return len(self._write_error_times)

    @property
    def write_unconfirmed(self) -> int:
        """How many of the three controls (charge/discharge/grid-switch) are
        currently pending confirmation. Falls back to 0 once all match."""
        return self._channels.unconfirmed_count

    @property
    def longest_pending_sec(self) -> float:
        """Seconds the longest-unconfirmed channel has been continuously
        unconfirmed right now. 0 when all three are confirmed.

        This is what separates a normal, self-resolving confirmation cycle
        (any Solarman bridge can legitimately take a while) from a channel
        that has genuinely diverged from what the app wants for a long time –
        see inverter_write_status / INVERTER_WRITE_STUCK_THRESHOLD_SEC.
        """
        return self._channels.longest_pending_sec

    @property
    def stuck_channel_labels(self) -> list[str]:
        """Labels of currently-unconfirmed channels, longest-pending first."""
        return self._channels.stuck_channel_labels

    def pop_write_events(self) -> list[dict[str, Any]]:
        """Return and clear write-confirm lifecycle events accumulated since
        the last call – one entry per confirmation, real failure, or channel
        still unconfirmed at shutdown.

        The add-on's own log buffer is far too short-lived (minutes) to
        analyse real confirmation latency over days; the caller persists
        these into the durable event_log table instead.
        """
        events, self._pending_events = self._pending_events, []
        return events

    async def apply_mode(self, mode: EMSMode, grid_charge_power_w: float | None = None) -> None:
        """Apply inverter settings for the given EMS mode.

        Every mode states all three settings explicitly, so the resulting
        inverter state does not depend on which mode preceded it.

        `grid_charge_power_w` overrides the GRID_CHARGING charge power, in
        WATTS (EMSController._grid_charge_power_w() – V1 "Ladeleistung
        strecken"; docs/roadmap/v3.0-geraeteprofile.md, "Die Watt-Abstraktion").
        `None` (all other modes, and any caller that doesn't pass it) keeps
        the original full-current behaviour – see
        `_resolve_grid_charge_current_a()` for exactly how "no target" and a
        live/cached actuator ceiling interact.
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
                charge_a = self._resolve_grid_charge_current_a(grid_charge_power_w)
                await self._set_charge_current(charge_a, sim)
                await self._set_discharge_current(self._resolve_discharge_current_a(0), sim)

            case EMSMode.PV_CHARGING:
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(
                    self._resolve_charge_current_a(cfg.battery_max_charge_current_a), sim)
                await self._set_discharge_current(
                    self._resolve_discharge_current_a(cfg.battery_max_discharge_current_a), sim)

            case EMSMode.EXPORT_SURPLUS:
                # Grid-friendly hold: block charging so the surplus goes to the
                # grid. Discharging stays at full so a passing cloud is covered
                # from the battery instead of by importing from the grid.
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(
                    self._resolve_charge_current_a(cfg.export_hold_charge_current_a), sim)
                await self._set_discharge_current(
                    self._resolve_discharge_current_a(cfg.battery_max_discharge_current_a), sim)

            case EMSMode.PROTECT_BATTERY:
                # SoC below minimum: no grid charge, no discharging.
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(
                    self._resolve_charge_current_a(cfg.battery_max_charge_current_a), sim)
                await self._set_discharge_current(self._resolve_discharge_current_a(0), sim)

            case EMSMode.IDLE:
                # Normal self-use operation
                await self._set_grid_charge(False, sim)
                await self._set_charge_current(
                    self._resolve_charge_current_a(cfg.battery_max_charge_current_a), sim)
                await self._set_discharge_current(
                    self._resolve_discharge_current_a(cfg.battery_max_discharge_current_a), sim)

    def _resolve_grid_charge_current_a(self, grid_charge_power_w: float | None) -> int:
        """V1: the Watt target from EMSController._grid_charge_power_w(),
        converted to the charge-current entity's native amps – the clamp to
        live min/max/step and the configured cap is then
        `_resolve_charge_current_a()`'s job, same as every other mode's
        charge target.

        `battery_voltage_entity` is read here – nowhere else in the control
        path – per docs/roadmap/v3.0-geraeteprofile.md, "Die
        Watt-Abstraktion": if it's unavailable, `to_actuator_value()` falls
        back to a fixed assumed voltage (48 V, a mid-range LFP pack voltage)
        rather than blocking the write.

        `None` (V1 couldn't compute a precise target – no price calendar, no
        window, or bat_kwh_free is zero/unknown) resolves to the actuator's
        own configured max, exactly like the pre-v2.4.0 amp-based fallback
        to `battery_max_charge_current_a` did.
        """
        cfg = self._cfg
        if grid_charge_power_w is None:
            return self._resolve_charge_current_a(cfg.battery_max_charge_current_a)

        control = ActuatorControl(
            quantity="power", domain="number", actuator_unit="A", via="dc_current",
            voltage_fallback_v=48.0,
        )
        voltage = (
            self._ws.get_state_value(cfg.battery_voltage_entity)
            if self._ws and cfg.battery_voltage_entity else None
        )
        raw_a = to_actuator_value(grid_charge_power_w, control, voltage_v=voltage)
        return self._resolve_charge_current_a(round(raw_a))

    def _resolve_charge_current_a(self, requested_a: int) -> int:
        """Clamp a charge-current target (already in amps – every mode's
        `charge` target is either the configured max, an explicit hold
        value, 0, or – only for GRID_CHARGING – a Watts-derived value
        already converted by `_resolve_grid_charge_current_a()`) to the
        charge-current entity's live min/max/step.

        `battery_max_charge_current_a` remains a hard outer ceiling
        regardless of what the entity's live `max` attribute reports – a
        live max can only ever *tighten* the effective limit (e.g. a
        BMS-imposed cap below the configured value), never loosen it beyond
        what the user configured.
        """
        cfg = self._cfg
        configured_max = cfg.battery_max_charge_current_a
        entity = cfg.inverter_charge_current_entity
        control = ActuatorControl(fallback_limits={"min": 0, "max": configured_max, "step": 1})

        live_min = self._ws.get_state_attribute(entity, "min") if self._ws and entity else None
        live_max = self._ws.get_state_attribute(entity, "max") if self._ws and entity else None
        live_step = self._ws.get_state_attribute(entity, "step") if self._ws and entity else None
        limits = self._charge_limits_cache.resolve(entity, live_min, live_max, live_step, control)
        limits = ActuatorLimits(min=limits.min, max=min(limits.max, configured_max), step=limits.step)

        return int(clamp_to_limits(requested_a, limits))

    def _resolve_discharge_current_a(self, requested_a: int) -> int:
        """The discharge-side equivalent of `_resolve_charge_current_a()` –
        same live min/max/step clamping, same "configured value is a hard
        outer ceiling" rule, its own `LiveLimitsCache` instance (a discharge
        BMS limit is tracked independently of the charge one)."""
        cfg = self._cfg
        configured_max = cfg.battery_max_discharge_current_a
        entity = cfg.battery_discharging_current_entity
        control = ActuatorControl(fallback_limits={"min": 0, "max": configured_max, "step": 1})

        live_min = self._ws.get_state_attribute(entity, "min") if self._ws and entity else None
        live_max = self._ws.get_state_attribute(entity, "max") if self._ws and entity else None
        live_step = self._ws.get_state_attribute(entity, "step") if self._ws and entity else None
        limits = self._discharge_limits_cache.resolve(entity, live_min, live_max, live_step, control)
        limits = ActuatorLimits(min=limits.min, max=min(limits.max, configured_max), step=limits.step)

        return int(clamp_to_limits(requested_a, limits))

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

        now = time.monotonic()
        for ch in self._channels:
            if not ch.confirmed and ch.pending_since is not None:
                # The most interesting case (a write that never confirmed at
                # all) would otherwise vanish silently on restart – the
                # per-channel state below is about to be thrown away.
                self._pending_events.append({
                    "channel": ch.label,
                    "target": ch.target,
                    "outcome": "unconfirmed_at_shutdown",
                    "latency_sec": round(now - ch.pending_since, 1),
                })

        self._channels.reset()
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
        expected = "on" if on else "off"
        spec = WriteSpec(
            domain="switch", service="turn_on" if on else "turn_off",
            entity_id=entity, payload={"entity_id": entity}, expected=expected,
        )

        await self._write_confirmed(
            self._channels["grid"], spec, matches(raw, spec), sim,
            f"Grid charge switch ({entity})",
        )

    async def _set_charge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.inverter_charge_current_entity
        if not entity:
            return
        self.charge_current_target_a = value_a
        actual = self._ws.get_state_value(entity) if self._ws else None
        spec = WriteSpec(
            domain="number", service="set_value",
            entity_id=entity, payload={"entity_id": entity, "value": value_a}, expected=value_a,
        )

        await self._write_confirmed(
            self._channels["charge"], spec, matches(actual, spec), sim,
            f"Charge current ({entity})",
        )
        self.charge_current_limit_a = value_a if self._channels["charge"].confirmed else None

    async def _set_discharge_current(self, value_a: int, sim: bool) -> None:
        entity = self._cfg.battery_discharging_current_entity
        if not entity:
            return
        self.discharge_current_target_a = value_a
        actual = self._ws.get_state_value(entity) if self._ws else None
        spec = WriteSpec(
            domain="number", service="set_value",
            entity_id=entity, payload={"entity_id": entity, "value": value_a}, expected=value_a,
        )

        await self._write_confirmed(
            self._channels["discharge"], spec, matches(actual, spec), sim,
            f"Discharge current ({entity})",
        )
        self.discharge_current_limit_a = value_a if self._channels["discharge"].confirmed else None

    async def _write_confirmed(
        self,
        ch: WriteChannel,
        spec: WriteSpec,
        matched: bool,
        sim: bool,
        label: str,
    ) -> None:
        """Send `spec.domain.spec.service(spec.payload)`, deduped on `spec.expected`.

        `matched` is the caller's fresh comparison of the *real* HA state
        against the target (see write_channel.matches()). HTTP success alone
        never marks a write confirmed – only `matched` does. While
        unconfirmed, the call is re-sent every INVERTER_WRITE_CONFIRM_TIMEOUT_SEC
        (one EMS tick), so a write that HA silently swallowed or that a slow
        bridge hasn't applied yet keeps being retried instead of being
        assumed done after a single 200.
        """
        now = time.monotonic()
        ch.label = label
        target = spec.expected
        domain, service, data = spec.domain, spec.service, spec.payload

        if ch.target != target:
            ch.target = target
            ch.confirmed = False
            ch.sent_at = None
            ch.pending_since = now

        if ch.confirmed:
            return

        if matched:
            if ch.pending_since is not None:
                self._pending_events.append({
                    "channel": label,
                    "target": target,
                    "outcome": "confirmed",
                    "latency_sec": round(now - ch.pending_since, 1),
                })
            ch.confirmed = True
            ch.sent_at = None
            ch.pending_since = None
            return

        if ch.sent_at is not None and (now - ch.sent_at) < INVERTER_WRITE_CONFIRM_TIMEOUT_SEC:
            return   # write in flight – give this cycle a chance to land

        if sim:
            # Nothing is actually sent to the inverter, so there is nothing
            # for the real HA state to ever confirm – treat it as done right
            # away (matches pre-v2.0.1 behaviour: log once per target change).
            # Not logged as a write-confirm event: simulated writes never
            # touch real hardware and would corrupt the real-world latency
            # data this is meant to collect.
            _LOGGER.info("[SIM] %s.%s(%s)", domain, service, data)
            ch.confirmed = True
            ch.sent_at = None
            ch.pending_since = None
            return

        ok = await self._call_service(domain, service, data)
        ch.sent_at = now
        if ok is False:
            # HA actively rejected the call (non-2xx) – that is a real failure.
            self._write_error_times.append(now)
            self._pending_events.append({
                "channel": label,
                "target": target,
                "outcome": "failed",
                "latency_sec": round(now - ch.pending_since, 1) if ch.pending_since else None,
            })
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
