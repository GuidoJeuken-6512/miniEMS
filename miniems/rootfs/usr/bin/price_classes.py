"""Relative price classes – low / medium / high relative to the day (A1).

docs/roadmap/gesamtplan.md, "Relative Preisklassen". Replaces the fixed
`cheap_rate_threshold_eur` wherever a tariff calendar is available: a fixed
threshold is right on one tariff and wrong on the next, while "the N
cheapest hours of the day" means the same thing for a three-step time-of-use
tariff and for hourly market prices.

    preise    = all prices of the calendar day the moment belongs to
    spanne    = max − min
    flat      = spanne < flat_spread            → no optimisation, nothing is low
    limit     = quantile(preise, cheap_hours ÷ 24)
    low       = price ≤ limit
    high      = price ≥ median + max(hysteresis, 0.5 × (max − median))
    medium    = otherwise

Three deliberate differences to EnergyWizard (checked against the Octopus
tariff 2026-11-15):

- per calendar day, not "from now to the end of the known prices": at 22:00
  only two hours are left, and a fixed number of cheap slots would then make
  everything cheap;
- no hysteresis on entering `low` – on a step tariff the limit *is* the low
  rate, `price ≤ limit − hysteresis` would never be true. The hysteresis is
  computed for slow actuators (heating) but the gates do not use it;
- `high` from the distance to the median rather than P85: HOCH lasts 3 of 24
  hours (12.5 %), so P85 would land on STANDARD and there would never be a
  peak.

Pure module, no I/O.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from price_curve import PriceCurve

# Sampling grid over the day – also makes every statistic time-weighted.
_STEP = timedelta(minutes=15)
# Fewer known samples than this (2 h) and the day says nothing.
_MIN_SAMPLES = 8
_EPS = 1e-9


@dataclass(frozen=True)
class PriceClasses:
    """Class boundaries of one calendar day."""

    day: date
    limit_eur: float            # price ≤ limit → low
    high_eur: float             # price ≥ high → high
    median_eur: float
    min_eur: float
    max_eur: float
    hysteresis_eur: float       # for slow actuators (heating), not for the gates
    flat: bool                  # spread below price_flat_spread_eur
    samples: int

    @property
    def spread_eur(self) -> float:
        return self.max_eur - self.min_eur

    def is_low(self, price: float | None) -> bool:
        if price is None or self.flat:
            return False
        return price <= self.limit_eur + _EPS

    def tier(self, price: float | None) -> str | None:
        """"low" | "medium" | "high" – the same three values as price_tier()."""
        if price is None:
            return None
        if self.flat:
            return "medium"
        if price <= self.limit_eur + _EPS:
            return "low"
        if price >= self.high_eur - _EPS:
            return "high"
        return "medium"

    def as_dict(self) -> dict:
        return {
            "day": self.day.isoformat(),
            "limit_eur": round(self.limit_eur, 5),
            "high_eur": round(self.high_eur, 5),
            "median_eur": round(self.median_eur, 5),
            "spread_eur": round(self.spread_eur, 5),
            "hysteresis_eur": round(self.hysteresis_eur, 5),
            "flat": self.flat,
            "samples": self.samples,
        }


def day_rates(curve: "PriceCurve", day: date, tzinfo) -> list[float]:
    """Known prices of `day` on a 15-min grid (holes left out)."""
    cursor = datetime.combine(day, time(0), tzinfo=tzinfo)
    end = cursor + timedelta(days=1)
    rates: list[float] = []
    while cursor < end:
        rate = curve.rate_at(cursor)
        if rate is not None:
            rates.append(rate)
        cursor += _STEP
    return rates


def classify_day(
    rates: list[float],
    day: date,
    cheap_hours: float,
    hysteresis_min_eur: float,
    hysteresis_max_eur: float,
    flat_spread_eur: float,
) -> PriceClasses | None:
    """Class boundaries from one day's prices. None with too few prices."""
    if len(rates) < _MIN_SAMPLES:
        return None
    s = sorted(rates)
    n = len(s)
    lo, hi = s[0], s[-1]
    spread = hi - lo
    # Nearest rank: the limit is always a price that actually occurs, never a
    # value interpolated between two tariff steps.
    q = max(0.0, min(1.0, cheap_hours / 24.0))
    limit = s[max(0, math.ceil(q * n) - 1)]
    median = s[n // 2]
    hysteresis = min(max(0.15 * spread, hysteresis_min_eur), hysteresis_max_eur)
    high = median + max(hysteresis, 0.5 * (hi - median))
    return PriceClasses(
        day=day, limit_eur=limit, high_eur=high, median_eur=median,
        min_eur=lo, max_eur=hi, hysteresis_eur=hysteresis,
        flat=spread < flat_spread_eur, samples=n,
    )


def classes_for(
    curve: "PriceCurve | None",
    moment: datetime,
    cheap_hours: float,
    hysteresis_min_eur: float,
    hysteresis_max_eur: float,
    flat_spread_eur: float,
) -> PriceClasses | None:
    """Classes of the calendar day `moment` belongs to, or None."""
    if curve is None:
        return None
    day = moment.date()
    return classify_day(
        day_rates(curve, day, moment.tzinfo), day, cheap_hours,
        hysteresis_min_eur, hysteresis_max_eur, flat_spread_eur,
    )
