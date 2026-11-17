"""SoC-bucketed history of the battery's actual GRID_CHARGING throughput.

See docs/technical/energiefahrplan.md, "Gelernte Ladeleistung statt
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

# A learned value below this share of the configured charge power is not
# trusted: a real battery limit that low means something is broken (or the
# observation was), and planning with it stops grid charging altogether.
PLAUSIBLE_MIN_SHARE = 0.2
# kv_state marker: history recorded before v4.2.1 used the wrong sign and is
# discarded once.
_PURGE_KEY = "capability_sign_fix_v421"
# A GRID_CHARGING tick whose charge power reaches this share of the requested
# power was limited by the request, not by the battery – not a limit sample.
CENSORED_SHARE = 0.9


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

    def record_tick(self, mode: EMSMode, soc: float | None, charge_power_w: float,
                    requested_w: float | None = None) -> None:
        """Update today's per-bucket max – but only from unbiased ticks.

        ``charge_power_w`` is the power flowing INTO the battery (positive
        while charging). The caller converts from battery_power_entity, whose
        canonical sign is positive = discharge (roles.yaml). Before v4.2.1 the
        raw sensor value was passed, so a charge (negative) never raised the
        maximum and the learned power stayed near 0 W.

        ``requested_w`` is what miniEMS asked the inverter for this tick (V1
        stretching, fuse cap) – None means full current. A tick where the
        battery took about what was asked measures the request, not the
        battery (censored): on 17.11.2026 V1 stretched to ~1 kW after 12:30,
        while the battery had just taken 3.7 kW at full current.

        Silently ignores everything that isn't GRID_CHARGING (see module
        docstring), censored ticks and any tick without a usable SoC.
        """
        if mode is not EMSMode.GRID_CHARGING or soc is None:
            return
        if requested_w is not None and charge_power_w >= CENSORED_SHARE * requested_w:
            return
        self._roll_in_memory_day_if_needed()
        bucket = soc_bucket_for(soc)
        max_w, count = self._today_stats.get(bucket, (0.0, 0))
        self._today_stats[bucket] = (max(max_w, charge_power_w), count + 1)

    async def purge_wrong_sign_history(self) -> bool:
        """Discard every value learned before v4.2.1 (wrong sign), once.

        Returns True when something was purged in this call.
        """
        if self._store is None or await self._store.get_state(_PURGE_KEY):
            return False
        removed = await self._store.clear_capability()
        await self._store.set_state(_PURGE_KEY, {"removed_rows": removed})
        self._today_stats = {}
        _LOGGER.warning("Learned charge power: %d value(s) recorded with the wrong sign "
                        "discarded – the configured charge power applies until new data", removed)
        return True

    async def restore(self) -> None:
        """After startup: roll over days a restart skipped, reload today.

        Without it a restart (e.g. installing an update) lost today's samples –
        the next flush overwrote the stored maxima with the fresh, smaller
        in-memory ones – and a day that ended while the add-on was down never
        reached the history.
        """
        if self._store is None:
            return
        today = date.today()
        for day in await self._store.pending_capability_days(today):
            await self._store.rollover_capability_day(day, BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY)
        self._today = today
        self._today_stats = await self._store.load_capability_today(today)

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
        over-recent-days approach), today included once it qualifies. Falls
        back to `fallback_kw` – the configured current x voltage estimate,
        which stays the ceiling – when the bucket has fewer than
        BATTERY_CAPABILITY_MIN_DAYS qualifying days: a bucket a user's
        battery_max_soc never reaches (e.g. "high" at max_soc=95) must stay on
        the config value rather than guess from a neighbouring bucket.

        Since v4.2.2 one day is enough (was 3) and today counts at once: the
        samples are uncensored (record_tick), and the config value is the
        harmful guess – 185 A × 52 V = 9.6 kW against a measured 3.7 kW made
        the day plan push grid charging to the end of the window.
        """
        if self._store is None:
            return fallback_kw
        bucket = soc_bucket_for(soc)
        values = await self._store.query_capability_history(
            bucket, BATTERY_CAPABILITY_LOOKBACK_DAYS, BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY
        )
        self._roll_in_memory_day_if_needed()
        today_max, today_count = self._today_stats.get(bucket, (0.0, 0))
        if today_count >= BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY and today_max > 0:
            values = [*values, today_max]
        if len(values) < BATTERY_CAPABILITY_MIN_DAYS:
            return fallback_kw
        learned_kw = min(statistics.median(values) / 1000.0, fallback_kw) if fallback_kw > 0 \
            else statistics.median(values) / 1000.0
        if fallback_kw > 0 and learned_kw < PLAUSIBLE_MIN_SHARE * fallback_kw:
            if not getattr(self, "_warned_implausible", False):
                self._warned_implausible = True
                _LOGGER.warning("Learned charge power %.2f kW is below %.0f %% of the configured "
                                "%.1f kW – ignored, using the configured value",
                                learned_kw, PLAUSIBLE_MIN_SHARE * 100, fallback_kw)
            return fallback_kw
        return learned_kw
