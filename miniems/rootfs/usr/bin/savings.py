"""C5 – savings as one headline figure (docs/roadmap/gesamtplan.md, C5).

Baseline: the same household without PV, battery and miniEMS. Its daily
consumption (the measured load of that day) is spread over the hours like a
standard household load profile and bought at that hour's actual price.

    baseline_day = load_day × Σ_h (w_h × price_h) / Σ_h w_h
    actual_day   = grid cost − feed-in revenue
    saving_day   = baseline_day − actual_day          (negative is shown as negative)

Only hours with a recorded price count (hourly_stats.price_eur_kwh, recorded
since v4.2.0); a day needs at least MIN_COVERAGE of the profile weight.

The profile is an approximation of the daily shape of the BDEW standard
household profile (H0, from 2025 H25): one weekday shape, normalised. It is
not the official quarter-hour table – the official table can replace
PROFILE_WEIGHTS without changing anything else. See docs/user/costs.md.

Pure.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date

# Relative household load per local hour (0–23), approximated daily shape.
PROFILE_WEIGHTS: tuple[float, ...] = (
    58, 48, 43, 41, 41, 45, 62, 89, 100, 104, 108, 118,
    130, 122, 110, 104, 110, 135, 160, 165, 150, 130, 105, 78,
)
MIN_COVERAGE = 0.75


@dataclass(frozen=True)
class DaySaving:
    day: date
    load_kwh: float
    baseline_eur: float
    actual_eur: float
    coverage: float

    @property
    def saving_eur(self) -> float:
        return self.baseline_eur - self.actual_eur


def baseline_cost(load_kwh: float, hour_prices: dict[int, float]) -> tuple[float | None, float]:
    """(baseline €, coverage of the profile weight)."""
    total_w = sum(PROFILE_WEIGHTS)
    known = [(PROFILE_WEIGHTS[h], p) for h, p in hour_prices.items() if 0 <= h < 24 and p is not None]
    w = sum(x for x, _ in known)
    coverage = w / total_w if total_w else 0.0
    if w <= 0:
        return None, 0.0
    return load_kwh * sum(x * p for x, p in known) / w, coverage


def day_saving(row: dict, hour_prices: dict[int, float]) -> DaySaving | None:
    try:
        day = date.fromisoformat(str(row["date"]))
    except (KeyError, ValueError):
        return None
    load = float(row.get("load_total_kwh") or 0.0)
    if load <= 0:
        return None
    base, coverage = baseline_cost(load, hour_prices)
    if base is None or coverage < MIN_COVERAGE:
        return None
    actual = float(row.get("grid_cost_eur") or 0.0) - float(row.get("feed_in_revenue_eur") or 0.0)
    return DaySaving(day, load, base, actual, coverage)


def summarise(days: list[DaySaving], today: date, plan_savings_eur: float | None = None) -> dict:
    """The headline tile: 7 days and this month."""
    def block(selected: list[DaySaving]) -> dict:
        return {
            "saving_eur": round(sum(d.saving_eur for d in selected), 2) if selected else None,
            "baseline_eur": round(sum(d.baseline_eur for d in selected), 2) if selected else None,
            "actual_eur": round(sum(d.actual_eur for d in selected), 2) if selected else None,
            "days": len(selected),
        }
    week = [d for d in days if 0 <= (today - d.day).days < 7]
    month = [d for d in days if d.day.year == today.year and d.day.month == today.month]
    return {
        "week": block(week),
        "month": block(month),
        "plan_today_eur": None if plan_savings_eur is None else round(plan_savings_eur, 2),
        "daily": [{"day": d.day.isoformat(), "saving_eur": round(d.saving_eur, 3),
                   "baseline_eur": round(d.baseline_eur, 3), "actual_eur": round(d.actual_eur, 3)}
                  for d in sorted(days, key=lambda d: d.day)[-31:]],
        "method": "same load, spread like a standard household profile, at the actual hourly price",
    }
