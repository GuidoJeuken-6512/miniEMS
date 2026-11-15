"""B3–B5 – EV charging: wallbox interface, override, plan and PV surplus.

docs/roadmap/gesamtplan.md, B3 (Anbindung), B4 (Planung), B5 (PV-Überschuss).

The wallbox is driven through plain HA entities (no fixed vendor names in the
logic): a charge-enable switch, a current limit (number, A) and, optionally,
a phase switch. Read-only roles: charge power, plugged-in, status, the car's
SoC. Target SoC and departure come from HA helpers (``input_number``,
``input_datetime``) or the config.

Override from day one (B3): an ``input_select`` helper with the options
Auto / Jetzt / Nur PV / Stopp, read every tick. It needs no writing
integration and is the condition for going live with B4 and B5.

Write path like every other actuator: ``ev_control_enabled`` off = decisions
are only computed and shown; ``ev_control_simulation`` = logged as [SIM];
live = written and confirmed against the real entity state.

kW ↔ A uses ``ev_max_charge_kw / ev_max_current_a`` for the full phase count,
not 230 V × phases – a car with a two-phase on-board charger would otherwise
be planned wrong. Single-phase is 230 V × A.
"""
from __future__ import annotations

import logging
import math
import time
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any

from config_loader import parse_hhmm
from energy_task import TaskSlot
from ev_planner import EVPlan, EVTarget, next_time_of_day, plan_ev
from ev_surplus import SurplusRegulator
from ha_service import ConfirmedWriter
from write_channel import WriteChannelSet, WriteSpec, matches

if TYPE_CHECKING:
    from config_loader import Config
    from ha_service import HAServiceCaller
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)

PHASE_V = 230.0
_PLAN_RECOMPUTE_SEC = 900
_STUCK_AFTER_SEC = 120
_STUCK_RETRY_SEC = 600
_PLUGGED_STATES = {"on", "true", "connected", "plugged", "plugged_in", "charging",
                   "waiting", "ready", "complete", "suspended", "b", "c"}


class EVMode(str, Enum):
    AUTO = "auto"
    NOW = "now"
    PV = "pv"
    STOP = "stop"


_MODE_WORDS = {
    "auto": EVMode.AUTO, "automatik": EVMode.AUTO,
    "jetzt": EVMode.NOW, "now": EVMode.NOW, "sofort": EVMode.NOW,
    "nur pv": EVMode.PV, "pv": EVMode.PV, "pv-only": EVMode.PV, "pv only": EVMode.PV,
    "nur_pv": EVMode.PV, "solar": EVMode.PV,
    "stopp": EVMode.STOP, "stop": EVMode.STOP, "aus": EVMode.STOP, "off": EVMode.STOP,
}


def parse_mode(raw: str | None) -> EVMode:
    """The override helper's option → mode. Unknown or missing = Auto."""
    if not raw:
        return EVMode.AUTO
    return _MODE_WORDS.get(str(raw).strip().lower(), EVMode.AUTO)


@dataclass(frozen=True)
class EVCommand:
    enable: bool
    current_a: float
    phases: int
    source: str          # "stop" | "now" | "plan" | "pv" | "idle" | "unplugged"
    reason: str


