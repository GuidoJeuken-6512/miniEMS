"""Hot-water heating decision and actuator (docs/roadmap/v3.4-warmwasser.md, Schritt 3+4).

Mirrors the battery's grid-charge decision: a pure per-tick decision with a
reason, fed by a plan (hot_water_planner.py), with a protection rule that
always wins and a fail-safe that hands control back to the heat pump.

The heat pump is steered only through its boiler setpoint (Lambda), and the
setpoint is a *start impulse*, not the goal: to start a charge it is raised
well above the boiler (boiler + hw_boost_delta_k, ad hoc + hw_adhoc_delta_k,
at least shut-off + _PSEUDO_MARGIN_K) so the heat pump switches to hot-water
mode. miniEMS itself ends the charge once the *measured* boiler temperature
reaches the shut-off temperature – hw_morning_target_c for plan, protection
and ad-hoc charges, hw_max_temp_c for a spontaneous PV-surplus charge – and
then writes the fixed hw_idle_setpoint_c (e.g. 35 °C), far below the day
target, so the heat pump's own hysteresis never starts a charge (the minimum
is guarded by the protection rule).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from battery_projection import BatteryContext, project_battery
from config_loader import parse_hhmm
from const import (
    HW_ADHOC_MAX_SEC,
    HW_CHARGE_MAX_SEC,
    HW_PLAN_RECOMPUTE_SEC,
    HW_WH_PER_L_K,
    PLAN_HORIZON_H,
    PLAN_SLOT_MIN,
    HWMode,
)
from event_log import LogEntry
from ha_service import ConfirmedWriter
from hot_water_model import HotWaterModelLoader, is_workday
from hot_water_planner import HotWaterPlan, HWRequirement, HWSlot, plan_hot_water
from write_channel import WriteChannel, WriteSpec

if TYPE_CHECKING:
    from bridge_planner import PlanSlot
    from config_loader import Config
    from event_log import EventLog
    from ha_service import HAServiceCaller
    from ha_state_client import HAStateClient
    from store import EnergyStore
    from weather_client import WeatherClient

_LOGGER = logging.getLogger(__name__)

# Hard ceiling for any written setpoint, whatever goal + boost would give.
_SETPOINT_MAX_C = 65.0
# The start-impulse setpoint is at least this far above the shut-off
# temperature, so the heat pump never stops on its own before miniEMS does.
_PSEUDO_MARGIN_K = 2.0
# The morning requirement sits this far below the shut-off temperature: the
# plan never heats above shut-off, so a requirement right at it could not
# survive even one slot of standby loss.
_MORNING_SLACK_K = 1.0
_CHARGING_MODES = (HWMode.CHARGING, HWMode.PV, HWMode.PROTECT, HWMode.MANUAL)


@dataclass
class _Charge:
    mode: HWMode
    goal_c: float
    setpoint_c: float
    since: datetime
    reason: str
    start_c: float = 0.0
    cost_th_eur_kwh: float | None = None   # assumed cost per kWh of heat
    # Started by the plan: runs to shut-off whatever PV does meanwhile. Only a
    # spontaneous export-driven PV charge ends when the surplus is gone.
    planned: bool = False


class HotWaterController:
    def __init__(
        self,
        cfg: "Config",
        ws: "HAStateClient",
        store: "EnergyStore | None",
        caller: "HAServiceCaller | None",
        event_log: "EventLog | None" = None,
        weather: "WeatherClient | None" = None,
        interval_sec: int = 30,
    ) -> None:
        self._cfg = cfg
        self._ws = ws
        self._loader = HotWaterModelLoader(store, cfg, interval_sec)
        self._writer = ConfirmedWriter(caller) if caller is not None else None
        self._channel = WriteChannel()
        self._event_log = event_log
        self._weather = weather
        self._mode = HWMode.IDLE
        self._reason = "startup"
        self._charge: _Charge | None = None
        self._last_stop: datetime | None = None
        self._adhoc_since: datetime | None = None
        # Fixed when the button is pressed: boiler temperature + hw_adhoc_delta_k.
        self._adhoc_target_c: float | None = None
        self._grid_w: float | None = None
        self._plan: HotWaterPlan | None = None
        self._plan_at: datetime | None = None
        # End of the slot the plan was computed for: crossing it re-plans, so a
        # charge planned for the next slot starts on time (and every tariff
        # change is a decision point).
        self._plan_slot_end: datetime | None = None
        self._last_rate: float | None = None
        self._pending_logs: list[LogEntry] = []
        # Summary of the charge just stopped, logged by the next _step().
        self._stop_note: str | None = None
        self._stop_cost: float | None = None
        self._setpoint_c: float | None = None
        self._boil_c: float | None = None
        self._outdoor_c: float | None = None
        self._warned_no_service = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        """Decisions are computed whenever a boiler temperature is configured."""
        return bool(self._cfg.boil_temp_entity)

    @property
    def couples(self) -> bool:
        """Does the WW plan replace the flat hot-water share in the battery plan?

        Only when miniEMS really moves the heat pump – in simulation the heat
        pump still runs on its own schedule and the history stays correct.
        """
        return (self.enabled and self._cfg.hw_control_enabled
                and not self._cfg.hw_control_simulation and self._plan is not None)

    @property
    def mode(self) -> HWMode:
        return self._mode

    @property
    def plan(self) -> HotWaterPlan | None:
        return self._plan

    @property
    def model(self):
        return self._loader.model

    async def request_adhoc(self, now: datetime | None = None) -> dict[str, Any]:
        """Start a manual charge: setpoint impulse boiler + hw_adhoc_delta_k,
        ended by miniEMS at the shut-off temperature.

        Decided and written right away, not on the next EMS tick, so the
        button takes effect immediately.
        """
        now = now or datetime.now().astimezone()
        self._boil_c = self._value(self._cfg.boil_temp_entity)
        if self._boil_c is not None and self._boil_c >= self._shutoff_c():
            _LOGGER.info("Hot water: ad-hoc ignored – boiler %.1f °C already at shut-off %.1f °C",
                         self._boil_c, self._shutoff_c())
            return {**self.adhoc_status(now), "info": "already at shut-off temperature"}
        self._adhoc_since = now
        self._adhoc_target_c = self._shutoff_c()
        _LOGGER.info("Hot water: manual ad-hoc charge requested (boiler %s °C, setpoint %s °C, "
                     "shut-off %.1f °C)", self._boil_c,
                     self._impulse_c(HWMode.MANUAL, self._shutoff_c(), self._boil_c),
                     self._shutoff_c())
        await self._step(now)
        return self.adhoc_status(now)

    async def cancel_adhoc(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        if self._adhoc_since is not None:
            _LOGGER.info("Hot water: manual ad-hoc charge cancelled")
        self._clear_adhoc()
        if self._charge is not None and self._charge.mode is HWMode.MANUAL:
            self._stop_charge(now, "manual ad-hoc cancelled")
        await self._step(now)
        return self.adhoc_status(now)

    def adhoc_status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        active = self._adhoc_since is not None
        return {
            "active": active,
            # Shut-off temperature: the charge ends when the boiler reaches it.
            "target_c": self._adhoc_target_c if active else self._shutoff_c(),
            # Start impulse written to the heat pump (while inactive: what a press would write).
            "setpoint_c": (self._charge.setpoint_c
                           if active and self._charge is not None
                           and self._charge.mode is HWMode.MANUAL
                           else self._impulse_c(HWMode.MANUAL, self._shutoff_c(), self._boil_c)),
            "delta_k": self._cfg.hw_adhoc_delta_k,
            "since": self._adhoc_since.isoformat() if active else None,
            "elapsed_sec": int((now - self._adhoc_since).total_seconds()) if active else None,
            "max_sec": HW_ADHOC_MAX_SEC,
            "simulation": self._cfg.hw_control_simulation,
            "control_enabled": self._cfg.hw_control_enabled,
        }

    def load_delta_kwh(self, slot_end: datetime, hours: float) -> float:
        """Change to a bridge-plan slot's load: planned WW minus the flat WW share."""
        if not self.couples:
            return 0.0
        planned = self._plan.planned_elec_by_end.get(slot_end, 0.0)   # type: ignore[union-attr]
        flat = (self.model.ww_elec_kwh_per_h or 0.0) * hours
        return planned - flat

    def planned_pv_kwh_before(self, deadline: datetime) -> float:
        """Planned WW electricity from PV surplus before `deadline` – PV the battery won't get."""
        if not self.couples:
            return 0.0
        return sum(v for end, v in self._plan.pv_elec_by_end.items()   # type: ignore[union-attr]
                   if end <= deadline)

    async def update(
        self,
        now: datetime,
        slots: "list[PlanSlot] | None",
        pv_known: bool,
        load_known: bool,
        grid_w: float | None,
        price: float | None,
        battery: "BatteryContext | None" = None,
    ) -> None:
        if not self.enabled:
            return
        cfg = self._cfg
        self._boil_c = self._value(cfg.boil_temp_entity)
        self._outdoor_c = (self._value(cfg.outdoor_temp_entity) if cfg.outdoor_temp_entity
                           else (self._weather.hourly_temp_at(now) if self._weather else None))
        model = await self._loader.refresh(now)

        if self._boil_c is not None and (
            self._plan is None or self._plan_at is None
            or (now - self._plan_at).total_seconds() >= HW_PLAN_RECOMPUTE_SEC
            or (self._plan_slot_end is not None and now >= self._plan_slot_end)
        ):
            hw_slots = self._hw_slots(now, slots, pv_known, load_known, price, battery)
            self._plan = plan_hot_water(
                now, hw_slots, self._boil_c, model,
                floor_c=self._floor_c(),
                # Never plan above the shut-off temperature – the controller
                # ends every planned charge there.
                shutoff_c=self._shutoff_c(),
                volume_l=cfg.hw_volume_l,
                feed_in_eur_kwh=cfg.feed_in_tariff_eur_kwh,
                requirements=self._requirements(now, hw_slots[-1].end if hw_slots else now),
                tolerance_pct=cfg.hw_decision_tolerance_pct,
            )
            self._plan_at = now
            self._plan_slot_end = hw_slots[0].end if hw_slots else None
            rate = hw_slots[0].rate_eur_kwh if hw_slots else None
            tariff_change = (rate is not None and self._last_rate is not None
                             and abs(rate - self._last_rate) > 1e-4)
            self._last_rate = rate
            self._queue_decision_log(rate, tariff_change)

        self._grid_w = grid_w
        await self._step(now)

    def _queue_decision_log(self, rate: float | None, tariff_change: bool) -> None:
        """Event-log the plan's decision at every tariff change – only there.

        The 5-minute re-plans often just shift the planned start by a slot as
        the forecasts move; logging those flooded the event log with "wait"
        entries. A charge that actually starts is logged by _step() anyway.
        """
        plan = self._plan
        if plan is None or not tariff_change:
            return
        prefix = f"tariff change {rate * 100:.1f} ct: " if rate is not None else ""
        cost = (plan.windows[0].cost_th_eur_kwh if plan.charge_now and plan.windows
                else plan.now_cost_th_eur_kwh)
        self._pending_logs.append(self._entry(
            "hw_charge" if plan.charge_now else "hw_wait", prefix + plan.decision, cost))

    def _entry(self, state: str, reason: str, cost_th: float | None) -> LogEntry:
        return LogEntry(
            timestamp=datetime.now(timezone.utc).isoformat(timespec="seconds"),
            state=state, battery_kwh_freetochange=0.0, battery_kwh_useable=0.0,
            predicted_load_kwh=None, entry_type="hw_decision",
            price_eur_kwh=round(cost_th, 4) if cost_th is not None else None,
            mode=self._mode.value, reason=reason,
        )

    async def _step(self, now: datetime) -> None:
        """Decide, log the action and write the setpoint."""
        prev = self._mode
        prev_charge = self._charge
        self._decide(now, self._grid_w)
        logs, self._pending_logs = self._pending_logs, []
        ch = self._charge
        if ch is not None and ch is not prev_charge:
            # A charge started: the action entry replaces the plain decision entry.
            logs = [e for e in logs if e.state != "hw_charge"]
            logs.append(self._entry(
                "hw_on", f"start {ch.mode.value}: {ch.start_c:.1f} → {ch.goal_c:.0f} °C, "
                         f"setpoint {ch.setpoint_c:.1f} °C – {ch.reason}", ch.cost_th_eur_kwh))
        if self._stop_note is not None:
            # Set by _stop_charge – here or just before (e.g. ad-hoc cancelled).
            logs.append(self._entry("hw_off", self._stop_note, self._stop_cost))
            self._stop_note = self._stop_cost = None
        elif self._mode is not prev and ch is None:
            logs.append(self._entry("hw_off", self._reason, None))
        if self._mode is not prev:
            _LOGGER.info("Hot water: %s → %s (%s)", prev.value, self._mode.value, self._reason)
        for entry in logs:
            await self._log(entry)
        await self._apply()

    def status(self, now: datetime | None = None) -> dict[str, Any]:
        now = now or datetime.now().astimezone()
        cfg = self._cfg
        ch = self._charge
        nxt = self._next_morning(now)
        return {
            "mode": self._mode.value,
            "reason": self._reason,
            "control_enabled": cfg.hw_control_enabled,
            "simulation": cfg.hw_control_simulation,
            "write_service": cfg.hw_write_service,
            "boil_temp_c": self._boil_c,
            "outdoor_temp_c": self._outdoor_c,
            "min_temp_c": cfg.hot_water_min_temp_c,
            "day_target_c": self._floor_c(),
            "shutoff_c": self._shutoff_c(),
            "max_temp_c": cfg.hw_max_temp_c,
            "decision": self._plan.decision if self._plan else None,
            "now_cost_th_eur_kwh": (round(self._plan.now_cost_th_eur_kwh, 4)
                                    if self._plan and self._plan.now_cost_th_eur_kwh is not None
                                    else None),
            "morning": {
                "target_c": cfg.hw_morning_target_c,
                "time": cfg.hw_morning_time,
                "next": nxt.isoformat() if nxt else None,
            },
            "setpoint": {
                "target_c": self._setpoint_c,
                "raw_value": (round(self._setpoint_c * cfg.hw_write_scale)
                              if self._setpoint_c is not None else None),
                "read_back_c": self._value(cfg.hw_target_entity),
                "confirmed": self._channel.confirmed,
            },
            "charge": {
                "mode": ch.mode.value, "goal_c": round(ch.goal_c, 1),
                "setpoint_c": round(ch.setpoint_c, 1), "since": ch.since.isoformat(),
                "start_c": round(ch.start_c, 1),
                "cost_th_eur_kwh": (round(ch.cost_th_eur_kwh, 4)
                                    if ch.cost_th_eur_kwh is not None else None),
            } if ch else None,
            "adhoc": self.adhoc_status(now),
            "learned": self.model.as_dict(self._outdoor_c),
            "plan": self._plan.as_dict() if self._plan else None,
            "couples_battery_plan": self.couples,
        }

    def pop_write_events(self) -> list[dict[str, Any]]:
        return self._writer.pop_events() if self._writer else []

    def reset_write_state(self) -> None:
        """Forget the last sent/confirmed setpoint (see InverterController.reset_write_state)."""
        self._channel = WriteChannel()

    async def restore_safe_defaults(self) -> None:
        """Hand the boiler back to the heat pump's own control on shutdown."""
        if not self.enabled or not self._cfg.hw_control_enabled:
            return
        _LOGGER.info("Hot water: restoring setpoint %.0f °C before shutdown",
                     self._floor_c())
        self._channel = WriteChannel()
        self._setpoint_c = self._floor_c()
        await self._write(self._setpoint_c)

    # ------------------------------------------------------------------
    # Decision
    # ------------------------------------------------------------------

    def _decide(self, now: datetime, grid_w: float | None) -> None:
        cfg = self._cfg
        t = self._boil_c
        if t is None:
            self._charge = None
            self._set(HWMode.HANDOFF, "boiler temperature unavailable – heat pump controls itself")
            return

        self._check_stop(now, t, grid_w)

        if t < cfg.hot_water_min_temp_c:
            goal = self._shutoff_c()
            if self._charge is None or self._charge.goal_c < goal:
                self._start(now, HWMode.PROTECT, goal, t,
                            f"below minimum {cfg.hot_water_min_temp_c:.0f} °C")
            self._set(self._charge.mode, self._charge.reason)   # type: ignore[union-attr]
            return

        if self._adhoc_since is not None:
            if self._adhoc_target_c is None:
                self._adhoc_target_c = self._shutoff_c()
            target = self._adhoc_target_c
            elapsed = (now - self._adhoc_since).total_seconds()
            if elapsed > HW_ADHOC_MAX_SEC:
                self._clear_adhoc()
                if self._charge is not None and self._charge.mode is HWMode.MANUAL:
                    self._stop_charge(now, "manual ad-hoc timed out")
            elif t >= target:
                self._clear_adhoc()
            elif self._charge is None or self._charge.mode is not HWMode.MANUAL:
                self._start(now, HWMode.MANUAL, target, t,
                            f"manual ad-hoc until {target:.0f} °C")

        if self._charge is not None:
            self._set(self._charge.mode, self._charge.reason)
            return

        if (self._last_stop is not None
                and (now - self._last_stop).total_seconds() < cfg.hw_min_off_sec):
            self._set(HWMode.IDLE, "pause after charge (min off time)")
            return

        plan = self._plan
        goal = self._shutoff_c()
        if plan is not None and plan.charge_now and plan.windows and t < goal - 0.5:
            window = plan.windows[0]
            self._start(now, HWMode.PV if window.source == "pv" else HWMode.CHARGING,
                        goal, t, f"plan: {plan.decision}")
            self._charge.planned = True   # type: ignore[union-attr]
            self._set(self._charge.mode, self._charge.reason)   # type: ignore[union-attr]
            return

        if (grid_w is not None and grid_w <= -cfg.hw_pv_min_export_w
                and t < cfg.hw_max_temp_c - 1.0):
            reason = f"pv surplus {-grid_w / 1000:.1f} kW exported"
            self._start(now, HWMode.PV, cfg.hw_max_temp_c, t, reason)
            self._set(HWMode.PV, reason)
            return

        if plan is not None and plan.decision and not plan.charge_now:
            self._set(HWMode.IDLE, plan.decision)
        elif plan is not None and not plan.feasible:
            self._set(HWMode.IDLE, plan.reason)
        else:
            self._set(HWMode.IDLE, "no charge needed")

    def _check_stop(self, now: datetime, t: float, grid_w: float | None) -> None:
        ch = self._charge
        if ch is None:
            return
        # The setpoint is only a start impulse above the goal: miniEMS ends the
        # charge on the measured boiler temperature.
        if t >= ch.goal_c:
            if ch.mode is HWMode.MANUAL:
                self._clear_adhoc()
            self._stop_charge(now, f"goal {ch.goal_c:.0f} °C reached")
        elif (now - ch.since).total_seconds() > HW_CHARGE_MAX_SEC:
            self._stop_charge(now, "charge ran longer than allowed")
        elif ch.mode is HWMode.PV and not ch.planned and grid_w is not None and grid_w > 0 \
                and self._plan is not None and self._plan.window_at(now) is None:
            self._stop_charge(now, "pv surplus gone")

    def _start(self, now: datetime, mode: HWMode, goal_c: float, t: float, reason: str) -> None:
        setpoint = self._impulse_c(mode, goal_c, t)
        self._charge = _Charge(mode=mode, goal_c=goal_c, setpoint_c=setpoint,
                               since=now, reason=reason, start_c=t,
                               cost_th_eur_kwh=self._start_cost_th(mode))

    def _start_cost_th(self, mode: HWMode) -> float | None:
        """Assumed cost per kWh of heat for a charge started now."""
        plan = self._plan
        if plan is None:
            return None
        if mode is HWMode.PV and not (plan.charge_now and plan.windows):
            return plan.now_pv_cost_th_eur_kwh      # spontaneous export: feed-in / COP
        if plan.charge_now and plan.windows:
            return plan.windows[0].cost_th_eur_kwh
        return plan.now_cost_th_eur_kwh

    def _stop_charge(self, now: datetime, why: str) -> None:
        ch = self._charge
        if ch is None:
            return
        _LOGGER.info("Hot water: charge stopped – %s", why)
        # What the charge delivered, from the measured boiler temperature.
        t = self._boil_c if self._boil_c is not None else ch.start_c
        heat_kwh = max(0.0, t - ch.start_c) * HW_WH_PER_L_K * self._cfg.hw_volume_l / 1000.0
        minutes = (now - ch.since).total_seconds() / 60.0
        note = f"{why}: {ch.start_c:.1f} → {t:.1f} °C in {minutes:.0f} min, {heat_kwh:.2f} kWh_th"
        if ch.cost_th_eur_kwh is not None:
            note += f", ~{heat_kwh * ch.cost_th_eur_kwh:.2f} €"
        self._stop_note, self._stop_cost = note, ch.cost_th_eur_kwh
        self._last_stop = now
        self._charge = None
        self._reason = why

    def _set(self, mode: HWMode, reason: str) -> None:
        self._mode = mode
        self._reason = reason

    # ------------------------------------------------------------------
    # Actuator
    # ------------------------------------------------------------------

    def _desired_setpoint(self) -> float:
        cfg = self._cfg
        if self._mode is HWMode.HANDOFF:
            return self._floor_c()
        if self._charge is not None:
            return self._charge.setpoint_c
        # Far below the day target: the heat pump must not start on its own.
        return cfg.hw_idle_setpoint_c

    async def _apply(self) -> None:
        self._setpoint_c = self._desired_setpoint()
        if self._cfg.hw_control_enabled:
            await self._write(self._setpoint_c)

    async def _write(self, setpoint_c: float) -> None:
        cfg = self._cfg
        raw = round(setpoint_c * cfg.hw_write_scale)
        try:
            data = json.loads(cfg.hw_write_data.replace("{value}", str(raw)))
        except (json.JSONDecodeError, TypeError) as exc:
            _LOGGER.error("hw_write_data is not valid JSON after inserting the value: %s", exc)
            return
        if "." in cfg.hw_write_service:
            domain, service = cfg.hw_write_service.split(".", 1)
        elif cfg.hw_control_simulation:
            domain, service = "<hw_write_service unset>", "write"
        else:
            if not self._warned_no_service:
                _LOGGER.warning("hw_control_enabled but hw_write_service is not set – nothing written")
                self._warned_no_service = True
            return
        actual = self._value(cfg.hw_target_entity)
        spec = WriteSpec(domain=domain, service=service, entity_id=cfg.hw_target_entity,
                         payload=data, expected=round(setpoint_c, 1))
        matched = actual is not None and abs(actual - setpoint_c) < 0.5
        if self._writer is None:
            return
        # A confirmed setpoint is re-checked every tick: if the heat pump later
        # reports something else (a stale read at startup, e.g. the shutdown
        # value of the previous run, or another writer on the same register),
        # forget the confirmation so the value is sent again. Not in simulation,
        # where "confirmed" never meant the heat pump has the value.
        if (self._channel.confirmed and not cfg.hw_control_simulation
                and actual is not None and not matched):
            _LOGGER.warning("Hot water: heat pump reports setpoint %.1f °C instead of %.1f °C "
                            "– writing again", actual, setpoint_c)
            self._channel = WriteChannel()
        await self._writer.write(self._channel, spec, matched, cfg.hw_control_simulation,
                                 f"Boiler setpoint ({cfg.hw_target_entity})")

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _shutoff_c(self) -> float:
        """Shut-off temperature of plan, protection and ad-hoc charges."""
        return self._cfg.hw_morning_target_c

    def _impulse_c(self, mode: HWMode, goal_c: float, t: float | None) -> float | None:
        """Start-impulse setpoint: boiler + delta, at least goal + margin, capped at 65 °C.

        Only makes the heat pump switch to hot-water mode – the charge is ended
        by miniEMS at goal_c, never by the heat pump reaching this value.
        """
        if t is None:
            return None
        delta = (self._cfg.hw_adhoc_delta_k if mode is HWMode.MANUAL
                 else self._cfg.hw_boost_delta_k)
        return round(min(_SETPOINT_MAX_C, max(t + delta, goal_c + _PSEUDO_MARGIN_K)), 1)

    def _clear_adhoc(self) -> None:
        self._adhoc_since = None
        self._adhoc_target_c = None

    def _floor_c(self) -> float:
        return max(self._cfg.hw_day_target_c, self._cfg.hot_water_min_temp_c)

    def _value(self, entity: str) -> float | None:
        return self._ws.get_state_value(entity) if entity else None

    def _is_workday(self, day, today) -> bool:
        entity = self._cfg.hw_workday_entity
        if entity and day == today:
            raw = self._ws.get_state_raw(entity)
            if raw in ("on", "off"):
                return raw == "on"
        return is_workday(day)

    def _next_morning(self, now: datetime) -> datetime | None:
        hm = parse_hhmm(self._cfg.hw_morning_time)
        if hm is None:
            return None
        for offset in range(0, 8):
            day = now.date() + timedelta(days=offset)
            at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0) + timedelta(days=offset)
            if at > now and self._is_workday(day, now.date()):
                return at
        return None

    def _requirements(self, now: datetime, horizon_end: datetime) -> list[HWRequirement]:
        hm = parse_hhmm(self._cfg.hw_morning_time)
        if hm is None:
            return []
        reqs = []
        for offset in range(0, 3):
            day = now.date() + timedelta(days=offset)
            at = now.replace(hour=hm[0], minute=hm[1], second=0, microsecond=0) + timedelta(days=offset)
            if now < at <= horizon_end and self._is_workday(day, now.date()):
                reqs.append(HWRequirement(at, self._shutoff_c() - _MORNING_SLACK_K, "morning"))
        return reqs

    def _hw_slots(
        self,
        now: datetime,
        slots: "list[PlanSlot] | None",
        pv_known: bool,
        load_known: bool,
        price: float | None,
        battery: "BatteryContext | None" = None,
    ) -> list[HWSlot]:
        def temp_at(t: datetime) -> float | None:
            if self._weather is not None:
                v = self._weather.hourly_temp_at(t)
                if v is not None:
                    return v
            return self._outdoor_c

        if slots:
            # The battery only enters the price with a load forecast – without
            # one its projected state would be guesswork.
            bat = (project_battery([s.rate_eur_kwh for s in slots], [s.pv_kwh for s in slots],
                                   [s.load_kwh for s in slots], battery,
                                   feed_in_eur_kwh=self._cfg.feed_in_tariff_eur_kwh)
                   if battery is not None and load_known else None)
            return [
                HWSlot(
                    start=s.start, end=s.end, rate_eur_kwh=s.rate_eur_kwh,
                    pv_surplus_kwh=(max(0.0, s.pv_kwh - s.load_kwh)
                                    if pv_known and load_known else None),
                    outdoor_c=temp_at(s.start),
                    battery_full=bat[i].full if bat else None,
                    battery_has_energy=bat[i].has_energy if bat else None,
                    battery_blocked=bat[i].blocked if bat else False,
                    refill_eur_kwh=bat[i].refill_eur_kwh if bat else None,
                )
                for i, s in enumerate(slots)
            ]
        # No tariff calendar / forecast: flat price → heating lands just in time.
        step = timedelta(minutes=PLAN_SLOT_MIN)
        start = now.replace(second=0, microsecond=0)
        start -= timedelta(minutes=start.minute % PLAN_SLOT_MIN)
        out: list[HWSlot] = []
        a = now
        end = start + step
        while a < now + timedelta(hours=PLAN_HORIZON_H):
            out.append(HWSlot(start=a, end=end, rate_eur_kwh=price, pv_surplus_kwh=None,
                              outdoor_c=temp_at(a)))
            a, end = end, end + step
        return out

    async def _log(self, entry: LogEntry) -> None:
        if self._event_log is not None:
            try:
                await self._event_log.append(entry)
            except Exception as exc:
                _LOGGER.warning("Hot-water event log write failed: %s", exc)
