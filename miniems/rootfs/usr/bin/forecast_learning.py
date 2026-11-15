"""A7b/A7c – correct the PV and load forecasts from plan against result.

docs/roadmap/gesamtplan.md, A7b (W4) and A7c (W5). hourly_stats stores, per
local hour, the measured PV and load (pv_kwh, load_kwh) next to what was
forecast for that hour when it began (pv_fc_kwh, load_pred_kwh).

    A7b  PV factor per hour of day  = Σ measured ÷ Σ forecast  (0.3–2.0)
         – a roof facing east or a tree at 16:00 shows up as a shape, not
           as a single number
    A7c  systematic factors over all hours: load (0.8–1.25), PV (0.7–1.2)

Learned values are advice: only with enough data (per hour ≥ 5 days and
≥ 1 kWh forecast; overall ≥ 72 hours), clamped, otherwise 1.0.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

PV_HOUR_BOUNDS = (0.3, 2.0)
PV_TOTAL_BOUNDS = (0.7, 1.2)
LOAD_BOUNDS = (0.8, 1.25)
_MIN_HOUR_DAYS = 5
_MIN_HOUR_FC_KWH = 1.0
_MIN_TOTAL_HOURS = 72
_MIN_FC_KWH = 0.05     # an hour with less forecast PV says nothing about the ratio


def _clamp(value: float, bounds: tuple[float, float]) -> float:
    return max(bounds[0], min(bounds[1], value))


@dataclass(frozen=True)
class ForecastCorrections:
    pv_by_hour: dict[int, float] = field(default_factory=dict)   # A7b, learned hours only
    pv_total: float = 1.0                                        # A7c
    load_total: float = 1.0                                      # A7c
    pv_hours: int = 0
    load_hours: int = 0

    def pv_factor(self, hour: int, per_hour: bool, total: bool) -> float:
        if per_hour and hour in self.pv_by_hour:
            return self.pv_by_hour[hour]
        return self.pv_total if total else 1.0

    def as_dict(self) -> dict:
        return {
            "pv_by_hour": {str(h): round(f, 3) for h, f in sorted(self.pv_by_hour.items())},
            "pv_total": round(self.pv_total, 3),
            "load_total": round(self.load_total, 3),
            "pv_hours": self.pv_hours,
            "load_hours": self.load_hours,
        }


def learn_corrections(rows: list[dict[str, Any]]) -> ForecastCorrections:
    """Learn from complete hourly rows (the hour in progress excluded)."""
    by_hour: dict[int, list[tuple[float, float]]] = {}
    pv_meas = pv_fc = 0.0
    pv_hours = 0
    load_meas = load_pred = 0.0
    load_hours = 0
    for r in rows:
        fc, meas = r.get("pv_fc_kwh"), r.get("pv_kwh")
        if fc is not None and meas is not None and fc >= _MIN_FC_KWH:
            by_hour.setdefault(int(r["hour"]), []).append((meas, fc))
            pv_meas += meas
            pv_fc += fc
            pv_hours += 1
        pred, load = r.get("load_pred_kwh"), r.get("load_kwh")
        if pred is not None and load is not None and pred > 0:
            load_meas += load
            load_pred += pred
            load_hours += 1

    pv_by_hour: dict[int, float] = {}
    for hour, pairs in by_hour.items():
        fc_sum = sum(f for _, f in pairs)
        if len(pairs) >= _MIN_HOUR_DAYS and fc_sum >= _MIN_HOUR_FC_KWH:
            pv_by_hour[hour] = _clamp(sum(m for m, _ in pairs) / fc_sum, PV_HOUR_BOUNDS)

    pv_total = (_clamp(pv_meas / pv_fc, PV_TOTAL_BOUNDS)
                if pv_hours >= _MIN_TOTAL_HOURS and pv_fc > 0 else 1.0)
    load_total = (_clamp(load_meas / load_pred, LOAD_BOUNDS)
                  if load_hours >= _MIN_TOTAL_HOURS and load_pred > 0 else 1.0)
    return ForecastCorrections(pv_by_hour, pv_total, load_total, pv_hours, load_hours)
