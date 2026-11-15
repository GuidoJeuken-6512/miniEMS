"""Phase B wiring of EMSController (docs/roadmap/gesamtplan.md, section 5).

Kept out of ems_controller.py on purpose: the battery logic stays readable,
and every Phase B block enters it at three narrow points:

1. ``_update_phase_b_pre()`` – before the battery decision: planning slots,
   the fuse guard, the EV (B1–B5).
2. ``_couple_ev()`` – after the decision: while the EV charges, the battery
   does not discharge into it (B2 coupling rule, "block").
3. ``_phase_b_grid_charge_cap()`` – before the inverter write: the grid
   charge power within the fuse and §14a limits.

``_update_phase_b_post()`` then runs the heating (B6/B7) and the start
advice (B8). With every Phase B switch off, none of this changes a decision.
"""
from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from best_start import best_start, parse_devices
from bridge_planner import PlanSlot
from const import EMSMode, HWMode
from energy_task import SurplusState, TaskSlot, distribute_surplus
from heating_controller import HeatingInputs, PriceSlot
from heating_model import HeatSlot, HourAccumulator, RoomModel, fit_room_model, plan_heating
from price_classes import PriceClasses, classes_for
from resources import LoadDemand

if TYPE_CHECKING:
    from ev_controller import EVController
    from heating_controller import HeatingController
    from resources import ResourceCoordinator
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

_BEST_START_SEC = 900
_MODEL_FIT_SEC = 3600
_HEATING_PLAN_SEC = 900
_HEATING_HORIZON = timedelta(hours=24)
_HEAT_PUMP_HEATING_W = 300.0
_DEFAULT_HEATING_COP = 3.5


