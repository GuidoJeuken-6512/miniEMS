"""Stündliche Bedarfsprognose (v3.3.0).

docs/technical/netzladen-entscheidung.md, "Bedarfsprognose".

Two parts:

* HourlyLoadRecorder – integrates household load, optional heat-pump power
  and outdoor temperature per local clock hour into the `hourly_stats` table.
* LoadForecaster – fits a small, explainable model on that history and
  predicts the load of any future slot from its outdoor temperature:

      load(h, T) = base[h] + hp_base + k × max(0, T_limit − T)

  base[h]  – household load by hour of day (median), without the heat pump
             when a heat-pump meter is configured
  hp_base  – flat heat-pump share (hot water + standby), "Warmwasser pauschal"
  k        – kWh per hour and Kelvin below the heating limit

  Without a heat-pump meter, hp_base is 0 and k is learned from the total
  load; base[h] then also carries the hot-water share.

Deliberately no ML: a dozen numbers that can be shown on the dashboard and
checked by hand beat a black box that silently drifts.
"""
from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING

from const import LOAD_MODEL_LOOKBACK_DAYS, LOAD_MODEL_REFIT_SEC

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

# An hour counts as "complete" once at least this share of it was measured –
# an add-on restart mid-hour must not enter the model as a low-load hour.
_MIN_HOUR_COVERAGE = 0.75
# A day counts towards load_forecast_min_days with this many complete hours.
_MIN_HOURS_PER_DAY = 18


def _hdd(temp_c: float | None, limit_c: float) -> float | None:
    """Heating degree hours of one hour: K below the heating limit, ≥ 0."""
    if temp_c is None:
        return None
    return max(0.0, limit_c - temp_c)


# ---------------------------------------------------------------------------
# Recording
# ---------------------------------------------------------------------------


class HourlyLoadRecorder:
    """Accumulates one local clock hour at a time and upserts it every tick.

    Gaps (sensor unavailable for a tick) are filled with the hour's own
    average rather than counted as 0 W: `kwh = Σ(W·dt) × ticks / valid_ticks`.
    After a restart within an hour, the stored row is read back and continued,
    so the hour is not overwritten with a fragment.
    """

    def __init__(self, store: "EnergyStore | None", interval_sec: int) -> None:
        self._store = store
        self._interval = interval_sec
        self._hour: datetime | None = None
        self._seeded = False
        self._ticks = 0
        self._load_wh = 0.0
        self._load_ticks = 0
        self._hp_wh = 0.0
        self._hp_ticks = 0
        self._temp_sum = 0.0
        self._temp_ticks = 0

    def _reset(self, hour: datetime) -> None:
        self._hour = hour
        self._seeded = False
        self._ticks = 0
        self._load_wh = self._hp_wh = self._temp_sum = 0.0
        self._load_ticks = self._hp_ticks = self._temp_ticks = 0

    async def record_tick(
        self,
        now: datetime,
        load_w: float | None,
        heat_pump_w: float | None,
        outdoor_temp_c: float | None,
    ) -> None:
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour != self._hour:
            self._reset(hour)
        if not self._seeded:
            await self._seed_from_store()
            self._seeded = True

        dt_h = self._interval / 3600.0
        self._ticks += 1
        if load_w is not None:
            self._load_wh += max(0.0, load_w) * dt_h
            self._load_ticks += 1
        if heat_pump_w is not None:
            self._hp_wh += max(0.0, heat_pump_w) * dt_h
            self._hp_ticks += 1
        if outdoor_temp_c is not None:
            self._temp_sum += outdoor_temp_c
            self._temp_ticks += 1
        await self._flush()

    async def _seed_from_store(self) -> None:
        """Continue an hour that was partly recorded before a restart."""
        if self._store is None or self._hour is None:
            return
        rows = await self._store.query_hours_since(self._hour.date())
        key = self._hour.isoformat()
        row = next((r for r in rows if r.get("hour_start") == key), None)
        if not row or not row.get("ticks"):
            return
        ticks = int(row["ticks"])
        self._ticks = ticks
        self._load_wh = float(row.get("load_kwh") or 0.0) * 1000.0
        self._load_ticks = ticks
        if row.get("heat_pump_kwh") is not None:
            self._hp_wh = float(row["heat_pump_kwh"]) * 1000.0
            self._hp_ticks = ticks
        if row.get("outdoor_temp_c") is not None:
            self._temp_sum = float(row["outdoor_temp_c"]) * ticks
            self._temp_ticks = ticks

    def _scaled_kwh(self, wh: float, valid: int) -> float:
        return wh / 1000.0 * self._ticks / valid

    async def _flush(self) -> None:
        if self._store is None or self._hour is None or self._load_ticks == 0:
            return
        fields = {
            "date": str(self._hour.date()),
            "hour": self._hour.hour,
            "load_kwh": round(self._scaled_kwh(self._load_wh, self._load_ticks), 5),
            "heat_pump_kwh": (
                round(self._scaled_kwh(self._hp_wh, self._hp_ticks), 5)
                if self._hp_ticks else None
            ),
            "outdoor_temp_c": (
                round(self._temp_sum / self._temp_ticks, 2) if self._temp_ticks else None
            ),
            "ticks": self._ticks,
        }
        await self._store.upsert_hour(self._hour.isoformat(), fields)


