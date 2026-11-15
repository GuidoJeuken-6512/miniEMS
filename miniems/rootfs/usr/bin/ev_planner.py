"""B4 – EV charge planning (docs/roadmap/gesamtplan.md, B4).

Turns "SoC now, target, departure" into energy tasks for the distributor
(energy_task.plan_tasks):

- **Energy:** (target − SoC) × capacity × CHARGE_LOSS_FACTOR. Without a SoC
  reading UNKNOWN_SOC_PCT is assumed; the car stops by itself when full.
- **Deadline:** the departure, one hour earlier for a 100 % target – the
  margin against throttling by the fuse or the heating.
- **Two sub-targets:** first a minimum SoC by the morning time, then the rest
  by departure. The morning part is a "must" task.
- **Slot score:** effective price plus LOW_POWER_PENALTY × (P_max / P − 1),
  because charging at 6 A has a poor efficiency.
- **Power per slot:** fuse working limit minus the forecast base load (the
  load model already depends on the temperature). Two passes: first with a
  reserve, then – for the "must" part – without it, never above the fuse.

Pure: everything is passed in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

from energy_task import EnergyTask, TaskPlan, TaskSlot, plan_tasks

CHARGE_LOSS_FACTOR = 1.12
UNKNOWN_SOC_PCT = 20.0
LOW_POWER_PENALTY_EUR_KWH = 0.10
FULL_TARGET_MARGIN = timedelta(hours=1)
DEFAULT_RESERVE_W = 1500.0


@dataclass(frozen=True)
class EVTarget:
    soc_pct: float | None          # None = unknown
    target_pct: float
    capacity_kwh: float
    departure: datetime
    morning_pct: float = 0.0       # 0 = no morning sub-target
    morning_at: datetime | None = None
    charged_kwh: float = 0.0       # this session, used when the SoC is unknown


@dataclass
class EVPlan:
    need_kwh: float
    deadline: datetime
    power_w: list[float]
    slots: list[TaskSlot]
    feasible: bool
    morning_ok: bool
    shortfall_kwh: float
    cost_eur: float
    reason: str
    soc_assumed: bool = False
    parts: dict[str, TaskPlan] = field(default_factory=dict)

    def power_at(self, moment: datetime) -> float:
        """Planned power for the slot containing ``moment`` (0 outside)."""
        for s, p in zip(self.slots, self.power_w):
            if s.start <= moment < s.end:
                return p
        return 0.0

    def as_dict(self) -> dict:
        return {
            "need_kwh": round(self.need_kwh, 2),
            "deadline": self.deadline.isoformat(),
            "feasible": self.feasible,
            "morning_ok": self.morning_ok,
            "shortfall_kwh": round(self.shortfall_kwh, 2),
            "cost_eur": round(self.cost_eur, 3),
            "reason": self.reason,
            "soc_assumed": self.soc_assumed,
            "slots": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "power_w": round(p)}
                for s, p in zip(self.slots, self.power_w) if p > 0
            ],
        }


def energy_need_kwh(soc_pct: float | None, target_pct: float, capacity_kwh: float,
                    charged_kwh: float = 0.0) -> tuple[float, bool]:
    """(kWh to buy incl. losses, SoC was assumed)."""
    assumed = soc_pct is None
    soc = UNKNOWN_SOC_PCT if assumed else soc_pct
    need = max(0.0, target_pct - soc) / 100.0 * capacity_kwh * CHARGE_LOSS_FACTOR
    if assumed:
        need = max(0.0, need - charged_kwh)
    return need, assumed


def deadline_for(target: EVTarget) -> datetime:
    if target.target_pct >= 100.0:
        return target.departure - FULL_TARGET_MARGIN
    return target.departure


def plan_ev(
    target: EVTarget,
    slots: list[TaskSlot],
    *,
    min_w: float,
    max_w: float,
    now: datetime,
    reserve_w: float = DEFAULT_RESERVE_W,
) -> EVPlan:
    """Plan the EV's charge over ``slots`` (fuse limit and base load inside)."""
    deadline = deadline_for(target)
    need, assumed = energy_need_kwh(target.soc_pct, target.target_pct,
                                    target.capacity_kwh, target.charged_kwh)
    slots = [s for s in slots if s.end > now and s.start < deadline]
    if need <= 0.05:
        return EVPlan(0.0, deadline, [0.0] * len(slots), slots, True, True, 0.0, 0.0,
                      "target reached", assumed)
    if not slots:
        return EVPlan(need, deadline, [], [], False, False, need, 0.0,
                      "no slots before the deadline", assumed)

    tasks: list[EnergyTask] = []
    morning_need = 0.0
    if (target.morning_pct > 0 and target.morning_at is not None
            and now < target.morning_at < deadline):
        morning_need, _ = energy_need_kwh(target.soc_pct, min(target.morning_pct, target.target_pct),
                                          target.capacity_kwh, target.charged_kwh)
        morning_need = min(morning_need, need)
    if morning_need > 0.05:
        tasks.append(EnergyTask("morning", morning_need, min_w, max_w, target.morning_at,
                                kind="must",
                                low_power_penalty_eur_kwh=LOW_POWER_PENALTY_EUR_KWH))
    rest = need - (morning_need if morning_need > 0.05 else 0.0)
    if rest > 0.05:
        tasks.append(EnergyTask("rest", rest, min_w, max_w, deadline, kind="must",
                                low_power_penalty_eur_kwh=LOW_POWER_PENALTY_EUR_KWH))

    parts = plan_tasks(tasks, slots, reserve_w=reserve_w)
    power = [0.0] * len(slots)
    for part in parts.values():
        for i, p in enumerate(part.power_w):
            power[i] += p
    shortfall = sum(p.shortfall_kwh for p in parts.values())
    cost = sum(p.cost_eur for p in parts.values())
    morning_ok = parts["morning"].feasible if "morning" in parts else True
    feasible = all(p.feasible for p in parts.values())
    if feasible:
        reason = f"{need:.1f} kWh by {deadline:%H:%M}"
    elif not morning_ok:
        reason = (f"target not reachable: morning minimum {target.morning_pct:.0f} % "
                  f"by {target.morning_at:%H:%M} misses {parts['morning'].shortfall_kwh:.1f} kWh")
    else:
        reason = f"target not reachable: {shortfall:.1f} kWh missing by {deadline:%H:%M}"
    return EVPlan(need, deadline, power, slots, feasible, morning_ok, shortfall, cost,
                  reason, assumed, parts)


def next_time_of_day(now: datetime, hh: int, mm: int) -> datetime:
    """The next occurrence of HH:MM after ``now`` (same tz)."""
    t = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
    return t if t > now else t + timedelta(days=1)
