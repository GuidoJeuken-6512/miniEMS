"""A7a – battery characteristics learned from the own history.

docs/roadmap/gesamtplan.md, A7a (W3). The economic gate (A3), the discharge
hold (A4) and the day plan (A6) need the round-trip efficiency and the
usable capacity. Until now they used the inverter's PV-conversion efficiency
(`today_efficiency()`) and the configured capacity.

Per day, CostOptimizer records (daily_stats):

    bat_in_kwh, bat_out_kwh     energy into / out of the battery (power sensor,
                                DC side on a Deye)
    soc_first_pct, soc_last_pct SoC at the day's first and last tick
    dis_eff_load_kwh / _bat_kwh house load ÷ battery output while the battery
                                alone supplies the house (no PV, ~no grid)

Learned over the last days:

    η_dc       = (Σ out + ΔE) ÷ Σ in,   ΔE = capacity × (SoC_end − SoC_start)
    η_dis      = Σ (load + standby × hours) ÷ Σ battery output   (inverter on the
                 way out; standby = its own consumption, paid by grid or battery
                 alike – v4.2.3)
    round trip = η_dc × η_dis²   (the way in through the inverter is taken as
                                  lossy as the way out – not measurable apart)
    capacity   = median over days with |ΔSoC| ≥ 20 % of
                 (in × √η_dc − out ÷ √η_dc) ÷ ΔSoC

Learned values are advice (guideline 5): only with enough data, only inside
physical bounds, otherwise None and the caller keeps its start value.
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any

_MIN_IN_KWH = 10.0
_MIN_DIS_KWH = 5.0
_MIN_DAYS = 3
ETA_DC_BOUNDS = (0.80, 0.99)
ETA_DIS_BOUNDS = (0.80, 1.00)
ROUND_TRIP_BOUNDS = (0.65, 0.98)
_CAPACITY_BOUNDS = (0.5, 1.5)      # × configured capacity
_MIN_SOC_SWING = 20.0
_MIN_IDLE_HOURS = 1.0


@dataclass(frozen=True)
class LearnedBattery:
    days: int
    eta_dc: float | None
    eta_discharge: float | None
    round_trip: float | None
    capacity_kwh: float | None

    def as_dict(self) -> dict:
        def r(v: float | None, n: int = 3) -> float | None:
            return round(v, n) if v is not None else None
        return {"days": self.days, "eta_dc": r(self.eta_dc), "eta_discharge": r(self.eta_discharge),
                "round_trip": r(self.round_trip), "capacity_kwh": r(self.capacity_kwh, 2)}


def _within(value: float, bounds: tuple[float, float]) -> float | None:
    return value if bounds[0] <= value <= bounds[1] else None


def standby_kw(days: list[dict[str, Any]]) -> float | None:
    """The inverter's own consumption: Σ(grid − load) ÷ hours while the battery
    rests and no PV comes. None below _MIN_IDLE_HOURS of such ticks."""
    hours = sum(r.get("idle_hours") or 0.0 for r in days)
    if hours < _MIN_IDLE_HOURS:
        return None
    return sum(r.get("idle_overhead_kwh") or 0.0 for r in days) / hours


def _discharge_eta(days: list[dict[str, Any]]) -> float | None:
    """η_dis = (house load + inverter standby) ÷ battery output.

    While the battery alone supplies the house its output also covers the
    inverter's own consumption – which the grid pays just the same when the
    battery rests (17.11.2026: grid 903 W at 820 W load). Before v4.2.3 it
    counted as a conversion loss: η_dis ≈ 0.85 and, squared, a round trip of
    ~0.70 – a kWh bought at 27.4 ct cost 39 ct, so the day plan stopped grid
    charging at night. Only days that recorded the hours (v4.2.3 on) count.
    """
    rows = [r for r in days if r.get("dis_eff_hours") is not None]
    standby = standby_kw(days)
    bat = sum(r.get("dis_eff_bat_kwh") or 0.0 for r in rows)
    if standby is None or bat < _MIN_DIS_KWH:
        return None
    load = sum((r.get("dis_eff_load_kwh") or 0.0) + standby * (r.get("dis_eff_hours") or 0.0)
               for r in rows)
    return _within(load / bat, ETA_DIS_BOUNDS)


def learn_battery(rows: list[dict[str, Any]], capacity_kwh: float) -> LearnedBattery:
    """Learn from complete daily rows (today excluded by the caller)."""
    days = [r for r in rows if r.get("bat_in_kwh") is not None and r.get("bat_out_kwh") is not None]
    days.sort(key=lambda r: r["date"])
    if len(days) < _MIN_DAYS:
        return LearnedBattery(len(days), None, None, None, None)

    e_in = sum(r["bat_in_kwh"] for r in days)
    e_out = sum(r["bat_out_kwh"] for r in days)
    eta_dc = None
    first, last = days[0].get("soc_first_pct"), days[-1].get("soc_last_pct")
    if e_in >= _MIN_IN_KWH and first is not None and last is not None:
        delta = capacity_kwh * (last - first) / 100.0
        eta_dc = _within((e_out + delta) / e_in, ETA_DC_BOUNDS)

    eta_dis = _discharge_eta(days)

    round_trip = None
    if eta_dc is not None and eta_dis is not None:
        round_trip = _within(eta_dc * eta_dis ** 2, ROUND_TRIP_BOUNDS)

    capacity = None
    if eta_dc is not None:
        root = eta_dc ** 0.5
        estimates = []
        for r in days:
            a, b = r.get("soc_first_pct"), r.get("soc_last_pct")
            if a is None or b is None or abs(b - a) < _MIN_SOC_SWING:
                continue
            stored = r["bat_in_kwh"] * root - r["bat_out_kwh"] / root
            if stored * (b - a) > 0:                 # same sign as the SoC change
                estimates.append(stored / ((b - a) / 100.0))
        if estimates:
            cap = statistics.median(estimates)
            lo, hi = (capacity_kwh * f for f in _CAPACITY_BOUNDS)
            capacity = cap if lo <= cap <= hi else None

    return LearnedBattery(len(days), eta_dc, eta_dis, round_trip, capacity)
