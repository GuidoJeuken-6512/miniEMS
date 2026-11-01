"""Fill gaps in daily_stats from Home Assistant's long-term statistics.

A new install – or an add-on reinstalled under a different slug, which gets a
fresh /data – starts with an empty daily_stats table, so the load forecast
(consumption_model.py) has nothing to match against for weeks. HA's recorder
usually holds months of the same raw data: the inverter's lifetime load
counter (daily `change`) and the outdoor temperature sensor (daily `mean`).

Runs once at startup. Only fills what is missing – a day miniEMS measured
itself is never overwritten; today (still in progress) is never touched.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING, Any, Awaitable, Callable

import ha_ws_api

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

BACKFILL_DAYS = 60   # = consumption_model._LOOKBACK_DAYS
# A lifetime counter that was reset or replaced shows a negative or huge
# daily change; no household uses more than this per day.
_MAX_DAILY_LOAD_KWH = 200.0

StatsFetcher = Callable[[list[str], datetime, datetime], Awaitable[dict[str, list[dict[str, Any]]]]]


def _by_local_day(rows: list[dict[str, Any]], key: str) -> dict[date, float]:
    out: dict[date, float] = {}
    for r in rows or []:
        value = r.get(key)
        start = r.get("start")
        if value is None or start is None:
            continue
        out[datetime.fromtimestamp(start / 1000).date()] = float(value)
    return out


def plan_backfill(
    existing: dict[str, dict[str, Any]],
    load_by_day: dict[date, float],
    temp_by_day: dict[date, float],
    today: date,
) -> dict[date, dict[str, float]]:
    """Fields to write per day: load only where none was measured, temperature
    only where none was recorded, implausible counter deltas dropped."""
    plan: dict[date, dict[str, float]] = {}
    for day in sorted(set(load_by_day) | set(temp_by_day)):
        if day >= today:
            continue
        row = existing.get(str(day), {})
        fields: dict[str, float] = {}
        load = load_by_day.get(day)
        if (row.get("load_total_kwh") or 0) <= 0 and load is not None \
                and 0 < load <= _MAX_DAILY_LOAD_KWH:
            fields["load_total_kwh"] = round(load, 3)
        temp = temp_by_day.get(day)
        if row.get("avg_outdoor_temp_c") is None and temp is not None:
            fields["avg_outdoor_temp_c"] = round(temp, 2)
        if fields:
            plan[day] = fields
    return plan


async def backfill_daily_stats(
    cfg: "Config",
    store: "EnergyStore",
    fetch: StatsFetcher | None = None,
    today: date | None = None,
) -> int:
    """Returns the number of days written. Never raises – a failed backfill
    only means the forecast keeps learning from scratch."""
    today = today or date.today()
    load_id = cfg.load_consumption_total_entity
    temp_id = cfg.outdoor_temp_entity
    ids = [i for i in (load_id, temp_id) if i]
    if not ids:
        return 0
    if fetch is None:
        async def fetch(stat_ids, start, end):
            return await ha_ws_api.get_daily_statistics(stat_ids, start, end, cfg.long_lived_token)

    start = datetime.combine(today - timedelta(days=BACKFILL_DAYS), time.min).astimezone()
    end = datetime.combine(today, time.min).astimezone()
    try:
        stats = await fetch(ids, start, end)
    except Exception as exc:
        _LOGGER.warning("History backfill skipped – HA statistics unavailable: %s", exc)
        return 0

    existing = {r["date"]: r for r in await store.query_recent_days(BACKFILL_DAYS + 1)}
    plan = plan_backfill(
        existing,
        _by_local_day(stats.get(load_id, []), "change") if load_id else {},
        _by_local_day(stats.get(temp_id, []), "mean") if temp_id else {},
        today,
    )
    for day, fields in plan.items():
        await store.upsert_day(day, fields)

    n_load = sum("load_total_kwh" in f for f in plan.values())
    n_temp = sum("avg_outdoor_temp_c" in f for f in plan.values())
    if plan:
        _LOGGER.info(
            "History backfill from HA statistics: %d day(s) – load %d (%s), "
            "outdoor temp %d (%s)",
            len(plan), n_load, load_id or "-", n_temp, temp_id or "not configured",
        )
    else:
        _LOGGER.info("History backfill: nothing to fill (last %d days complete)", BACKFILL_DAYS)
    return len(plan)
