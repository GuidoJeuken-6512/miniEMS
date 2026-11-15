"""A11 – maintenance runs: battery full charge and hot-water hygiene."""
from datetime import date, datetime, timedelta, timezone

import pytest

import store as store_module
from const import EMSMode, HWMode
from cost_optimizer import CostOptimizer
from store import EnergyStore

from test_ems_controller import make_controller

TZ = timezone(timedelta(hours=1))
NIGHT = datetime(2026, 11, 16, 3, 0, tzinfo=TZ)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


class TestHistory:
    @pytest.mark.asyncio
    async def test_days_since_full(self, db, make_config):
        today = date.today()
        await db.upsert_day(today - timedelta(days=20), {"max_soc_pct": 95.0})
        await db.upsert_day(today - timedelta(days=9), {"max_soc_pct": 96.0})
        await db.upsert_day(today - timedelta(days=2), {"max_soc_pct": 70.0})
        opt = CostOptimizer(make_config(), db)
        assert await opt.days_since("max_soc_pct", 94) == 9
        opt.note_maxima(95.0, None)
        assert await opt.days_since("max_soc_pct", 94) == 0

    @pytest.mark.asyncio
    async def test_never_full_counts_from_first_record(self, db, make_config):
        today = date.today()
        await db.upsert_day(today - timedelta(days=5), {"max_soc_pct": 60.0})
        opt = CostOptimizer(make_config(), db)
        assert await opt.days_since("max_soc_pct", 94) == 5

    @pytest.mark.asyncio
    async def test_fresh_install_is_not_overdue(self, db, make_config):
        opt = CostOptimizer(make_config(), db)
        assert await opt.days_since("max_soc_pct", 94) is None

    @pytest.mark.asyncio
    async def test_maxima_are_flushed(self, db, make_config):
        opt = CostOptimizer(make_config(), db)
        opt.note_maxima(80.0, 48.0)
        opt.note_maxima(85.0, 46.0)
        await opt.flush_to_db()
        row = await db.load_day(date.today())
        assert row["max_soc_pct"] == 85.0
        assert row["max_boil_temp_c"] == 48.0


class FakeOpt:
    def __init__(self, days_soc=None, days_boil=None, cheap=True):
        self._days = {"max_soc_pct": days_soc, "max_boil_temp_c": days_boil}
        self.cheap = cheap
        self.noted = []

    def note_maxima(self, soc, boil):
        self.noted.append((soc, boil))

    async def days_since(self, column, value):
        return self._days[column]

    def is_cheap_rate(self, price):
        return self.cheap


class TestFullCharge:
    @pytest.mark.asyncio
    async def test_due_after_n_days(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_full_charge_days=14,
                               battery_control_enabled=True)
        ctrl._optimizer = FakeOpt(days_soc=15)
        await ctrl._update_maintenance(NIGHT, 60.0)
        assert ctrl._full_charge_due is True
        ctrl._optimizer = FakeOpt(days_soc=3)
        ctrl._maintenance_checked_at = None
        await ctrl._update_maintenance(NIGHT, 60.0)
        assert ctrl._full_charge_due is False

    @pytest.mark.asyncio
    async def test_off_by_default(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._optimizer = FakeOpt(days_soc=99)
        await ctrl._update_maintenance(NIGHT, 60.0)
        assert ctrl._full_charge_due is False

    def test_grid_charge_in_cheap_hour_without_pv(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_control_enabled=True)
        ctrl._optimizer = FakeOpt()
        ctrl._full_charge_due = True
        fake_ws.values[ctrl._cfg.solcast_remaining_today_entity] = 0.0
        d = ctrl._decide(pv_w=0, load_w=400, bat_soc=60.0, price=0.27, bat_kwh_free=3.5, now=NIGHT)
        assert d.mode is EMSMode.GRID_CHARGING and d.reason == "maintenance full charge"

    def test_no_grid_charge_when_pv_fills(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_control_enabled=True)
        ctrl._optimizer = FakeOpt()
        ctrl._full_charge_due = True
        ctrl._remaining_load_kwh = 3.0
        fake_ws.values[ctrl._cfg.solcast_remaining_today_entity] = 20.0
        assert ctrl._full_charge_from_grid(0.27, 3.5, NIGHT) is False

    def test_not_in_an_expensive_hour(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_control_enabled=True)
        ctrl._optimizer = FakeOpt(cheap=False)
        ctrl._full_charge_due = True
        assert ctrl._full_charge_from_grid(0.39, 3.5, NIGHT) is False

    def test_pv_hold_released(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=True,
                               pv_charge_backstop_hour=17)
        ctrl._full_charge_due = True
        noon = NIGHT.replace(hour=11)
        assert ctrl._should_hold_pv_charge(60.0, 3.0, noon) == (False, "maintenance full charge")


class TestHygiene:
    def _hw(self, make_config, fake_ws, **kw):
        from hot_water_controller import HotWaterController
        cfg = make_config(boil_temp_entity="sensor.boil", hw_hygiene_enabled=True,
                          hw_hygiene_temp_c=60.0, **kw)
        hw = HotWaterController(cfg, fake_ws, None, None)
        hw._boil_c = 50.0
        return hw

    def test_starts_in_the_cheapest_slot(self, make_config, fake_ws):
        from hot_water_planner import HWSlot
        hw = self._hw(make_config, fake_ws)
        hw.set_hygiene_due(True)
        t = NIGHT
        hw._hw_slots_cache = [HWSlot(t + timedelta(minutes=30 * i), t + timedelta(minutes=30 * (i + 1)),
                                     0.27 if i < 6 else 0.34, 0.0, 5.0) for i in range(48)]
        hw._decide(NIGHT, grid_w=300.0)
        assert hw._mode is HWMode.CHARGING
        assert hw._charge.goal_c == 60.0

    def test_waits_for_a_cheaper_slot(self, make_config, fake_ws):
        from hot_water_planner import HWSlot
        hw = self._hw(make_config, fake_ws)
        hw.set_hygiene_due(True)
        t = NIGHT
        hw._hw_slots_cache = [HWSlot(t + timedelta(minutes=30 * i), t + timedelta(minutes=30 * (i + 1)),
                                     0.34 if i < 6 else 0.27, 0.0, 5.0) for i in range(48)]
        hw._decide(NIGHT, grid_w=300.0)
        assert hw._charge is None

    def test_not_due_no_charge(self, make_config, fake_ws):
        hw = self._hw(make_config, fake_ws)
        hw._decide(NIGHT, grid_w=300.0)
        assert hw._charge is None

    @pytest.mark.asyncio
    async def test_controller_sets_due(self, make_config, fake_ws):
        class HW:
            due = None

            def set_hygiene_due(self, d):
                self.due = d

        ctrl = make_controller(make_config, fake_ws, hw_hygiene_enabled=True,
                               hw_hygiene_interval_days=7)
        ctrl._optimizer = FakeOpt(days_boil=8)
        ctrl._hot_water = HW()
        await ctrl._update_maintenance(NIGHT, 50.0)
        assert ctrl._hot_water.due is True
