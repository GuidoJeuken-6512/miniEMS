"""load_forecast: hourly recording and the temperature-driven load model."""
from datetime import date, datetime, timedelta, timezone

import pytest

import store as store_module
from load_forecast import HourlyLoadRecorder, LoadForecaster, fit_load_model
from store import EnergyStore

TZ = timezone(timedelta(hours=1))
INTERVAL = 30
FULL_TICKS = 3600 // INTERVAL


def _rows(days: int, base=lambda h: 0.3, k=0.1, limit=15.0, temp=lambda d, h: 5.0,
          hp: bool = False, hp_base=0.05, start=date(2026, 11, 1)):
    """Synthetic hourly_stats rows following the model exactly."""
    rows = []
    for d in range(days):
        day = start + timedelta(days=d)
        for h in range(24):
            t = temp(d, h)
            hp_kwh = hp_base + k * max(0.0, limit - t)
            load = base(h) + hp_kwh if hp else base(h) + k * max(0.0, limit - t)
            rows.append({
                "hour_start": datetime(day.year, day.month, day.day, h, tzinfo=TZ).isoformat(),
                "date": str(day), "hour": h, "load_kwh": load,
                "heat_pump_kwh": hp_kwh if hp else None,
                "outdoor_temp_c": t, "ticks": FULL_TICKS,
            })
    return rows


def _vary(d, h):
    return -5.0 + (d % 7) * 3 + (h % 6)


class TestFit:
    def test_empty(self):
        assert fit_load_model([], 15.0, INTERVAL, False) is None

    def test_recovers_k_and_base_without_heat_pump_meter(self):
        base = lambda h: 0.2 if h < 6 else 0.5
        m = fit_load_model(_rows(21, base=base, k=0.12, temp=_vary), 15.0, INTERVAL, False)
        assert m.days == 21
        assert m.k_kwh_per_kh == pytest.approx(0.12, rel=0.05)
        assert m.base_by_hour[3] == pytest.approx(0.2, abs=0.05)
        assert m.base_by_hour[18] == pytest.approx(0.5, abs=0.05)
        assert not m.heat_pump_split

    def test_heat_pump_meter_split(self):
        m = fit_load_model(_rows(14, k=0.15, temp=_vary, hp=True, hp_base=0.08),
                           15.0, INTERVAL, True)
        assert m.heat_pump_split
        assert m.k_kwh_per_kh == pytest.approx(0.15, rel=0.02)
        assert m.hp_base_kwh_h == pytest.approx(0.08, abs=0.01)
        assert m.base_by_hour[12] == pytest.approx(0.3, abs=0.01)

    def test_colder_predicts_more(self):
        m = fit_load_model(_rows(14, temp=_vary), 15.0, INTERVAL, False)
        assert m.predict_day_kwh(-10.0) > m.predict_day_kwh(5.0) > m.predict_day_kwh(20.0)

    def test_above_heating_limit_only_base(self):
        m = fit_load_model(_rows(14, temp=_vary), 15.0, INTERVAL, False)
        assert m.predict_hour_kwh(12, 25.0) == pytest.approx(m.base_by_hour[12], abs=1e-9)

    def test_incomplete_hours_ignored(self):
        rows = _rows(3)
        for r in rows:
            r["ticks"] = 10        # restart fragments
        assert fit_load_model(rows, 15.0, INTERVAL, False) is None

    def test_days_count_needs_most_hours(self):
        rows = [r for r in _rows(2) if r["hour"] < 10]   # 10 h per day only
        m = fit_load_model(rows, 15.0, INTERVAL, False)
        assert m.days == 0

    def test_missing_temperature_uses_mean_hdd(self):
        m = fit_load_model(_rows(14, temp=lambda d, h: 5.0), 15.0, INTERVAL, False)
        assert m.predict_hour_kwh(12, None) == pytest.approx(m.predict_hour_kwh(12, 5.0))


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


