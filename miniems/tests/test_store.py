"""EnergyStore: SQLite persistence, against a real temp-file database."""
from datetime import date, timedelta

import pytest

import store as store_module
from store import EnergyStore


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


@pytest.mark.asyncio
async def test_open_creates_tables_idempotently(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    await s.open()   # second open must not raise on "column already exists"
    await s.close()


@pytest.mark.asyncio
async def test_upsert_then_load_day(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 1.5, "grid_cost_eur": 0.3})
    row = await db.load_day(today)
    assert row["grid_import_kwh"] == 1.5
    assert row["grid_cost_eur"] == 0.3


@pytest.mark.asyncio
async def test_upsert_overwrites_existing_day(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 1.0})
    await db.upsert_day(today, {"grid_import_kwh": 2.0})
    row = await db.load_day(today)
    assert row["grid_import_kwh"] == 2.0


@pytest.mark.asyncio
async def test_load_day_missing_returns_empty_dict(db):
    assert await db.load_day(date(2000, 1, 1)) == {}


@pytest.mark.asyncio
async def test_query_recent_days_respects_cutoff(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 1.0})
    await db.upsert_day(today - timedelta(days=100), {"grid_import_kwh": 5.0})
    rows = await db.query_recent_days(14)
    dates = [r["date"] for r in rows]
    assert str(today) in dates
    assert str(today - timedelta(days=100)) not in dates


@pytest.mark.asyncio
async def test_query_recent_days_newest_first(db):
    today = date.today()
    await db.upsert_day(today - timedelta(days=2), {"grid_import_kwh": 1.0})
    await db.upsert_day(today, {"grid_import_kwh": 3.0})
    await db.upsert_day(today - timedelta(days=1), {"grid_import_kwh": 2.0})
    rows = await db.query_recent_days(14)
    assert rows[0]["date"] == str(today)


@pytest.mark.asyncio
async def test_query_all_days(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 1.0})
    rows = await db.query_all_days()
    assert len(rows) == 1


@pytest.mark.asyncio
async def test_query_month_sums_matching_days(db):
    today = date.today()
    ym = today.strftime("%Y-%m")
    await db.upsert_day(today, {"grid_import_kwh": 1.0, "grid_cost_eur": 0.2})
    result = await db.query_month(ym)
    assert result["grid_import_kwh"] == 1.0
    assert result["grid_cost_eur"] == 0.2


@pytest.mark.asyncio
async def test_query_month_no_matches_returns_zeros(db):
    result = await db.query_month("1999-01")
    assert result["grid_import_kwh"] == 0.0


@pytest.mark.asyncio
async def test_query_year_sums_matching_days(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 4.0})
    result = await db.query_year(today.year)
    assert result["grid_import_kwh"] == 4.0


@pytest.mark.asyncio
async def test_query_days_similar_temp_filters_by_tolerance(db):
    today = date.today()
    await db.upsert_day(today, {"avg_outdoor_temp_c": 10.0})
    await db.upsert_day(today - timedelta(days=1), {"avg_outdoor_temp_c": 25.0})
    rows = await db.query_days_similar_temp(target_temp_c=10.0, tolerance=2.0, lookback_days=14)
    assert len(rows) == 1
    assert rows[0]["avg_outdoor_temp_c"] == 10.0


@pytest.mark.asyncio
async def test_query_days_similar_temp_excludes_null_temp(db):
    today = date.today()
    await db.upsert_day(today, {"grid_import_kwh": 1.0})   # no temp column set
    rows = await db.query_days_similar_temp(target_temp_c=10.0, tolerance=5.0, lookback_days=14)
    assert rows == []


@pytest.mark.asyncio
async def test_append_and_load_recent_events(db):
    entry = dict(
        timestamp="2026-08-27T10:00:00+00:00", entry_type="mode_change", state="on",
        battery_kwh_freetochange=1.0, battery_kwh_useable=2.0, predicted_load_kwh=None,
        price_eur_kwh=None, mode="Idle", reason="startup",
        write_channel=None, write_latency_sec=None, write_outcome=None,
    )
    await db.append_event(entry)
    rows = await db.load_recent_events(limit=10)
    assert len(rows) == 1
    assert rows[0]["mode"] == "Idle"


@pytest.mark.asyncio
async def test_load_recent_events_respects_limit(db):
    for i in range(5):
        await db.append_event(dict(
            timestamp=f"2026-08-27T10:0{i}:00+00:00", entry_type="mode_change", state="on",
            battery_kwh_freetochange=0, battery_kwh_useable=0, predicted_load_kwh=None,
            price_eur_kwh=None, mode=str(i), reason="",
            write_channel=None, write_latency_sec=None, write_outcome=None,
        ))
    rows = await db.load_recent_events(limit=2)
    assert len(rows) == 2
    assert rows[0]["mode"] == "4"   # newest (highest id) first


@pytest.mark.asyncio
async def test_cleanup_event_log_deletes_old_rows(db):
    await db.append_event(dict(
        timestamp="2000-01-01T00:00:00+00:00", entry_type="mode_change", state="on",
        battery_kwh_freetochange=0, battery_kwh_useable=0, predicted_load_kwh=None,
        price_eur_kwh=None, mode="old", reason="",
        write_channel=None, write_latency_sec=None, write_outcome=None,
    ))
    deleted = await db.cleanup_event_log(retention_days=30)
    assert deleted == 1
    assert await db.load_recent_events(limit=10) == []


@pytest.mark.asyncio
async def test_operations_on_unopened_store_return_empty_not_raise():
    s = EnergyStore()   # never opened
    assert await s.load_day(date.today()) == {}
    assert await s.query_recent_days(7) == []
    assert await s.query_all_days() == []
    assert await s.load_recent_events() == []
    assert await s.cleanup_event_log(30) == 0
    await s.upsert_day(date.today(), {"grid_import_kwh": 1.0})   # no-op, no raise
    await s.append_event({
        "timestamp": "x", "entry_type": "mode_change", "state": "on",
        "battery_kwh_freetochange": 0, "battery_kwh_useable": 0,
    })   # no-op, no raise
