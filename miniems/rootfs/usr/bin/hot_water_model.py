"""Learned hot-water parameters (docs/roadmap/v3.4-warmwasser.md, Schritt 2).

Fitted from the history heat_pump_recorder.py collects. While that history is
thin, a configured prior (e.g. COP 4.77 at 8.5 °C) stands in. The prior is
blended into the fit as pseudo-data, never written to the database, so it
fades out as real hours accumulate and invented values never mix with
measured ones.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from const import (
    HW_COP_CLAMP,
    HW_COP_MIN_LEARNED_H,
    HW_COP_MIN_TICKS,
    HW_COP_PRIOR_SLOPE,
    HW_COP_PRIOR_WEIGHT_H,
    HW_DRAW_LOOKBACK_DAYS,
    HW_DRAW_MIN_DAYS,
    HW_LOSS_MIN_SEGMENTS,
    HW_LOSS_PRIOR_K_PER_H_PER_K,
    HW_MODEL_LOOKBACK_DAYS,
    HW_MODEL_REFIT_SEC,
    HW_ROOM_TEMP_C,
)

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)


def is_workday(day: date) -> bool:
    return day.weekday() < 5


@dataclass(frozen=True)
class HotWaterModel:
    cop_a: float                  # COP = cop_a + cop_b × T_outdoor
    cop_b: float
    cop_ref_temp_c: float         # used when no outdoor temperature is known
    cop_source: str               # "prior" | "learned"
    cop_hours: float              # real hot-water running hours behind the fit
    loss_coeff: float             # K/h per K above room temperature
    loss_source: str
    loss_segments: int
    heat_kw_th: float
    heat_source: str
    # (workday, hour) → expected drop in K during that hour
    draw_profile: dict[tuple[bool, int], float] = field(default_factory=dict)
    draw_days: int = 0
    # Average hot-water electricity per hour over the history, kWh. What the
    # load forecast already contains as a flat base.
    ww_elec_kwh_per_h: float | None = None

    def cop_at(self, outdoor_c: float | None) -> float:
        t = self.cop_ref_temp_c if outdoor_c is None else outdoor_c
        lo, hi = HW_COP_CLAMP
        return max(lo, min(hi, self.cop_a + self.cop_b * t))

    def loss_k_per_h(self, boil_c: float) -> float:
        return max(0.0, self.loss_coeff * (boil_c - HW_ROOM_TEMP_C))

    def draw_k(self, start: datetime, end: datetime) -> float:
        """Expected drop (K) between start and end from the learned profile."""
        if not self.draw_profile:
            return 0.0
        total = 0.0
        t = start
        while t < end:
            hour_end = t.replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
            seg_end = min(end, hour_end)
            frac = (seg_end - t).total_seconds() / 3600.0
            total += self.draw_profile.get((is_workday(t.date()), t.hour), 0.0) * frac
            t = seg_end
        return total

    def as_dict(self, outdoor_c: float | None = None) -> dict[str, Any]:
        return {
            "cop_now": round(self.cop_at(outdoor_c), 2),
            "cop_outdoor_c": outdoor_c,
            "cop_a": round(self.cop_a, 3),
            "cop_b": round(self.cop_b, 4),
            "cop_source": self.cop_source,
            "cop_hours": round(self.cop_hours, 1),
            "loss_k_per_h_at_50": round(self.loss_k_per_h(50.0), 3),
            "loss_source": self.loss_source,
            "loss_segments": self.loss_segments,
            "heat_kw_th": round(self.heat_kw_th, 2),
            "heat_source": self.heat_source,
            "draw_days": self.draw_days,
            "draw_min_days": HW_DRAW_MIN_DAYS,
            "draw_learned": bool(self.draw_profile),
            "draw_k_per_day_workday": round(
                sum(v for (wd, _), v in self.draw_profile.items() if wd), 1),
            "draw_k_per_day_weekend": round(
                sum(v for (wd, _), v in self.draw_profile.items() if not wd), 1),
            "ww_elec_kwh_per_h": (
                round(self.ww_elec_kwh_per_h, 3) if self.ww_elec_kwh_per_h is not None else None
            ),
        }


def _fit_cop(
    rows: list[dict], mode: int, interval_sec: int, prior_cop: float, prior_temp: float
) -> tuple[float, float, str, float]:
    """Weighted least squares of COP over outdoor temperature.

    The prior enters as two pseudo-points ±5 K around prior_temp on the prior
    slope, together worth HW_COP_PRIOR_WEIGHT_H hours: it fixes level and
    slope while real data is thin and is outweighed once real hours pile up.
    """
    pts: list[tuple[float, float, float]] = []
    real_h = 0.0
    for r in rows:
        if r.get("mode") != mode or r.get("cop_avg") is None or r.get("outdoor_temp_c") is None:
            continue
        ticks = int(r.get("cop_ticks") or 0)
        if ticks < HW_COP_MIN_TICKS:
            continue
        w = ticks * interval_sec / 3600.0
        pts.append((float(r["outdoor_temp_c"]), float(r["cop_avg"]), w))
        real_h += w
    half = HW_COP_PRIOR_WEIGHT_H / 2.0
    pts.append((prior_temp - 5.0, prior_cop - 5.0 * HW_COP_PRIOR_SLOPE, half))
    pts.append((prior_temp + 5.0, prior_cop + 5.0 * HW_COP_PRIOR_SLOPE, half))

    sw = sum(w for _, _, w in pts)
    mx = sum(x * w for x, _, w in pts) / sw
    my = sum(y * w for _, y, w in pts) / sw
    sxx = sum(w * (x - mx) ** 2 for x, _, w in pts)
    sxy = sum(w * (x - mx) * (y - my) for x, y, w in pts)
    b = sxy / sxx if sxx > 1e-9 else HW_COP_PRIOR_SLOPE
    a = my - b * mx
    source = "learned" if real_h >= HW_COP_MIN_LEARNED_H else "prior"
    return a, b, source, real_h


def _fit_loss(segments: list[dict]) -> tuple[float, str, int]:
    coeffs = []
    for s in segments:
        avg = s.get("avg_boil_temp_c")
        k = s.get("k_per_h")
        if avg is None or k is None or avg - HW_ROOM_TEMP_C < 5.0:
            continue
        coeffs.append(float(k) / (float(avg) - HW_ROOM_TEMP_C))
    if len(coeffs) >= HW_LOSS_MIN_SEGMENTS:
        return statistics.median(coeffs), "learned", len(coeffs)
    return HW_LOSS_PRIOR_K_PER_H_PER_K, "prior", len(coeffs)


def _fit_heat_power(rows: list[dict], mode: int, interval_sec: int, fallback_kw: float) -> tuple[float, str]:
    values = []
    for r in rows:
        if r.get("mode") != mode or r.get("heat_pump_kwh") is None or r.get("cop_avg") is None:
            continue
        ticks = int(r.get("ticks") or 0)
        if ticks < 20:
            continue
        hours = ticks * interval_sec / 3600.0
        values.append(float(r["heat_pump_kwh"]) / hours * float(r["cop_avg"]))
    if len(values) >= 3:
        return statistics.median(values), "learned"
    return fallback_kw, "config"


def _fit_draws(draws: list[dict], today: date) -> tuple[dict[tuple[bool, int], float], int]:
    since = today - timedelta(days=HW_DRAW_LOOKBACK_DAYS)
    parsed = []
    for d in draws:
        try:
            ts = datetime.fromisoformat(d["ts"])
        except (KeyError, TypeError, ValueError):
            continue
        if ts.date() >= since:
            parsed.append((ts, float(d.get("drop_k") or 0.0)))
    if not parsed:
        return {}, 0
    first = min(ts.date() for ts, _ in parsed)
    span = (today - first).days
    if span < HW_DRAW_MIN_DAYS:
        return {}, span
    days = [first + timedelta(days=i) for i in range(span)]
    n_work = sum(1 for d in days if is_workday(d)) or 1
    n_free = sum(1 for d in days if not is_workday(d)) or 1
    profile: dict[tuple[bool, int], float] = {}
    for ts, drop in parsed:
        if ts.date() >= today:
            continue
        key = (is_workday(ts.date()), ts.hour)
        profile[key] = profile.get(key, 0.0) + drop
    for key in profile:
        profile[key] /= n_work if key[0] else n_free
    return profile, span


def _ww_elec_per_hour(rows: list[dict], mode: int) -> float | None:
    hours = {r["hour_start"] for r in rows if r.get("hour_start")}
    kwh = [float(r["heat_pump_kwh"]) for r in rows
           if r.get("mode") == mode and r.get("heat_pump_kwh") is not None]
    if not hours or not kwh:
        return None
    return sum(kwh) / len(hours)


def fit_hot_water_model(
    hp_rows: list[dict],
    segments: list[dict],
    draws: list[dict],
    cfg: "Config",
    today: date,
    interval_sec: int,
) -> HotWaterModel:
    mode = cfg.hp_mode_hot_water_code
    a, b, cop_source, cop_h = _fit_cop(hp_rows, mode, interval_sec,
                                       cfg.hw_cop_prior, cfg.hw_cop_prior_temp_c)
    loss, loss_source, n_seg = _fit_loss(segments)
    heat_kw, heat_source = _fit_heat_power(hp_rows, mode, interval_sec, cfg.hw_heat_power_kw)
    profile, draw_days = _fit_draws(draws, today)
    return HotWaterModel(
        cop_a=a, cop_b=b, cop_ref_temp_c=cfg.hw_cop_prior_temp_c,
        cop_source=cop_source, cop_hours=cop_h,
        loss_coeff=loss, loss_source=loss_source, loss_segments=n_seg,
        heat_kw_th=heat_kw, heat_source=heat_source,
        draw_profile=profile, draw_days=draw_days,
        ww_elec_kwh_per_h=_ww_elec_per_hour(hp_rows, mode),
    )


class HotWaterModelLoader:
    """Refits the model from the store at most every HW_MODEL_REFIT_SEC."""

    def __init__(self, store: "EnergyStore | None", cfg: "Config", interval_sec: int) -> None:
        self._store = store
        self._cfg = cfg
        self._interval = interval_sec
        self._fitted_at: datetime | None = None
        self.model: HotWaterModel = fit_hot_water_model([], [], [], cfg, date.today(), interval_sec)

    async def refresh(self, now: datetime) -> HotWaterModel:
        if (self._fitted_at is not None
                and (now - self._fitted_at).total_seconds() < HW_MODEL_REFIT_SEC):
            return self.model
        self._fitted_at = now
        if self._store is None:
            return self.model
        try:
            since = now.date() - timedelta(days=HW_MODEL_LOOKBACK_DAYS)
            hp_rows = await self._store.query_hp_hours_since(since)
            segments = await self._store.query_loss_segments()
            draws = await self._store.query_draws(limit=5000)
            self.model = fit_hot_water_model(hp_rows, segments, draws, self._cfg,
                                             now.date(), self._interval)
        except Exception as exc:   # keep the last model; learning is best-effort
            _LOGGER.warning("Hot-water model refit failed: %s", exc)
        return self.model
