"""A6 – battery day plan by dynamic programming.

docs/roadmap/gesamtplan.md, "Akku-Tagesplan". The bridging plan answers "how
much do I buy now?", the hold (A4) "do I discharge now?". This plan looks at
the whole known horizon at once and picks, per 30-min slot, one action:

    AUTO    self-use: PV surplus charges, a deficit discharges
    HOLD    no discharge, PV surplus still charges (A4)
    CHARGE  grid charge at full power, no discharge
    EXPORT  PV surplus goes to the grid, the battery may discharge (PV hold)

    state    : stored energy (STEPS levels between min and max SoC) × last action
    cost     : Σ import × rate − export × feed-in + wear × discharged kWh
               + switch cost per change of action      (a calm plan)
               − residual value of the energy left at the end
                 (energy × percentile of the rates × η – otherwise the plan
                 would empty the battery at the horizon)
    feed-in  : export above the feed-in limit is lost, not paid
    solved   : backwards over the slots, ~72 × 201 × 4 × 4 evaluations

Planning styles scale the forecasts and the residual value – a simple,
understandable risk setting:

    careful   PV × 0.85, load × 1.10, residual P50
    balanced  PV × 1.00, load × 1.00, residual P25
    bold      PV × 1.05, load × 0.95, residual P10

Pure module. Guideline 1: the plan is recomputed, never stored and replayed
– only slot 0 is ever executed, the real-time layer corrects every tick.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge_planner import PlanSlot

# Levels between min and max SoC. 200 since v4.2.2 (was 100): rounding towards
# the start level leaves up to one level per slot unused – 0.1 kWh at 20 kWh
# instead of 0.2. ~0.16 s for 72 slots on a PC.
STEPS = 200
# Two choices within this many € count as equal (float residue of the level
# grid decided otherwise – 17.11.2026: hold vs. discharge, both 7.2417 €).
TIE_EUR = 0.001


class Action(str, Enum):
    AUTO = "auto"
    HOLD = "hold"
    CHARGE = "charge"
    EXPORT = "export"


_ACTIONS = (Action.AUTO, Action.HOLD, Action.CHARGE, Action.EXPORT)

STYLES: dict[str, tuple[float, float, float]] = {
    # (pv factor, load factor, residual percentile)
    "careful": (0.85, 1.10, 0.50),
    "balanced": (1.00, 1.00, 0.25),
    "bold": (1.05, 0.95, 0.10),
}


@dataclass(frozen=True)
class PlanStep:
    start: datetime
    end: datetime
    action: Action
    soc_kwh_end: float
    grid_charge_kwh: float
    import_kwh: float
    export_kwh: float
    rate_eur_kwh: float


@dataclass
class BatteryPlan:
    computed_at: datetime
    style: str
    steps: list[PlanStep] = field(default_factory=list)
    cost_eur: float = 0.0
    baseline_cost_eur: float = 0.0     # everything AUTO, same end valuation
    residual_eur_kwh: float = 0.0
    reason: str = ""

    @property
    def savings_eur(self) -> float:
        return self.baseline_cost_eur - self.cost_eur

    @property
    def first(self) -> PlanStep | None:
        return self.steps[0] if self.steps else None

    def as_dict(self) -> dict:
        return {
            "computed_at": self.computed_at.isoformat(),
            "style": self.style,
            "cost_eur": round(self.cost_eur, 4),
            "baseline_cost_eur": round(self.baseline_cost_eur, 4),
            "savings_eur": round(self.savings_eur, 4),
            "residual_eur_kwh": round(self.residual_eur_kwh, 5),
            "reason": self.reason,
            "steps": [
                {"start": s.start.isoformat(), "end": s.end.isoformat(), "action": s.action.value,
                 "soc_kwh": round(s.soc_kwh_end, 3), "grid_charge_kwh": round(s.grid_charge_kwh, 3),
                 "rate_eur_kwh": round(s.rate_eur_kwh, 5)}
                for s in self.steps
            ],
        }


def _percentile(values: list[float], q: float) -> float:
    s = sorted(values)
    return s[min(len(s) - 1, max(0, int(q * (len(s) - 1) + 0.5)))]


def plan_battery(
    now: datetime,
    slots: "list[PlanSlot]",
    stored_kwh: float,
    capacity_kwh: float,
    charge_kw: float,
    discharge_kw: float,
    eta_charge: float,
    eta_discharge: float,
    feed_in_eur_kwh: float,
    wear_eur_kwh: float = 0.0,
    switch_cost_eur: float = 0.01,
    feed_in_limit_kw: float = 0.0,
    style: str = "careful",
    last_action: Action = Action.AUTO,
) -> BatteryPlan:
    """Plan the slots. `stored_kwh` / `capacity_kwh` are measured above min SoC."""
    plan = BatteryPlan(computed_at=now, style=style)
    rated = [s for s in slots if s.rate_eur_kwh is not None]
    if not rated or capacity_kwh <= 0.1 or charge_kw <= 0 or discharge_kw <= 0:
        plan.reason = "missing input (rates, capacity or power)"
        return plan
    # The plan ends where the known prices end.
    slots = slots[: next((i for i, s in enumerate(slots) if s.rate_eur_kwh is None), len(slots))]
    pv_f, load_f, q = STYLES.get(style, STYLES["careful"])
    residual = _percentile([s.rate_eur_kwh for s in slots], q) * eta_discharge   # type: ignore[misc]
    plan.residual_eur_kwh = residual
    unit = capacity_kwh / STEPS
    n = len(slots)

    def step(level: int, a: Action, s: "PlanSlot") -> tuple[int, float, float, float, float]:
        """→ (new level, cost, grid charge kWh, import kWh, export kWh)."""
        h = s.hours
        e = level * unit
        room = capacity_kwh - e
        net = s.pv_kwh * pv_f - s.load_kwh * load_f
        chg_cap = charge_kw * h
        imp = exp = grid_chg = dis = 0.0
        if net >= 0:
            if a is Action.EXPORT:
                exp = net
            else:
                into = min(net, room / eta_charge, chg_cap)
                e += into * eta_charge
                exp = net - into
                if a is Action.CHARGE:
                    grid_chg = min(chg_cap - into, (capacity_kwh - e) / eta_charge)
                    e += grid_chg * eta_charge
                    imp = grid_chg
        else:
            deficit = -net
            if a in (Action.AUTO, Action.EXPORT):
                dis = min(deficit, e * eta_discharge, discharge_kw * h)
                e -= dis / eta_discharge
                imp = deficit - dis
            else:
                imp = deficit
                if a is Action.CHARGE:
                    grid_chg = min(chg_cap, room / eta_charge)
                    e += grid_chg * eta_charge
                    imp += grid_chg
        # Round towards the starting level, then recompute every flow from the
        # level actually reached. Rounding to the nearest level credited up to
        # half a level for free – a different amount in every slot. On
        # 17.11.2026 that alone moved the discharge from tonight's HOCH to
        # tomorrow's (same rate, 0.44 instead of 0.40 kWh for the same four
        # levels). Now every level stored or drawn is worth the same everywhere.
        x = e / unit
        if x >= level:
            new = min(STEPS, int(x + 1e-9))
        else:
            new = max(0, -int(-x + 1e-9))
        delta = (new - level) * unit
        if net >= 0:
            if a is not Action.EXPORT:
                pv_stored = min(into * eta_charge, delta)
                exp = net - pv_stored / eta_charge
                grid_chg = (delta - pv_stored) / eta_charge if a is Action.CHARGE else 0.0
                imp = grid_chg
        elif a in (Action.AUTO, Action.EXPORT):
            dis = -delta * eta_discharge
            imp = deficit - dis
        elif a is Action.CHARGE:
            grid_chg = delta / eta_charge
            imp = deficit + grid_chg
        if feed_in_limit_kw > 0:
            paid_exp = min(exp, feed_in_limit_kw * h)
        else:
            paid_exp = exp
        cost = imp * s.rate_eur_kwh - paid_exp * feed_in_eur_kwh + dis * wear_eur_kwh  # type: ignore[operator]
        return new, cost, grid_chg, imp, exp

    # value[l][a_prev] for the slot after the last one: residual value of the energy
    inf = float("inf")
    value = [[-(l * unit) * residual] * len(_ACTIONS) for l in range(STEPS + 1)]
    choice: list[list[list[int]]] = []
    transitions: list[list[list[tuple[int, float, float, float, float]]]] = []
    for t in range(n - 1, -1, -1):
        s = slots[t]
        trans = [[step(l, a, s) for a in _ACTIONS] for l in range(STEPS + 1)]
        new_value = [[inf] * len(_ACTIONS) for _ in range(STEPS + 1)]
        new_choice = [[0] * len(_ACTIONS) for _ in range(STEPS + 1)]
        for l in range(STEPS + 1):
            for pa in range(len(_ACTIONS)):
                totals = [trans[l][ai][1] + (switch_cost_eur if ai != pa else 0.0)
                          + value[trans[l][ai][0]][ai] for ai in range(len(_ACTIONS))]
                # Ties go to the first action in _ACTIONS order (AUTO): with two
                # equally dear windows, spend in the one that comes first.
                floor = min(totals) + TIE_EUR
                best_a = next(ai for ai, total in enumerate(totals) if total <= floor)
                new_value[l][pa] = totals[best_a]
                new_choice[l][pa] = best_a
        value = new_value
        choice.append(new_choice)
        transitions.append(trans)
    choice.reverse()
    transitions.reverse()

    level = min(STEPS, max(0, int(round(stored_kwh / unit))))
    prev = _ACTIONS.index(last_action)
    plan.cost_eur = value[level][prev]
    for t in range(n):
        ai = choice[t][level][prev]
        nl, _, gc, imp, exp = transitions[t][level][ai]
        s = slots[t]
        plan.steps.append(PlanStep(s.start, s.end, _ACTIONS[ai], nl * unit, gc, imp, exp,
                                   s.rate_eur_kwh))   # type: ignore[arg-type]
        level, prev = nl, ai

    # Baseline: AUTO throughout, same end valuation.
    level = min(STEPS, max(0, int(round(stored_kwh / unit))))
    base = 0.0
    for t in range(n):
        nl, cost, *_ = transitions[t][level][0]
        base += cost
        level = nl
    plan.baseline_cost_eur = base - level * unit * residual
    return plan
