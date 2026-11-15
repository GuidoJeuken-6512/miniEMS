"""A8 – hot water: the heat lost per start, learned from the own charges.

docs/roadmap/gesamtplan.md, A8. Every start costs: the flow pipes are heated
first, cold water from the pipes pulls the sensor down, the heat pump runs on
after the end. The planner priced a charge only per kWh of heat, so a 0.7 K
top-up looked as cheap as a full charge – the fixed minimum charge
(hw_min_charge_k) has guarded against that since v3.7.0.

Per finished charge (table hot_water_charges):

    heat_lambda = the heat pump's thermal hot-water counter over the charge
                  (including 10 min of trailing run)
    heat_boiler = ΔT × volume × 1.163 Wh/(l·K)
    loss        = heat_lambda − heat_boiler

Learned separately for "start from standby" and "switch over from space
heating" (the compressor already runs) by a straight line

    loss ≈ fix + variable × ΔT

– `fix` is the heat lost per start. Charges with a draw are dropped (the
boiler balance is wrong then). Advice only: at least 5 charges per kind,
fix within 0–3 kWh, otherwise None and the fixed minimum charge alone decides.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

_MIN_CHARGES = 5
_FIX_BOUNDS = (0.0, 3.0)


@dataclass(frozen=True)
class StartLosses:
    standby_kwh_th: float | None      # start from standby (STBY)
    running_kwh_th: float | None      # switch over while the compressor runs (CH → DHW)
    standby_charges: int = 0
    running_charges: int = 0

    def as_dict(self) -> dict:
        r = lambda v: round(v, 3) if v is not None else None   # noqa: E731
        return {"standby_kwh_th": r(self.standby_kwh_th), "running_kwh_th": r(self.running_kwh_th),
                "standby_charges": self.standby_charges, "running_charges": self.running_charges}


def _fit(points: list[tuple[float, float]]) -> float | None:
    """Intercept of loss over ΔT; the mean loss when ΔT does not vary."""
    if len(points) < _MIN_CHARGES:
        return None
    n = len(points)
    mx = sum(x for x, _ in points) / n
    my = sum(y for _, y in points) / n
    sxx = sum((x - mx) ** 2 for x, _ in points)
    if sxx < 1e-6:
        fix = my
    else:
        slope = sum((x - mx) * (y - my) for x, y in points) / sxx
        fix = my - max(0.0, slope) * mx
    lo, hi = _FIX_BOUNDS
    return max(lo, min(hi, fix))


def learn_start_losses(rows: list[dict[str, Any]]) -> StartLosses:
    groups: dict[bool, list[tuple[float, float]]] = {False: [], True: []}
    for r in rows:
        if not r.get("valid"):
            continue
        try:
            dt = float(r["end_c"]) - float(r["start_c"])
            loss = float(r["heat_lambda_kwh"]) - float(r["heat_boiler_kwh"])
        except (KeyError, TypeError, ValueError):
            continue
        if dt <= 0:
            continue
        groups[bool(r.get("from_running"))].append((dt, loss))
    return StartLosses(
        standby_kwh_th=_fit(groups[False]), running_kwh_th=_fit(groups[True]),
        standby_charges=len(groups[False]), running_charges=len(groups[True]),
    )
