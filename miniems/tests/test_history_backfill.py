"""history_backfill.py: fill daily_stats gaps from HA long-term statistics."""
from datetime import date, datetime, timedelta

import pytest

import store as store_module
from history_backfill import backfill_daily_stats, plan_backfill
from store import EnergyStore

TODAY = date(2026, 11, 1)


def _ms(day: date) -> int:
    return int(datetime.combine(day, datetime.min.time()).timestamp() * 1000)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


class TestPlan:
    def test_fills_missing_load_and_temp(self):
        d = TODAY - timedelta(days=3)
        plan = plan_backfill({}, {d: 19.4}, {d: 8.7}, TODAY)
        assert plan == {d: {"load_total_kwh": 19.4, "avg_outdoor_temp_c": 8.7}}

    def test_never_overwrites_measured_values(self):
        d = TODAY - timedelta(days=2)
        existing = {str(d): {"load_total_kwh": 21.0, "avg_outdoor_temp_c": 9.0}}
        assert plan_backfill(existing, {d: 30.0}, {d: 1.0}, TODAY) == {}

    def test_fills_only_the_missing_half(self):
        d = TODAY - timedelta(days=2)
        existing = {str(d): {"load_total_kwh": 21.0, "avg_outdoor_temp_c": None}}
        assert plan_backfill(existing, {d: 30.0}, {d: 7.5}, TODAY) == {
            d: {"avg_outdoor_temp_c": 7.5}}

    def test_today_is_never_touched(self):
        assert plan_backfill({}, {TODAY: 5.0}, {TODAY: 9.0}, TODAY) == {}

    @pytest.mark.parametrize("bad", [-3905.8, 0.0, 450.0])
    def test_counter_reset_or_jump_is_dropped(self, bad):
        d = TODAY - timedelta(days=4)
        assert plan_backfill({}, {d: bad}, {}, TODAY) == {}


@pytest.mark.asyncio
async def test_backfill_writes_into_store(db, make_config):
    cfg = make_config(
        history_backfill_enabled=True,
        load_consumption_total_entity="sensor.load_total",
        outdoor_temp_entity="sensor.outdoor",
    )
    d1, d2 = TODAY - timedelta(days=2), TODAY - timedelta(days=1)
    await db.upsert_day(d2, {"load_total_kwh": 20.5})   # measured by miniEMS

    async def fake_fetch(ids, start, end):
        assert ids == ["sensor.load_total", "sensor.outdoor"]
        return {
            "sensor.load_total": [{"start": _ms(d1), "change": 18.2},
                                  {"start": _ms(d2), "change": 99.0}],
            "sensor.outdoor": [{"start": _ms(d1), "mean": 6.4},
                               {"start": _ms(d2), "mean": 5.7}],
        }

    written = await backfill_daily_stats(cfg, db, fetch=fake_fetch, today=TODAY)
    assert written == 2
    assert (await db.load_day(d1))["load_total_kwh"] == 18.2
    row2 = await db.load_day(d2)
    assert row2["load_total_kwh"] == 20.5            # kept
    assert row2["avg_outdoor_temp_c"] == 5.7         # filled


@pytest.mark.asyncio
async def test_backfill_survives_ha_errors(db, make_config):
    cfg = make_config(history_backfill_enabled=True,
                      load_consumption_total_entity="sensor.load_total")

    async def failing_fetch(ids, start, end):
        raise ConnectionError("boom")

    assert await backfill_daily_stats(cfg, db, fetch=failing_fetch, today=TODAY) == 0


@pytest.mark.asyncio
async def test_backfill_switched_off_does_not_query_ha(db, make_config):
    cfg = make_config(load_consumption_total_entity="sensor.load_total")   # default: off

    async def must_not_be_called(ids, start, end):
        raise AssertionError("queried HA although disabled")

    assert await backfill_daily_stats(cfg, db, fetch=must_not_be_called, today=TODAY) == 0


@pytest.mark.asyncio
async def test_second_run_changes_nothing(db, make_config):
    """Runs on every start: once the gaps are filled, a rerun is a no-op –
    even if HA's statistics changed meanwhile."""
    cfg = make_config(history_backfill_enabled=True,
                      load_consumption_total_entity="sensor.load_total")
    d = TODAY - timedelta(days=3)
    value = {"change": 18.2}

    async def fetch(ids, start, end):
        return {"sensor.load_total": [{"start": _ms(d), **value}]}

    assert await backfill_daily_stats(cfg, db, fetch=fetch, today=TODAY) == 1
    value["change"] = 25.0
    assert await backfill_daily_stats(cfg, db, fetch=fetch, today=TODAY) == 0
    assert (await db.load_day(d))["load_total_kwh"] == 18.2