class PhaseBMixin:
    """Mixed into EMSController; uses its _cfg, _ws, _inverter, _mode, …"""

    def _init_phase_b(
        self,
        ev: "EVController | None",
        heating: "HeatingController | None",
        resources: "ResourceCoordinator | None",
        store: "EnergyStore | None",
    ) -> None:
        self._ev = ev
        self._heating = heating
        self._resources = resources
        self._b_store = store
        self._task_slots: list[TaskSlot] | None = None
        self._caps: dict[str, float | None] = {}
        self._grid_charge_applied_w: float | None = None
        self._actuator_lock = asyncio.Lock()
        self._best_start: list[dict] = []
        self._best_start_at: datetime | None = None
        self._room_acc = HourAccumulator()
        self._room_model: RoomModel | None = None
        self._room_model_at: datetime | None = None
        self._heating_plan = None
        self._heating_plan_at: datetime | None = None
        self._heating_low = False
        self._class_cache: dict[Any, PriceClasses | None] = {}
        self._surplus_share = None
        self._last_readings: dict[str, float | None] = {}

    # ------------------------------------------------------------------
    # Slots for the distributor (B2/B4/B8)
    # ------------------------------------------------------------------

    def _phase_b_wants_slots(self) -> bool:
        cfg = self._cfg
        return bool((self._ev is not None and self._ev.configured) or cfg.flex_devices
                    or (self._heating is not None and self._heating.controlling))

    def _fuse_limit_w(self) -> float | None:
        guard = self._resources.guard if self._resources is not None else None
        if guard is None:
            return None
        return guard.work_limit_a * guard.voltage_v * 3

    def _build_task_slots(self, now: datetime, price: float | None,
                          pv_w: float | None) -> list[TaskSlot] | None:
        slots, _, _, _ = self._plan_slots(now, price, pv_w, need_pv=False, need_load=False)
        if not slots:
            return None
        limit = self._fuse_limit_w()
        feed_in = 0.0 if self._export_unpaid(price) else self._cfg.feed_in_tariff_eur_kwh
        out = []
        for s in slots:
            if s.rate_eur_kwh is None or s.hours <= 0:
                continue
            load_w = s.load_kwh * 1000.0 / s.hours
            pv = s.pv_kwh * 1000.0 / s.hours
            out.append(TaskSlot(s.start, s.end, s.rate_eur_kwh, base_load_w=load_w,
                                pv_surplus_w=max(0.0, pv - load_w), feed_in_eur_kwh=feed_in,
                                limit_w=limit))
        return out or None

    def _with_planned_loads(self, slots: list[PlanSlot]) -> list[PlanSlot]:
        """Hot-water plan (live) and EV plan as load per slot – A6 sees both."""
        hw = self._hot_water
        ev = getattr(self, "_ev", None)
        if hw is not None and hw.couples:
            slots = [
                PlanSlot(s.start, s.end, s.rate_eur_kwh,
                         max(0.0, s.load_kwh + hw.load_delta_kwh(s.end, s.hours)), s.pv_kwh)
                for s in slots
            ]
        if ev is not None and ev.controlling:
            slots = [
                PlanSlot(s.start, s.end, s.rate_eur_kwh,
                         s.load_kwh + ev.extra_load_kwh(s.start, s.end), s.pv_kwh)
                for s in slots
            ]
        return slots

    # ------------------------------------------------------------------
    # Before the decision
    # ------------------------------------------------------------------

    def _grid_charge_own_w(self, pv_w: float | None, load_w: float | None,
                           bat_w: float | None) -> float:
        """Grid share of the battery charge now (battery_power: + = discharge)."""
        if self._mode is not EMSMode.GRID_CHARGING or bat_w is None or bat_w >= 0:
            return 0.0
        surplus = max(0.0, (pv_w or 0.0) - (load_w or 0.0))
        return max(0.0, -bat_w - surplus)

    def _grid_charge_want_w(self) -> float:
        cfg = self._cfg
        voltage = (self._ws.get_state_value(cfg.battery_voltage_entity)
                   if cfg.battery_voltage_entity else None) or 48.0
        return cfg.battery_max_charge_current_a * voltage

    def _loads_in_order(self, now: datetime, pv_w, load_w, bat_w) -> list[LoadDemand]:
        """Controllable grid loads, tightest deadline first (B2)."""
        loads: list[tuple[datetime, LoadDemand]] = []
        if self._cfg.battery_control_enabled:
            loads.append((self._plan_deadline(now), LoadDemand(
                "grid_charge", self._grid_charge_own_w(pv_w, load_w, bat_w),
                self._grid_charge_want_w(), 3)))
        ev = self._ev
        if ev is not None and ev.configured:
            plan = ev.plan
            deadline = plan.deadline if plan is not None else ev.target(now).departure
            phases = max(1, int(self._cfg.ev_phases_max))
            loads.append((deadline, LoadDemand("ev", ev.power_w, ev.max_w(), phases)))
        loads.sort(key=lambda x: x[0])
        return [ld for _, ld in loads]

    async def _update_phase_b_pre(self, now: datetime, price: float | None,
                                  pv_w: float | None, load_w: float | None,
                                  grid_w: float | None, bat_w: float | None,
                                  bat_soc: float | None) -> None:
        self._last_readings = {"pv_w": pv_w, "load_w": load_w, "grid_w": grid_w, "bat_w": bat_w}
        self._task_slots = (self._build_task_slots(now, price, pv_w)
                            if self._phase_b_wants_slots() else None)
        t = time.monotonic()
        res = self._resources
        if res is not None and res.active:
            controlled = self._grid_charge_own_w(pv_w, load_w, bat_w)
            if self._ev is not None:
                controlled += self._ev.power_w
            res.tick(t, controlled)
            self._caps = res.caps(t, self._loads_in_order(now, pv_w, load_w, bat_w))
        else:
            self._caps = {}

        ev = self._ev
        if ev is not None and ev.configured:
            cfg = self._cfg
            yield_w = 0.0
            if (cfg.charge_priority == "ev"
                    or (bat_soc is not None and bat_soc >= cfg.battery_priority_soc_pct)):
                # The battery yields its PV charge to the car.
                yield_w = max(0.0, -(bat_w or 0.0)) if self._mode is not EMSMode.GRID_CHARGING else 0.0
            tier = self._optimizer.price_tier(price)
            expensive = grid_w is not None and grid_w > 100 and tier in ("medium", "high")
            async with self._actuator_lock:
                await ev.update(now, slots=self._task_slots, grid_w=grid_w,
                                yield_battery_w=yield_w, expensive_import=expensive,
                                cap_w=self._caps.get("ev"), t=t)
            bat_max_w = self._grid_charge_want_w()
            self._surplus_share = distribute_surplus(SurplusState(
                surplus_w=(pv_w or 0.0) - (load_w or 0.0),
                battery_soc=bat_soc,
                battery_priority_soc=cfg.battery_priority_soc_pct,
                battery_max_charge_w=bat_max_w,
                battery_full=bat_soc is not None and bat_soc >= cfg.battery_max_soc,
                ev_min_w=ev.min_w(), ev_max_w=ev.max_w(),
                ev_wants=ev.mode.value in ("auto", "pv"),
                charge_priority=cfg.charge_priority,
            ))

    # ------------------------------------------------------------------
    # Coupling rule (B2)
    # ------------------------------------------------------------------

    def _couple_ev(self, decision):
        """While the EV charges, the battery must not discharge into it."""
        ev = self._ev
        cfg = self._cfg
        if (ev is None or not ev.controlling or cfg.ev_battery_mode != "block"
                or not ev.charging):
            return decision
        if decision.mode in (EMSMode.GRID_CHARGING, EMSMode.PROTECT_BATTERY,
                             EMSMode.HOLD_FOR_PEAK):
            return decision
        # Urgent: waiting out mode_dwell_sec would let the battery feed the
        # car for minutes – exactly what the rule is there to prevent. Leaving
        # the hold again goes through the normal dwell time.
        return type(decision)(EMSMode.HOLD_FOR_PEAK,
                              f"ev charging: battery does not discharge into the car "
                              f"(was: {decision.reason})", urgent=True)

    # ------------------------------------------------------------------
    # Before the inverter write (B1)
    # ------------------------------------------------------------------

    def _phase_b_grid_charge_cap(self, grid_charge_power_w: float | None) -> float | None:
        cap = self._caps.get("grid_charge")
        if cap is None or self._mode is not EMSMode.GRID_CHARGING:
            return grid_charge_power_w
        want = grid_charge_power_w if grid_charge_power_w is not None else self._grid_charge_want_w()
        if cap < want:
            _LOGGER.info("Fuse guard: grid charge limited to %.0f W (wanted %.0f W)", cap, want)
            return max(0.0, cap)
        return grid_charge_power_w

    async def fast_tick(self) -> None:
        """B1: phase check every fuse_poll_sec while a large load runs.

        The 30-s loop is too slow for an overload; this re-applies the caps
        at once when the guard throttles.
        """
        res = self._resources
        if res is None or res.guard is None:
            return
        r = self._last_readings
        ev = self._ev
        grid_running = self._mode is EMSMode.GRID_CHARGING
        ev_running = ev is not None and ev.charging
        if not (grid_running or ev_running):
            return
        now = datetime.now().astimezone()
        t = time.monotonic()
        controlled = self._grid_charge_own_w(r.get("pv_w"), r.get("load_w"), r.get("bat_w"))
        if ev is not None:
            controlled += ev.power_w
        res.tick(t, controlled)
        status = res.guard.status(t)
        if not (status.throttling or status.tripped):
            return
        caps = res.caps(t, self._loads_in_order(now, r.get("pv_w"), r.get("load_w"), r.get("bat_w")))
        self._caps = caps
        async with self._actuator_lock:
            if grid_running and self._inverter is not None and caps.get("grid_charge") is not None:
                await self._inverter.apply_mode(EMSMode.GRID_CHARGING,
                                                self._phase_b_grid_charge_cap(None))
            if ev_running and ev.controlling and caps.get("ev") is not None:
                await ev.update(now, slots=self._task_slots, grid_w=r.get("grid_w"),
                                cap_w=caps.get("ev"), t=t)
        _LOGGER.warning("Fuse guard: %s – caps %s", status.reason,
                        {k: (None if v is None else round(v)) for k, v in caps.items()})

    # ------------------------------------------------------------------
    # After the decision: heating (B6/B7), start advice (B8)
    # ------------------------------------------------------------------

    def _classes(self, moment: datetime) -> PriceClasses | None:
        cfg = self._cfg
        key = moment.date()
        if key not in self._class_cache:
            if len(self._class_cache) > 4:
                self._class_cache.clear()
            self._class_cache[key] = classes_for(
                self._price_curve(), moment, cfg.price_cheap_hours, cfg.price_hysteresis_min_eur,
                cfg.price_hysteresis_max_eur, cfg.price_flat_spread_eur)
        return self._class_cache[key]

    def _heating_tier(self, now: datetime, price: float | None) -> str | None:
        """A1 class with the hysteresis meant for slow actuators."""
        classes = self._classes(now)
        if classes is None or price is None:
            return self._optimizer.price_tier(price)
        tier = classes.tier(price)
        if (tier != "low" and self._heating_low and not classes.flat
                and price <= classes.limit_eur + classes.hysteresis_eur):
            tier = "low"
        self._heating_low = tier == "low"
        return tier

    def _price_slots(self, now: datetime, horizon: timedelta) -> list[PriceSlot]:
        out = []
        for s in self._task_slots or []:
            if s.start >= now + horizon:
                break
            classes = self._classes(s.start)
            tier = classes.tier(s.price_eur_kwh) if classes is not None else None
            out.append(PriceSlot(s.start, s.end, s.price_eur_kwh, tier))
        return out

    async def _update_phase_b_post(self, now: datetime, price: float | None,
                                   grid_w: float | None) -> None:
        cfg = self._cfg
        heating = self._heating
        if heating is not None and heating.configured and cfg.heating_control_enabled:
            await self._update_room_model(now)
            tier = self._heating_tier(now, price)
            slots = self._price_slots(now, timedelta(hours=6))
            pv_ahead = sum(max(0.0, s.pv_surplus_w) * s.hours / 1000.0
                           for s in (self._task_slots or []) if s.start < now + timedelta(hours=12))
            plan = self._heating_plan if cfg.heating_room_control_enabled else None
            async with self._actuator_lock:
                await heating.update(HeatingInputs(
                    now=now, price_eur_kwh=price, tier=tier, slots=slots,
                    outdoor_c=self._outdoor_temp_c(now),
                    pv_ahead_kwh=pv_ahead if self._task_slots else None,
                    grid_w=grid_w, plan=plan))
        if cfg.flex_devices:
            self._update_best_start(now)

    async def _update_room_model(self, now: datetime) -> None:
        """B7: record the room hour, refit hourly, re-plan every 15 min."""
        cfg = self._cfg
        heating = self._heating
        device = heating._device
        room = device.room_temp_c()
        power = device.power_w()
        compressor = device.compressor_running()
        hw_busy = (self._hot_water is not None and self._hot_water.enabled
                   and self._hot_water.mode in (HWMode.CHARGING, HWMode.PV,
                                                HWMode.PROTECT, HWMode.MANUAL))
        heats = (not hw_busy) and (
            (power is not None and power > _HEAT_PUMP_HEATING_W) if power is not None
            else bool(compressor))
        done = self._room_acc.add(now, room, heats, heating.offset_k)
        store = self._b_store
        if done is not None and store is not None:
            hour_start = done.pop("hour_start")
            await store.upsert_hour(hour_start, done)
        if store is None:
            return
        if (self._room_model_at is None
                or (now - self._room_model_at).total_seconds() >= _MODEL_FIT_SEC):
            self._room_model_at = now
            rows = await store.query_hours_since((now - timedelta(days=30)).date())
            self._room_model = await asyncio.to_thread(fit_room_model, rows)
        model = self._room_model
        if (model is None or room is None or cfg.heating_comfort_min_c <= 0
                or not self._task_slots):
            self._heating_plan = None
            return
        if (self._heating_plan_at is not None and self._heating_plan is not None
                and (now - self._heating_plan_at).total_seconds() < _HEATING_PLAN_SEC):
            return
        self._heating_plan_at = now
        slots = []
        for s in self._task_slots:
            if s.start >= now + _HEATING_HORIZON:
                break
            outdoor = (self._weather.hourly_temp_at(s.start) if self._weather is not None else None)
            if outdoor is None:
                outdoor = self._outdoor_temp_c(now)
            if outdoor is None:
                break
            classes = self._classes(s.start)
            tier = classes.tier(s.price_eur_kwh) if classes is not None else None
            slots.append(HeatSlot(s.start, s.end, s.price_eur_kwh, tier, outdoor,
                                  pv_kwh=s.pv_surplus_w * s.hours / 1000.0,
                                  cop=_DEFAULT_HEATING_COP))
        self._heating_plan = plan_heating(
            model, slots, t_now=room, comfort_min_c=cfg.heating_comfort_min_c,
            setpoint_c=cfg.heating_room_setpoint_c, up_k=cfg.heating_offset_up_k,
            down_k=cfg.heating_offset_down_k) if slots else None

    def _update_best_start(self, now: datetime) -> None:
        if (self._best_start_at is not None
                and (now - self._best_start_at).total_seconds() < _BEST_START_SEC):
            return
        self._best_start_at = now
        slots = self._task_slots
        out = []
        for dev in parse_devices(self._cfg.flex_devices):
            advice = best_start(dev, slots or [], now)
            out.append(advice.as_dict() if advice is not None
                       else {"name": dev.name, "slug": dev.slug, "start": None,
                             "reason": "no prices for the run"})
        self._best_start = out

    # ------------------------------------------------------------------
    # Status, failsafe, write events
    # ------------------------------------------------------------------

    def _phase_b_status(self, now: datetime) -> dict[str, Any]:
        out: dict[str, Any] = {}
        if self._resources is not None and self._resources.active:
            out["fuse"] = self._resources.status(time.monotonic())
        if self._ev is not None and self._ev.configured:
            out["ev"] = self._ev.status(now)
            if self._surplus_share is not None:
                out["surplus_share"] = self._surplus_share.as_dict()
        if self._heating is not None and self._heating.configured and self._cfg.heating_control_enabled:
            out["heating"] = self._heating.status(now)
            out["heating"]["model"] = self._room_model.as_dict() if self._room_model else None
            out["heating"]["plan"] = self._heating_plan.as_dict() if self._heating_plan else None
        if self._cfg.flex_devices:
            out["best_start"] = self._best_start
        return out

    def _phase_b_failsafe(self) -> list[dict]:
        actions: list[dict] = []
        for ctrl in (self._ev, self._heating):
            if ctrl is not None:
                actions += ctrl.failsafe_actions()
        return actions

    def _phase_b_write_events(self) -> list[dict]:
        events: list[dict] = []
        for ctrl in (self._ev, self._heating):
            if ctrl is not None:
                events += ctrl.pop_write_events()
        return events

    def _phase_b_warnings(self) -> list[str]:
        cfg = self._cfg
        warnings = []
        if cfg.fuse_guard_enabled and (self._resources is None or self._resources.guard is None):
            warnings.append("Fuse guard enabled but not active: set fuse_rated_a and three "
                            "phase_current_entities")
        ev = self._ev
        if ev is not None and ev.controlling and ev.plan is not None and not ev.plan.feasible:
            warnings.append(f"EV: {ev.plan.reason}")
        if cfg.ev_control_enabled and (ev is None or not ev.configured):
            warnings.append("EV control enabled but no wallbox entities configured "
                            "(ev_charge_power_entity / ev_plugged_entity)")
        if cfg.heating_control_enabled and (self._heating is None or not self._heating.configured):
            warnings.append("Heating control enabled but heating_offset_entity is empty")
        return warnings
