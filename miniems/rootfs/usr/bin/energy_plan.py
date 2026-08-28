"""Der Energiefahrplan – proaktive Tagesbilanz für die kommende Nacht.

See docs/roadmap/energiefahrplan.md, "Der Energiefahrplan — Bausteine zu
einer Tagesbilanz zusammenführen". This answers, explicitly and ahead of
time, the question EMSController._should_grid_charge()'s reactive
tomorrow-fallback branch only answers implicitly, late (only once today's
forecast is already exhausted) and invisibly (nowhere in the dashboard):
how much energy must come from the grid tonight so the battery survives
until tomorrow's PV takes over, and in which windows would that be
cheapest?

Display-only: this module computes a plan for the dashboard. It does NOT
feed back into the actual charge decision, which stays the tick-level
reactive logic in ems_controller.py (_should_grid_charge,
_should_hold_pv_charge, _grid_charge_current_a) – see the roadmap doc's
"Trigger" section for why a computed plan is not the same thing as a stored
one: recomputed hourly, never persisted, replaces only the *display*.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from price_curve import PriceCurve


@dataclass(frozen=True)
class PlannedWindow:
    """One grid-charge window the plan would use."""
    start: datetime
    end: datetime
    rate_eur_kwh: float
    energy_kwh: float

    @property
    def cost_eur(self) -> float:
        return self.energy_kwh * self.rate_eur_kwh


@dataclass(frozen=True)
class EnergyPlan:
    """Result of compute_energy_plan() – always returned, even when there is
    nothing to charge or nothing could be planned. `reason` explains a
    non-feasible or otherwise incomplete plan for display; empty otherwise.
    """
    computed_at: datetime
    deficit_kwh: float | None
    windows: list[PlannedWindow] = field(default_factory=list)
    feasible: bool = True
    reason: str = ""

    @property
    def estimated_cost_eur(self) -> float:
        return sum(w.cost_eur for w in self.windows)

    @property
    def planned_kwh(self) -> float:
        return sum(w.energy_kwh for w in self.windows)


def compute_energy_plan(
    now: datetime,
    deadline: datetime | None,
    bat_kwh_free: float | None,
    predicted_pv_tomorrow_kwh: float | None,
    margin_factor: float,
    charge_kw: float | None,
    curve: "PriceCurve | None",
) -> EnergyPlan:
    """Pure computation, no I/O – see module docstring for the formula.

    deficit_kwh = max(0, bat_kwh_free - predicted_pv_tomorrow_kwh * margin_factor)

    Deliberately the same formula as _should_grid_charge()'s existing
    tomorrow-fallback branch – this makes it explicit and proactive, not a
    different calculation.

    When deficit_kwh > 0, fills the cheapest windows before `deadline`
    first, using `charge_kw` as the assumed rate, until the deficit is
    covered or the candidates run out (`feasible=False` in the latter case).
    """
    if bat_kwh_free is None:
        return EnergyPlan(now, None, feasible=False, reason="battery SoC unavailable")
    if predicted_pv_tomorrow_kwh is None:
        return EnergyPlan(now, None, feasible=False, reason="no PV forecast for tomorrow")

    deficit_kwh = max(0.0, bat_kwh_free - predicted_pv_tomorrow_kwh * margin_factor)
    if deficit_kwh <= 0:
        return EnergyPlan(now, 0.0, reason="tomorrow's PV forecast covers the need")

    if deadline is None:
        return EnergyPlan(now, deficit_kwh, feasible=False, reason="no deadline available (peak time/dark window unknown)")
    if curve is None:
        return EnergyPlan(now, deficit_kwh, feasible=False, reason="no tariff calendar available")
    if charge_kw is None or charge_kw <= 0:
        return EnergyPlan(now, deficit_kwh, feasible=False, reason="no charge power estimate available")

    candidates = curve.windows_before(now, deadline)
    candidates.sort(key=lambda c: c[2])   # (start, end, rate) -> cheapest first

    windows: list[PlannedWindow] = []
    remaining = deficit_kwh
    for start, end, rate in candidates:
        if remaining <= 0:
            break
        capacity_kwh = (end - start).total_seconds() / 3600.0 * charge_kw
        take = min(capacity_kwh, remaining)
        if take <= 0:
            continue
        windows.append(PlannedWindow(start, end, rate, take))
        remaining -= take

    feasible = remaining <= 1e-9
    windows.sort(key=lambda w: w.start)   # chronological, for display
    reason = "" if feasible else "available windows before the deadline cannot cover the deficit"
    return EnergyPlan(now, deficit_kwh, windows, feasible, reason)