# ---------------------------------------------------------------------------
# Model
# ---------------------------------------------------------------------------


@dataclass
class LoadModel:
    base_by_hour: list[float]          # kWh per hour of day, index 0..23
    hp_base_kwh_h: float               # flat heat-pump share per hour
    k_kwh_per_kh: float                # kWh per hour per K below the limit
    heating_limit_c: float
    heat_pump_split: bool              # True = fitted on a separate HP meter
    days: int                          # qualifying days of history
    hours: int                         # complete hours used
    mean_hdd: float                    # fallback when a slot has no temperature
    fitted_at: datetime | None = None

    def predict_hour_kwh(self, hour: int, temp_c: float | None) -> float:
        hdd = _hdd(temp_c, self.heating_limit_c)
        if hdd is None:
            hdd = self.mean_hdd
        return max(0.0, self.base_by_hour[hour % 24] + self.hp_base_kwh_h
                   + self.k_kwh_per_kh * hdd)

    def predict_day_kwh(self, temp_c: float | None) -> float:
        return sum(self.predict_hour_kwh(h, temp_c) for h in range(24))

    def as_dict(self) -> dict:
        return {
            "base_by_hour_kwh": [round(v, 3) for v in self.base_by_hour],
            "heat_pump_base_kwh_h": round(self.hp_base_kwh_h, 3),
            "k_kwh_per_kelvin_h": round(self.k_kwh_per_kh, 4),
            "heating_limit_c": self.heating_limit_c,
            "heat_pump_split": self.heat_pump_split,
            "days": self.days,
            "hours": self.hours,
            "fitted_at": self.fitted_at.isoformat() if self.fitted_at else None,
        }


def _median_by_hour(pairs: list[tuple[int, float]]) -> list[float]:
    buckets: dict[int, list[float]] = {h: [] for h in range(24)}
    for h, v in pairs:
        buckets[h % 24].append(v)
    all_vals = [v for _, v in pairs]
    overall = statistics.median(all_vals) if all_vals else 0.0
    return [max(0.0, statistics.median(b)) if b else max(0.0, overall)
            for b in (buckets[h] for h in range(24))]


def _linear_fit(xs: list[float], ys: list[float]) -> tuple[float, float]:
    """Least-squares (a, k) for y = a + k·x, both clamped ≥ 0."""
    n = len(xs)
    if n == 0:
        return 0.0, 0.0
    mx = sum(xs) / n
    my = sum(ys) / n
    sxx = sum((x - mx) ** 2 for x in xs)
    if sxx <= 0:
        return max(0.0, my), 0.0
    k = max(0.0, sum((x - mx) * (y - my) for x, y in zip(xs, ys)) / sxx)
    a = max(0.0, my - k * mx)
    return a, k