class EVController:
    def __init__(self, cfg: "Config", ws: "HAStateClient",
                 caller: "HAServiceCaller | None") -> None:
        self._cfg = cfg
        self._ws = ws
        self._caller = caller
        self._writer = ConfirmedWriter(caller) if caller is not None else None
        self._channels = WriteChannelSet(["enable", "current", "phases"])
        self._regulator = self._new_regulator()
        self._plan: EVPlan | None = None
        self._plan_at: datetime | None = None
        self._plan_key: tuple | None = None
        self._command: EVCommand | None = None
        self._mode = EVMode.AUTO
        self._plugged = False
        self._session_kwh = 0.0
        self._last_t: float | None = None
        self._stuck_since: float | None = None
        self._stuck_retry_at: float | None = None
        self._stuck_retries = 0
        self.cap_w: float | None = None
        # C2: an override with an expiry beats the input_select helper.
        self.override_mode = "auto"

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        """Anything to read at all? (Read-only B3 works without control.)"""
        c = self._cfg
        return bool(c.ev_charge_power_entity or c.ev_plugged_entity)

    @property
    def controlling(self) -> bool:
        return self.configured and self._cfg.ev_control_enabled

    @property
    def simulation(self) -> bool:
        return self._cfg.ev_control_simulation

    @property
    def power_w(self) -> float:
        value = self._value(self._cfg.ev_charge_power_entity)
        if value is None:
            return 0.0
        # A kW sensor reads e.g. 7.2 – anything below 50 is taken as kW.
        return value * 1000.0 if 0 < value < 50 else max(0.0, value)

    @property
    def charging(self) -> bool:
        """Draws power right now (measured), or – without a power sensor – is told to."""
        if self._cfg.ev_charge_power_entity:
            return self.power_w > 200.0
        return self._command is not None and self._command.enable and self._command.current_a > 0

    @property
    def wants_grid(self) -> bool:
        """The command charges on purpose from the grid (plan or 'now')."""
        return self._command is not None and self._command.source in ("plan", "now")

    @property
    def mode(self) -> EVMode:
        return self._mode

    @property
    def plan(self) -> EVPlan | None:
        return self._plan

    def w_per_a(self, phases: int) -> float:
        c = self._cfg
        full = max(1, int(c.ev_phases_max))
        if phases >= full and c.ev_max_current_a > 0 and c.ev_max_charge_kw > 0:
            return min(PHASE_V * full, c.ev_max_charge_kw * 1000.0 / c.ev_max_current_a)
        return PHASE_V * max(1, phases)

    def max_w(self) -> float:
        c = self._cfg
        return c.ev_max_current_a * self.w_per_a(max(1, int(c.ev_phases_max)))

    def min_w(self) -> float:
        c = self._cfg
        phases = 1 if self._can_switch() else max(1, int(c.ev_phases_max))
        return c.ev_min_current_a * self.w_per_a(phases)

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def update(
        self,
        now: datetime,
        *,
        slots: list[TaskSlot] | None,
        grid_w: float | None,
        yield_battery_w: float = 0.0,
        expensive_import: bool = False,
        cap_w: float | None = None,
        t: float | None = None,
    ) -> None:
        if not self.configured:
            return
        t = time.monotonic() if t is None else t
        self.cap_w = cap_w
        self._mode = (parse_mode(self.override_mode) if self.override_mode != "auto"
                      else parse_mode(self._raw(self._cfg.ev_mode_entity)))
        plugged = self._read_plugged()
        if self._last_t is not None and plugged:
            self._session_kwh += self.power_w * max(0.0, t - self._last_t) / 3_600_000.0
        self._last_t = t
        if plugged != self._plugged:
            self._plugged = plugged
            self._regulator = self._new_regulator()
            self._session_kwh = 0.0
            self._plan = None
            self._stuck_since = None
            self._stuck_retries = 0

        if not plugged:
            self._command = EVCommand(False, 0.0, self._regulator.phases, "unplugged",
                                      "not plugged in")
            return

        if self._mode is EVMode.AUTO:
            self._refresh_plan(now, slots)

        self._command = self._decide(now, t, grid_w, yield_battery_w, expensive_import, cap_w)
        if self.controlling:
            await self._apply(self._command)
            await self._check_stuck(t)

    def _decide(self, now: datetime, t: float, grid_w: float | None, yield_w: float,
                expensive: bool, cap_w: float | None) -> EVCommand:
        c = self._cfg
        full_phases = max(1, int(c.ev_phases_max))
        if self._mode is EVMode.STOP:
            self._regulator = self._new_regulator()
            return EVCommand(False, 0.0, self._regulator.phases, "stop", "override: stop")
        if self._mode is EVMode.NOW:
            amps = c.ev_max_current_a
            reason = "override: charge now"
            if cap_w is not None:
                amps = min(amps, math.floor(cap_w / self.w_per_a(full_phases)))
                if amps < c.ev_min_current_a:
                    return EVCommand(True, 0.0, full_phases, "now",
                                     "override: charge now – paused, fuse limit")
                if amps < c.ev_max_current_a:
                    reason += f" – limited to {amps:.0f} A by the fuse"
            return EVCommand(True, float(amps), full_phases, "now", reason)

        available = None if grid_w is None else self.power_w - grid_w + max(0.0, yield_w)
        pv_cmd = None
        if available is not None:
            pv_cmd = self._regulator.step(t, available, expensive, cap_w)
        if self._mode is EVMode.PV:
            if pv_cmd is None:
                return EVCommand(False, 0.0, self._regulator.phases, "pv",
                                 "pv only: grid reading missing")
            return EVCommand(pv_cmd.charging, pv_cmd.current_a, pv_cmd.phases, "pv",
                             "pv only: " + pv_cmd.reason)

        planned_w = self._plan.power_at(now) if self._plan is not None else 0.0
        if cap_w is not None:
            planned_w = min(planned_w, cap_w)
        pv_w = (pv_cmd.current_a * self._regulator.w_per_a(pv_cmd.phases)
                if pv_cmd is not None and pv_cmd.charging else 0.0)
        if planned_w > 0 and planned_w >= pv_w:
            amps = planned_w / self.w_per_a(full_phases)
            amps = max(c.ev_min_current_a, min(c.ev_max_current_a, math.ceil(amps)))
            if cap_w is not None and amps * self.w_per_a(full_phases) > cap_w + 1:
                amps = math.floor(cap_w / self.w_per_a(full_phases))
                if amps < c.ev_min_current_a:
                    return EVCommand(True, 0.0, full_phases, "plan", "plan: paused, fuse limit")
            return EVCommand(True, float(amps), full_phases, "plan",
                             f"plan: {planned_w:.0f} W in this slot ({self._plan.reason})")
        if pv_cmd is not None and pv_cmd.charging:
            return EVCommand(True, pv_cmd.current_a, pv_cmd.phases, "pv", pv_cmd.reason)
        reason = self._plan.reason if self._plan is not None else "no plan"
        return EVCommand(True, 0.0, self._regulator.phases, "idle", f"waiting – {reason}")

    # ------------------------------------------------------------------
    # Plan (B4)
    # ------------------------------------------------------------------

    def target(self, now: datetime) -> EVTarget:
        c = self._cfg
        target_pct = self._value(c.ev_target_soc_entity)
        if target_pct is None:
            target_pct = c.ev_target_soc_pct
        departure = self._departure(now)
        morning_at = None
        hm = parse_hhmm(c.ev_morning_time)
        if c.ev_morning_soc_pct > 0 and hm is not None:
            morning_at = next_time_of_day(now, *hm)
        soc = self._value(c.ev_soc_entity)
        return EVTarget(soc_pct=soc, target_pct=float(target_pct),
                        capacity_kwh=c.ev_capacity_kwh, departure=departure,
                        morning_pct=c.ev_morning_soc_pct, morning_at=morning_at,
                        charged_kwh=self._session_kwh)

    def _departure(self, now: datetime) -> datetime:
        raw = self._raw(self._cfg.ev_departure_entity)
        if raw:
            parsed = _parse_departure(raw, now)
            if parsed is not None:
                return parsed
        hm = parse_hhmm(self._cfg.ev_departure_time) or (7, 0)
        return next_time_of_day(now, *hm)

    def _refresh_plan(self, now: datetime, slots: list[TaskSlot] | None) -> None:
        if slots is None:
            if self._plan is None:
                return
            # Keep the last plan for at most its own age limit.
            if self._plan_at is not None and now - self._plan_at > timedelta(seconds=2 * _PLAN_RECOMPUTE_SEC):
                self._plan = None
            return
        tgt = self.target(now)
        key = (round(tgt.target_pct), tgt.departure, tgt.morning_pct,
               None if tgt.soc_pct is None else round(tgt.soc_pct))
        slot_start = slots[0].start if slots else None
        fresh = (self._plan is not None and self._plan_at is not None
                 and now - self._plan_at < timedelta(seconds=_PLAN_RECOMPUTE_SEC)
                 and key == self._plan_key
                 and not any(s.start <= now < s.end and s.start > self._plan_at for s in self._plan.slots))
        if fresh and slot_start is not None:
            return
        self._plan = plan_ev(tgt, slots, min_w=self.min_w(), max_w=self.max_w(), now=now)
        self._plan_at = now
        self._plan_key = key
        if not self._plan.feasible:
            _LOGGER.warning("EV: %s", self._plan.reason)

    def extra_load_kwh(self, start: datetime, end: datetime) -> float:
        """Planned EV energy in [start, end) – the battery plan sees it as load."""
        if self._plan is None or self._mode is not EVMode.AUTO or not self._plugged:
            return 0.0
        total = 0.0
        for s, p in zip(self._plan.slots, self._plan.power_w):
            if p <= 0 or s.end <= start or s.start >= end:
                continue
            overlap_h = (min(end, s.end) - max(start, s.start)).total_seconds() / 3600.0
            total += p * overlap_h / 1000.0
        return total

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def _apply(self, cmd: EVCommand) -> None:
        c = self._cfg
        if self._writer is None:
            return
        sim = self.simulation
        pause_by_switch = c.ev_pause_mode == "switch"
        enable = cmd.enable and not (pause_by_switch and cmd.current_a <= 0)
        if c.ev_enable_entity:
            raw = self._raw(c.ev_enable_entity)
            expected = "on" if enable else "off"
            spec = WriteSpec("switch", "turn_on" if enable else "turn_off", c.ev_enable_entity,
                             {"entity_id": c.ev_enable_entity}, expected)
            await self._writer.write(self._channels["enable"], spec, matches(raw, spec), sim,
                                     f"EV enable ({c.ev_enable_entity})")
        if c.ev_current_entity and (enable or not c.ev_enable_entity):
            amps = cmd.current_a if enable else 0.0
            if pause_by_switch and amps <= 0:
                amps = c.ev_min_current_a
            spec = WriteSpec("number", "set_value", c.ev_current_entity,
                             {"entity_id": c.ev_current_entity, "value": amps}, amps)
            await self._writer.write(self._channels["current"], spec,
                                     matches(self._value(c.ev_current_entity), spec), sim,
                                     f"EV current ({c.ev_current_entity})")
        if c.ev_phases_entity and self._can_switch():
            entity = c.ev_phases_entity
            domain = entity.split(".", 1)[0]
            if domain in ("select", "input_select"):
                value = str(cmd.phases)
                spec = WriteSpec("select", "select_option", entity,
                                 {"entity_id": entity, "option": value}, value)
                observed = self._raw(entity)
            else:
                spec = WriteSpec("number", "set_value", entity,
                                 {"entity_id": entity, "value": cmd.phases}, cmd.phases)
                observed = self._value(entity)
            await self._writer.write(self._channels["phases"], spec, matches(observed, spec),
                                     sim, f"EV phases ({entity})")

    async def _check_stuck(self, t: float) -> None:
        """'Charge hangs': status waits for start although enabled with current."""
        c = self._cfg
        cmd = self._command
        if (not c.ev_status_entity or cmd is None or not cmd.enable or cmd.current_a <= 0):
            self._stuck_since = None
            return
        state = (self._raw(c.ev_status_entity) or "").strip().lower()
        waiting = {s.strip().lower() for s in c.ev_status_waiting_states.split(",") if s.strip()}
        if state not in waiting:
            self._stuck_since = None
            return
        if self._stuck_since is None:
            self._stuck_since = t
            return
        if t - self._stuck_since < _STUCK_AFTER_SEC:
            return
        if self._stuck_retry_at is not None and t - self._stuck_retry_at < _STUCK_RETRY_SEC:
            return
        self._stuck_retry_at = t
        self._stuck_retries += 1
        _LOGGER.warning("EV: charge hangs (status %s) – turn_on attempt %d", state,
                        self._stuck_retries)
        if self.simulation or not c.ev_enable_entity or self._caller is None:
            _LOGGER.info("[SIM] switch.turn_on(%s)", c.ev_enable_entity)
            return
        await self._caller.call("switch", "turn_on", {"entity_id": c.ev_enable_entity})

    def failsafe_actions(self) -> list[dict[str, Any]]:
        """A10: hand the wallbox back – enabled at full current (costs money,
        harms neither the car nor the comfort). Empty while not live."""
        c = self._cfg
        if not self.controlling or self.simulation:
            return []
        actions: list[dict[str, Any]] = []
        if c.ev_current_entity:
            actions.append({"service": "number.set_value",
                            "data": {"entity_id": c.ev_current_entity, "value": c.ev_max_current_a}})
        if c.ev_enable_entity:
            actions.append({"service": "switch.turn_on", "data": {"entity_id": c.ev_enable_entity}})
        return actions

    async def restore_safe_defaults(self) -> None:
        if not self.controlling:
            return
        _LOGGER.info("EV: handing the wallbox back (enabled, %s A)", self._cfg.ev_max_current_a)
        self._channels.reset()
        c = self._cfg
        await self._apply(EVCommand(True, float(c.ev_max_current_a), max(1, int(c.ev_phases_max)),
                                    "handover", "shutdown"))

    def reset_write_state(self) -> None:
        self._channels.reset()

    def pop_write_events(self) -> list[dict[str, Any]]:
        return self._writer.pop_events() if self._writer is not None else []

    # ------------------------------------------------------------------
    # Status
    # ------------------------------------------------------------------

    def status(self, now: datetime) -> dict[str, Any]:
        cmd = self._command
        c = self._cfg
        return {
            "control": ("off" if not c.ev_control_enabled
                        else "simulation" if c.ev_control_simulation else "live"),
            "mode": self._mode.value,
            "plugged": self._plugged,
            "power_w": round(self.power_w),
            "soc_pct": self._value(c.ev_soc_entity),
            "session_kwh": round(self._session_kwh, 2),
            "command": None if cmd is None else {
                "enable": cmd.enable, "current_a": cmd.current_a, "phases": cmd.phases,
                "source": cmd.source, "reason": cmd.reason,
            },
            "cap_w": None if self.cap_w is None else round(self.cap_w),
            "plan": self._plan.as_dict() if self._plan is not None else None,
            "stuck_retries": self._stuck_retries,
            "write_unconfirmed": self._channels.unconfirmed_count,
        }

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _can_switch(self) -> bool:
        c = self._cfg
        return bool(c.ev_phases_entity) and int(c.ev_phases_max) > 1

    def _new_regulator(self) -> SurplusRegulator:
        c = self._cfg
        full = max(1, int(c.ev_phases_max))
        return SurplusRegulator(c.ev_min_current_a, c.ev_max_current_a, full,
                                self.w_per_a(1), self.w_per_a(full), self._can_switch())

    def _read_plugged(self) -> bool:
        c = self._cfg
        if c.ev_plugged_entity:
            raw = (self._raw(c.ev_plugged_entity) or "").strip().lower()
            return raw in _PLUGGED_STATES
        # No plug sensor: plugged when the car draws power.
        return self.power_w > 200.0

    def _raw(self, entity: str) -> str | None:
        if not entity:
            return None
        state = self._ws.state_cache.get(entity, {}).get("state") if hasattr(self._ws, "state_cache") else None
        if state in (None, "unavailable", "unknown"):
            return None
        return str(state)

    def _value(self, entity: str) -> float | None:
        return self._ws.get_state_value(entity) if entity else None


def _parse_departure(raw: str, now: datetime) -> datetime | None:
    """input_datetime state: 'YYYY-MM-DD HH:MM:SS' or a time 'HH:MM:SS'."""
    raw = raw.strip()
    try:
        if len(raw) >= 16 and raw[4] == "-":
            dt = datetime.fromisoformat(raw.replace(" ", "T"))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
            return dt if dt > now else None
        parts = raw.split(":")
        return next_time_of_day(now, int(parts[0]), int(parts[1]))
    except (ValueError, IndexError):
        return None