class TestRecorder:
    @pytest.mark.asyncio
    async def test_integrates_one_hour(self, db):
        rec = HourlyLoadRecorder(db, INTERVAL)
        t0 = datetime(2026, 12, 1, 10, 0, tzinfo=TZ)
        for i in range(FULL_TICKS):
            await rec.record_tick(t0 + timedelta(seconds=INTERVAL * i), 1000.0, 400.0, 2.0)
        rows = await db.query_hours_since(date(2026, 12, 1))
        assert len(rows) == 1
        assert rows[0]["load_kwh"] == pytest.approx(1.0)
        assert rows[0]["heat_pump_kwh"] == pytest.approx(0.4)
        assert rows[0]["outdoor_temp_c"] == pytest.approx(2.0)
        assert rows[0]["ticks"] == FULL_TICKS

    @pytest.mark.asyncio
    async def test_gaps_filled_with_hour_average(self, db):
        rec = HourlyLoadRecorder(db, INTERVAL)
        t0 = datetime(2026, 12, 1, 10, 0, tzinfo=TZ)
        for i in range(FULL_TICKS):
            load = None if i % 2 else 1000.0
            await rec.record_tick(t0 + timedelta(seconds=INTERVAL * i), load, None, None)
        row = (await db.query_hours_since(date(2026, 12, 1)))[0]
        assert row["load_kwh"] == pytest.approx(1.0)
        assert row["heat_pump_kwh"] is None
        assert row["outdoor_temp_c"] is None

    @pytest.mark.asyncio
    async def test_restart_continues_hour(self, db):
        t0 = datetime(2026, 12, 1, 10, 0, tzinfo=TZ)
        rec = HourlyLoadRecorder(db, INTERVAL)
        for i in range(60):
            await rec.record_tick(t0 + timedelta(seconds=INTERVAL * i), 1000.0, None, None)
        rec2 = HourlyLoadRecorder(db, INTERVAL)   # add-on restart
        for i in range(60, FULL_TICKS):
            await rec2.record_tick(t0 + timedelta(seconds=INTERVAL * i), 1000.0, None, None)
        row = (await db.query_hours_since(date(2026, 12, 1)))[0]
        assert row["ticks"] == FULL_TICKS
        assert row["load_kwh"] == pytest.approx(1.0)

    @pytest.mark.asyncio
    async def test_new_hour_new_row(self, db):
        rec = HourlyLoadRecorder(db, INTERVAL)
        t0 = datetime(2026, 12, 1, 10, 59, 30, tzinfo=TZ)
        await rec.record_tick(t0, 1000.0, None, None)
        await rec.record_tick(t0 + timedelta(seconds=60), 1000.0, None, None)
        rows = await db.query_hours_since(date(2026, 12, 1))
        assert [r["hour"] for r in rows] == [10, 11]


class TestForecaster:
    @pytest.mark.asyncio
    async def test_ready_after_min_days(self, db, make_config):
        cfg = make_config(load_forecast_min_days=14, update_interval_sec=INTERVAL)
        for r in _rows(13, temp=_vary):
            await db.upsert_hour(r.pop("hour_start"), r)
        fc = LoadForecaster(cfg, db)
        now = datetime(2026, 11, 20, 12, tzinfo=TZ)
        await fc.refresh(now)
        assert fc.model is not None and not fc.ready
        for r in _rows(1, temp=_vary, start=date(2026, 11, 14)):
            await db.upsert_hour(r.pop("hour_start"), r)
        await fc.refresh(now + timedelta(minutes=5))      # throttled
        assert not fc.ready
        await fc.refresh(now + timedelta(hours=2))
        assert fc.ready

    @pytest.mark.asyncio
    async def test_predict_scales_with_minutes(self, db, make_config):
        cfg = make_config(update_interval_sec=INTERVAL)
        for r in _rows(14, temp=_vary):
            await db.upsert_hour(r.pop("hour_start"), r)
        fc = LoadForecaster(cfg, db)
        await fc.refresh(datetime(2026, 11, 20, 12, tzinfo=TZ))
        t = datetime(2026, 11, 20, 18, tzinfo=TZ)
        assert fc.predict_kwh(t, 30, 0.0) == pytest.approx(fc.predict_kwh(t, 60, 0.0) / 2)
