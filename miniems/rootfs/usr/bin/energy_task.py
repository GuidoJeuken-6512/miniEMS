"""B2 – energy tasks and the resource distributor (docs/roadmap/gesamtplan.md, B2).

``EnergyTask`` replaces the Energiefahrplan's ``ChargeTask``: one device, an
energy need, power limits, a deadline and how flexible it is. Two pure
functions work on it:

- ``plan_tasks()`` – planning layer. Tasks are served by deadline (the
  tightest first); each takes the cheapest slots before its deadline, within
  the power the main fuse leaves after the forecast base load and the tasks
  already placed. The battery day plan (A6) then sees the planned powers as
  load per slot.
- ``distribute_surplus()`` – real-time layer, rank 7 of the rule order:
  battery up to the priority SoC → EV → hot water → rest of the battery →
  export.

Coupling rules (EnergyWizard ``battery_control.py`` rules 5–6) are the
caller's: while the EV charges, the battery must not discharge into it.

Pure: no Home Assistant access, everything is passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime


@dataclass(frozen=True)
class EnergyTask:
    """One device's job. Power in W, energy in kWh."""

    name: str
    energy_kwh: float
    min_w: float
    max_w: float
    deadline: datetime
    kind: str = "flexible"            # "must" | "flexible"
    interruptible: bool = True
    min_run_slots: int = 1
    # Extra cost per kWh when running below max power (low-current losses):
    # penalty × (max_w / power − 1), see B4.
    low_power_penalty_eur_kwh: float = 0.0


@dataclass(frozen=True)
class TaskSlot:
    """One planning slot as the distributor sees it."""

    start: datetime
    end: datetime
    price_eur_kwh: float              # effective price of grid energy
    base_load_w: float = 0.0          # forecast uncontrolled load
    pv_surplus_w: float = 0.0         # PV left after the base load (≥ 0)
    feed_in_eur_kwh: float = 0.0      # value of PV that would be exported
    limit_w: float | None = None      # grid power the fuse allows in total

    @property
    def hours(self) -> float:
        return max(0.0, (self.end - self.start).total_seconds() / 3600.0)


@dataclass
class TaskPlan:
    name: str
    power_w: list[float]
    energy_kwh: float
    shortfall_kwh: float
    cost_eur: float
    feasible: bool
    reason: str = ""

    def as_dict(self, slots: list[TaskSlot]) -> dict:
        return {
            "name": self.name,
            "energy_kwh": round(self.energy_kwh, 3),
            "shortfall_kwh": round(self.shortfall_kwh, 3),
            "cost_eur": round(self.cost_eur, 4),
            "feasible": self.feasible,
            "reason": self.reason,
            "slots": [
                {"start": s.start.isoformat(), "power_w": round(p)}
                for s, p in zip(slots, self.power_w) if p > 0
            ],
        }


def effective_price(slot: TaskSlot, power_w: float, pv_left_w: float) -> float:
    """€/kWh of running ``power_w`` in this slot: PV share at the feed-in
    value, the rest at the grid price."""
    if power_w <= 0:
        return slot.price_eur_kwh
    pv_w = min(power_w, max(0.0, pv_left_w))
    return (pv_w * slot.feed_in_eur_kwh + (power_w - pv_w) * slot.price_eur_kwh) / power_w


def plan_tasks(
    tasks: list[EnergyTask],
    slots: list[TaskSlot],
    *,
    reserve_w: float = 0.0,
) -> dict[str, TaskPlan]:
    """Place every task into the cheapest slots before its deadline.

    ``reserve_w`` is kept free below the fuse limit in every slot. A "must"
    task that does not fit with the reserve is placed a second time without
    it (never above the fuse limit).
    """
    used_w = [0.0] * len(slots)
    pv_left = [max(0.0, s.pv_surplus_w) for s in slots]
    out: dict[str, TaskPlan] = {}
    for task in sorted(tasks, key=lambda t: (t.deadline, t.name)):
        plan = _place(task, slots, used_w, pv_left, reserve_w)
        if not plan.feasible and task.kind == "must" and reserve_w > 0:
            plan = _place(task, slots, used_w, pv_left, 0.0, already=plan)
        for i, p in enumerate(plan.power_w):
            used_w[i] += p
            pv_left[i] = max(0.0, pv_left[i] - p)
        out[task.name] = plan
    return out


def _room_w(slot: TaskSlot, used_w: float, reserve_w: float) -> float:
    if slot.limit_w is None:
        return float("inf")
    return slot.limit_w - slot.base_load_w - used_w - reserve_w