def fit_load_model(
    rows: list[dict],
    heating_limit_c: float,
    interval_sec: int,
    heat_pump_split: bool,
    now: datetime | None = None,
) -> LoadModel | None:
    """Fit LoadModel on hourly_stats rows. None when there is nothing to fit."""
    min_ticks = _MIN_HOUR_COVERAGE * 3600.0 / max(1, interval_sec)
    complete = [r for r in rows if (r.get("ticks") or 0) >= min_ticks
                and r.get("load_kwh") is not None]
    if not complete:
        return None

    hours_per_day: dict[str, int] = {}
    for r in complete:
        hours_per_day[r["date"]] = hours_per_day.get(r["date"], 0) + 1
    days = sum(1 for n in hours_per_day.values() if n >= _MIN_HOURS_PER_DAY)

    with_temp = [r for r in complete if r.get("outdoor_temp_c") is not None]
    hdds = [_hdd(r["outdoor_temp_c"], heating_limit_c) for r in with_temp]
    mean_hdd = (sum(hdds) / len(hdds)) if hdds else 0.0

    hp_rows = [r for r in complete if r.get("heat_pump_kwh") is not None]
    if heat_pump_split and len(hp_rows) >= 24:
        base = _median_by_hour([
            (int(r["hour"]), float(r["load_kwh"]) - float(r["heat_pump_kwh"]))
            for r in hp_rows
        ])
        hp_temp = [r for r in hp_rows if r.get("outdoor_temp_c") is not None]
        if hp_temp:
            xs = [_hdd(r["outdoor_temp_c"], heating_limit_c) for r in hp_temp]
            ys = [float(r["heat_pump_kwh"]) for r in hp_temp]
            hp_base, k = _linear_fit(xs, ys)
        else:
            hp_base, k = statistics.median(float(r["heat_pump_kwh"]) for r in hp_rows), 0.0
        split = True
    else:
        # Joint fit with one intercept per hour of day ("fixed effects"):
        # k from the within-hour covariance of load and hdd, then
        # base[h] = median(load − k·hdd). Exact for the model, and robust in
        # base[h] against single odd hours (party, sauna, holiday).
        k = 0.0
        if with_temp:
            by_hour: dict[int, list[tuple[float, float]]] = {}
            for r, h in zip(with_temp, hdds):
                by_hour.setdefault(int(r["hour"]) % 24, []).append((h, float(r["load_kwh"])))
            sxy = sxx = 0.0
            for pairs in by_hour.values():
                mx = sum(p[0] for p in pairs) / len(pairs)
                my = sum(p[1] for p in pairs) / len(pairs)
                sxy += sum((x - mx) * (y - my) for x, y in pairs)
                sxx += sum((x - mx) ** 2 for x, _ in pairs)
            k = max(0.0, sxy / sxx) if sxx > 0 else 0.0
            base = _median_by_hour([
                (int(r["hour"]), float(r["load_kwh"]) - k * h)
                for r, h in zip(with_temp, hdds)
            ])
        else:
            base = _median_by_hour([(int(r["hour"]), float(r["load_kwh"])) for r in complete])
        hp_base = 0.0
        split = False

    return LoadModel(
        base_by_hour=base,
        hp_base_kwh_h=hp_base,
        k_kwh_per_kh=k,
        heating_limit_c=heating_limit_c,
        heat_pump_split=split,
        days=days,
        hours=len(complete),
        mean_hdd=mean_hdd,
        fitted_at=now,
    )


class LoadForecaster:
    """Refits LoadModel from the store at most every LOAD_MODEL_REFIT_SEC."""

    def __init__(self, config: "Config", store: "EnergyStore | None") -> None:
        self._cfg = config
        self._store = store
        self.model: LoadModel | None = None
        self._last_fit: datetime | None = None

    @property
    def ready(self) -> bool:
        """Enough history to let the forecast drive decisions."""
        return (self.model is not None
                and self.model.days >= self._cfg.load_forecast_min_days)

    async def refresh(self, now: datetime) -> None:
        if self._store is None:
            return
        if (self._last_fit is not None
                and (now - self._last_fit).total_seconds() < LOAD_MODEL_REFIT_SEC):
            return
        self._last_fit = now
        since: date = (now - timedelta(days=LOAD_MODEL_LOOKBACK_DAYS)).date()
        rows = await self._store.query_hours_since(since)
        # The hour in progress is incomplete by definition.
        current = now.replace(minute=0, second=0, microsecond=0).isoformat()
        rows = [r for r in rows if r.get("hour_start") != current]
        self.model = fit_load_model(
            rows,
            heating_limit_c=self._cfg.heating_limit_temp_c,
            interval_sec=self._cfg.update_interval_sec,
            heat_pump_split=bool(self._cfg.heat_pump_entities),
            now=now,
        )
        if self.model is not None:
            _LOGGER.info(
                "Load model refit: %d days / %d h, base %.2f kWh/day, HP base %.3f kWh/h, "
                "k=%.4f kWh/(K·h), split=%s, ready=%s",
                self.model.days, self.model.hours, sum(self.model.base_by_hour),
                self.model.hp_base_kwh_h, self.model.k_kwh_per_kh,
                self.model.heat_pump_split, self.ready,
            )

    def predict_kwh(self, start: datetime, minutes: float, temp_c: float | None) -> float | None:
        """Expected load in [start, start+minutes). None without a model."""
        if self.model is None:
            return None
        return self.model.predict_hour_kwh(start.hour, temp_c) * minutes / 60.0
