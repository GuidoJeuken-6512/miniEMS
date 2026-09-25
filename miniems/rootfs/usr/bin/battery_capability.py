"""SoC-bucketed history of the battery's actual GRID_CHARGING throughput.

See docs/roadmap/energiefahrplan.md, "Gelernte Ladeleistung statt
Konfigurationswert", for the full rationale. Short version:
battery_max_charge_current_a is what miniEMS *asks* the inverter for, not
what the battery actually *accepts* – a BMS can silently cap far below it.
Live-observing battery_power doesn't work either: at any moment its value is
min(PV surplus, grid setpoint, real battery limit), so whenever PV surplus or
a throttled setpoint is the bottleneck, that's what gets measured, not the
battery's limit (a censored observation).

Only GRID_CHARGING ticks are unbiased: miniEMS already commands full current
there, independent of PV, so the grid connection is never the bottleneck.
Every battery_power reading during GRID_CHARGING is therefore a real
limit observation at that SoC. PV_CHARGING/EXPORT_SURPLUS ticks stay excluded.

Buckets are on *absolute* SoC (0-10 / 11-89 / 90-100 %), deliberately not
relative to battery_min_soc/battery_max_soc: those are user-configurable, and
buckets tied to them would make historic data incomparable across a config
change.
"""
from __future__ import annotations

import logging
import statistics
from datetime import date
from typing import TYPE_CHECKING

from const import (
    BATTERY_CAPABILITY_LOOKBACK_DAYS,
    BATTERY_CAPABILITY_MIN_DAYS,
    BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY,
    EMSMode,
)

if TYPE_CHECKING:
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)


def soc_bucket_for(soc: float) -> str:
    """"low" (<=10%), "mid" (11-89%) or "high" (>=90%).

    Out-of-range readings (a bad sensor glitch) clamp to the nearest bucket
    instead of raising – this feeds a statistics table, not a safety check.
    """
    if soc <= 10.0:
        return "low"
    if soc >= 90.0:
        return "high"
    return "mid"


class BatteryCapabilityTracker:
    """Tracks today's per-bucket max GRID_CHARGING power and looks up the
    learned charge power for a given SoC from history.

    `store` is optional (None disables persistence entirely – in-memory only,
    used by tests and any caller that hasn't wired an EnergyStore) so this
    class always has a safe, inert default, matching the rest of the
    codebase's "None means the feature does nothing" convention (e.g.
    EMSController's `event_log` parameter).
    """

    def __init__(self, store: "EnergyStore | None") -> None:
        self._store = store
        self._today: date = date.today()
        # bucket -> (max_power_w, sample_count), today only.
        self._today_stats: dict[str, tuple[float, int]] = {}

    def record_tick(self, mode: EMSMode, soc: float | None, battery_power_w: float) -> None:
        """Update today's per-bucket max – but only from unbiased ticks.

        Silently ignores everything that isn't GRID_CHARGING (see module
        docstring) and any tick without a usable SoC.
        """
        if mode is not EMSMode.GRID_CHARGING or soc is None:
            return
        self._roll_in_memory_day_if_needed()
        bucket = soc_bucket_for(soc)
        max_w, count = self._today_stats.get(bucket, (0.0, 0))
        self._today_stats[bucket] = (max(max_w, battery_power_w), count + 1)

    def _roll_in_memory_day_if_needed(self) -> None:
        """Reset the in-memory accumulator on a date change.

        The durable day-cut (copying yesterday into history) is
        rollover_day() below, called explicitly once per local midnight by
        the caller – this just stops today's counters from silently mixing
        two different days' samples in memory between ticks.
        """
        today = date.today()
        if today != self._today:
            self._today = today
            self._today_stats = {}

    async def flush_to_db(self) -> None:
        """Persist today's accumulators. Call every tick, like
        CostOptimizer.flush_to_db() – cheap, at most 3 rows (one per bucket).
        """
        if self._store is None:
            return
        for bucket, (max_w, count) in self._today_stats.items():
            await self._store.upsert_capability_today(self._today, bucket, max_w, count)

    async def rollover_day(self, ended_day: date) -> None:
        """Copy `ended_day`'s qualifying buckets into permanent history.

        Call once per local midnight, passing the day that just ended – same
        trigger as EMSController's event-log cleanup. Buckets with fewer
        than BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY samples are dropped
        rather than carried forward: a day with, say, one GRID_CHARGING tick
        in a bucket is not a reliable ceiling estimate.
        """
        if self._store is None:
            return
        await self._store.rollover_capability_day(ended_day, BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY)

    async def charge_power_kw(self, soc: float, fallback_kw: float) -> float:
        """Learned charge power (kW) for this SoC, or `fallback_kw`.

        Median over the last BATTERY_CAPABILITY_LOOKBACK_DAYS qualifying days
        for soc's bucket (mirrors ConsumptionModel._predict_load()'s median-
        over-recent-days approach). Falls back to `fallback_kw` – the
        configured current x voltage estimate – when the bucket has fewer
        than BATTERY_CAPABILITY_MIN_DAYS qualifying days: a bucket a user's
        battery_max_soc never reaches (e.g. "high" at max_soc=95) must stay on
        the known-conservative config value rather than guess from a
        neighbouring bucket.
        """
        if self._store is None:
            return fallback_kw
        bucket = soc_bucket_for(soc)
        values = await self._store.query_capability_history(
            bucket, BATTERY_CAPABILITY_LOOKBACK_DAYS, BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY
        )
        if len(values) < BATTERY_CAPABILITY_MIN_DAYS:
            return fallback_kw
        return statistics.median(values) / 1000.0