def _place(task: EnergyTask, slots: list[TaskSlot], used_w: list[float],
           pv_left: list[float], reserve_w: float,
           already: TaskPlan | None = None) -> TaskPlan:
    power = list(already.power_w) if already is not None else [0.0] * len(slots)
    need = task.energy_kwh - (already.energy_kwh if already is not None else 0.0)
    eligible = [i for i, s in enumerate(slots) if s.end <= task.deadline and s.hours > 0]

    def avail(i: int) -> float:
        room = _room_w(slots[i], used_w[i] + power[i], reserve_w)
        return max(0.0, min(task.max_w - power[i], room))

    def score(i: int) -> float:
        p = avail(i) + power[i]
        if p < task.min_w or p <= 0:
            return float("inf")
        price = effective_price(slots[i], p, pv_left[i])
        if task.low_power_penalty_eur_kwh > 0 and p < task.max_w:
            price += task.low_power_penalty_eur_kwh * (task.max_w / p - 1.0)
        return price

    if not task.interruptible and need > 0:
        need = _place_block(task, slots, eligible, power, need, avail, score)
    else:
        for i in sorted(eligible, key=lambda i: (score(i), slots[i].start)):
            if need <= 1e-6:
                break
            a = avail(i)
            if a + power[i] < task.min_w or score(i) == float("inf"):
                continue
            hours = slots[i].hours
            want_w = need * 1000.0 / hours
            add = min(a, max(want_w, task.min_w - power[i]))
            power[i] += add
            need -= add * hours / 1000.0

    energy = sum(p * s.hours for p, s in zip(power, slots)) / 1000.0
    cost = 0.0
    for i, (p, s) in enumerate(zip(power, slots)):
        if p > 0:
            cost += effective_price(s, p, pv_left[i]) * p * s.hours / 1000.0
    shortfall = max(0.0, task.energy_kwh - energy)
    feasible = shortfall <= 0.01 * max(1.0, task.energy_kwh)
    reason = "ok" if feasible else f"{shortfall:.1f} kWh do not fit before {task.deadline:%H:%M}"
    return TaskPlan(task.name, power, energy, shortfall, cost, feasible, reason)


def _place_block(task, slots, eligible, power, need, avail, score) -> float:
    """Non-interruptible: the cheapest run of consecutive slots that covers ``need``."""
    best: tuple[float, list[int]] | None = None
    for a in range(len(eligible)):
        run: list[int] = []
        energy = 0.0
        cost = 0.0
        for idx in eligible[a:]:
            if run and slots[idx].start != slots[run[-1]].end:
                break
            p = avail(idx) + power[idx]
            if p < task.min_w:
                break
            run.append(idx)
            take = min(p, max(task.min_w, (need - energy) * 1000.0 / slots[idx].hours))
            energy += take * slots[idx].hours / 1000.0
            cost += score(idx) * take * slots[idx].hours / 1000.0
            if energy >= need - 1e-6:
                break
        if energy >= need - 1e-6 and len(run) >= task.min_run_slots:
            if best is None or cost < best[0]:
                best = (cost, run)
    if best is None:
        return need
    for idx in best[1]:
        p = avail(idx) + power[idx]
        take = min(p, max(task.min_w, need * 1000.0 / slots[idx].hours))
        power[idx] = max(power[idx], take)
        need -= take * slots[idx].hours / 1000.0
    return max(0.0, need)


# ----------------------------------------------------------------------
# Real time (rank 7)
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class SurplusState:
    """What the distributor needs to know this tick. Power in W."""

    surplus_w: float                  # PV − load, before battery and EV (may be < 0)
    battery_soc: float | None
    battery_priority_soc: float       # below it the battery comes first
    battery_max_charge_w: float
    battery_full: bool
    ev_min_w: float = 0.0
    ev_max_w: float = 0.0
    ev_wants: bool = False            # plugged in, mode allows PV charging
    charge_priority: str = "battery"  # "battery" | "ev"


@dataclass
class SurplusShare:
    battery_w: float = 0.0
    ev_w: float = 0.0
    hot_water_w: float = 0.0
    export_w: float = 0.0
    order: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {
            "battery_w": round(self.battery_w), "ev_w": round(self.ev_w),
            "hot_water_w": round(self.hot_water_w), "export_w": round(self.export_w),
            "order": self.order,
        }


def surplus_order(state: SurplusState) -> list[str]:
    """Rank 7: who gets the PV surplus first."""
    below_priority = (state.battery_soc is not None
                      and state.battery_soc < state.battery_priority_soc)
    if state.charge_priority == "ev":
        return ["ev", "hot_water", "battery", "export"]
    if below_priority:
        return ["battery_priority", "ev", "hot_water", "battery", "export"]
    return ["ev", "hot_water", "battery", "export"]


def distribute_surplus(state: SurplusState, hot_water_w: float = 0.0) -> SurplusShare:
    """Split the PV surplus. ``hot_water_w`` – what a running PV hot-water
    charge takes (it is not modulated by miniEMS, only accounted)."""
    left = max(0.0, state.surplus_w)
    share = SurplusShare(order=surplus_order(state))
    bat_room = 0.0 if state.battery_full else max(0.0, state.battery_max_charge_w)
    for who in share.order:
        if left <= 0:
            break
        if who in ("battery_priority", "battery"):
            take = min(left, bat_room)
            share.battery_w += take
            bat_room -= take
            left -= take
        elif who == "ev":
            if not state.ev_wants or left < state.ev_min_w:
                continue
            take = min(left, state.ev_max_w)
            share.ev_w = take
            left -= take
        elif who == "hot_water":
            take = min(left, max(0.0, hot_water_w))
            share.hot_water_w = take
            left -= take
        elif who == "export":
            share.export_w = left
            left = 0.0
    return share
