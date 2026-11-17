"""SoC-bucketed history of GRID_CHARGING throughput – see battery_capability.py."""
from datetime import date, timedelta

import pytest

import store as store_module
from battery_capability import BatteryCapabilityTracker, soc_bucket_for
from const import EMSMode
from store import EnergyStore


class TestSocBucketFor:
    def test_boundary_values_from_the_roadmap_doc(self):
        # 10%/11%/89%/90% -> low/mid/mid/high, exactly as specified.
        assert soc_bucket_for(10) == "low"
        assert soc_bucket_for(11) == "mid"
        assert soc_bucket_for(89) == "mid"
        assert soc_bucket_for(90) == "high"

    def test_clamps_out_of_range_readings(self):
        assert soc_bucket_for(-5) == "low"
        assert soc_bucket_for(150) == "high"


class TestRecordTick:
    def test_ignores_non_grid_charging_ticks(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.PV_CHARGING, 50.0, 5000.0)
        assert tracker._today_stats == {}

    def test_ignores_ticks_without_soc(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, None, 5000.0)
        assert tracker._today_stats == {}

    def test_records_grid_charging_tick_into_its_bucket(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)
        assert tracker._today_stats["mid"] == (3000.0, 1)

    def test_tracks_max_not_sum_across_ticks(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)
        tracker.record_tick(EMSMode.GRID_CHARGING, 55.0, 2000.0)   # lower -> ignored for max
        tracker.record_tick(EMSMode.GRID_CHARGING, 60.0, 4000.0)   # higher -> new max
        assert tracker._today_stats["mid"] == (4000.0, 3)

    def test_separates_buckets_independently(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 5.0, 1000.0)     # low
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)    # mid
        tracker.record_tick(EMSMode.GRID_CHARGING, 95.0, 500.0)     # high
        assert tracker._today_stats["low"] == (1000.0, 1)
        assert tracker._today_stats["mid"] == (3000.0, 1)
        assert tracker._today_stats["high"] == (500.0, 1)

    def test_censored_tick_is_ignored(self):
        """V1 asked for 1 kW and got 1 kW – that measures the request."""
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 30.0, 1015.0, requested_w=1050.0)
        assert tracker._today_stats == {}

    def test_tick_below_the_request_is_a_limit_sample(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 30.0, 3700.0, requested_w=13800.0)
        assert tracker._today_stats["mid"] == (3700.0, 1)

    def test_date_change_resets_in_memory_accumulator(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)
        tracker._today = date.today() - timedelta(days=1)   # simulate yesterday's leftover state
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 1000.0)
        assert tracker._today_stats["mid"] == (1000.0, 1)   # reset, not merged with 3000.0
        assert tracker._today == date.today()


class TestFlushAndRolloverWithoutStore:
    @pytest.mark.asyncio
    async def test_flush_is_a_noop_without_a_store(self):
        tracker = BatteryCapabilityTracker(None)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)
        await tracker.flush_to_db()   # must not raise

    @pytest.mark.asyncio
    async def test_rollover_is_a_noop_without_a_store(self):
        tracker = BatteryCapabilityTracker(None)
        await tracker.rollover_day(date.today())   # must not raise

    @pytest.mark.asyncio
    async def test_charge_power_kw_returns_fallback_without_a_store(self):
        tracker = BatteryCapabilityTracker(None)
        assert await tracker.charge_power_kw(50.0, fallback_kw=4.2) == 4.2


@pytest.fixture
async def real_store(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


class TestFlushAndRolloverWithStore:
    @pytest.mark.asyncio
    async def test_flush_persists_todays_buckets(self, real_store):
        tracker = BatteryCapabilityTracker(real_store)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 3000.0)
        await tracker.flush_to_db()
        # Not yet in history – only rollover_day() promotes it.
        assert await real_store.query_capability_history("mid", 14, 1) == []

    @pytest.mark.asyncio
    async def test_rollover_promotes_yesterdays_flushed_data(self, real_store):
        # Simulates a day already ended and flushed – set state directly
        # rather than via record_tick(), which re-syncs _today to the real
        # wall-clock date on every call and would immediately undo this.
        tracker = BatteryCapabilityTracker(real_store)
        yesterday = date.today() - timedelta(days=1)
        tracker._today = yesterday
        tracker._today_stats = {"mid": (3000.0, 6)}   # meets MIN_SAMPLES_PER_DAY
        await tracker.flush_to_db()
        await tracker.rollover_day(yesterday)
        assert await real_store.query_capability_history("mid", 14, 1) == [3000.0]

    @pytest.mark.asyncio
    async def test_charge_power_kw_uses_median_over_history(self, real_store):
        for i, watts in enumerate((2000.0, 3000.0, 4000.0)):
            day = date.today() - timedelta(days=i + 1)
            await real_store.upsert_capability_today(day, "mid", watts, 10)
            await real_store.rollover_capability_day(day, min_samples=1)
        tracker = BatteryCapabilityTracker(real_store)
        # Fallback 9 kW: the learned 3 kW stays above the plausibility floor (20 %).
        kw = await tracker.charge_power_kw(50.0, fallback_kw=9.0)
        assert kw == pytest.approx(3.0)   # median(2000, 3000, 4000) W -> 3.0 kW

    @pytest.mark.asyncio
    async def test_one_qualifying_day_is_enough(self, real_store):
        yesterday = date.today() - timedelta(days=1)
        await real_store.upsert_capability_today(yesterday, "mid", 3700.0, 10)
        await real_store.rollover_capability_day(yesterday, min_samples=1)
        tracker = BatteryCapabilityTracker(real_store)
        assert await tracker.charge_power_kw(50.0, fallback_kw=9.6) == pytest.approx(3.7)

    @pytest.mark.asyncio
    async def test_no_data_falls_back(self, real_store):
        tracker = BatteryCapabilityTracker(real_store)
        assert await tracker.charge_power_kw(50.0, fallback_kw=9.6) == 9.6

    @pytest.mark.asyncio
    async def test_today_counts_once_it_qualifies(self, real_store):
        """17.11.2026: 3.7 kW at full current – used the same day, not after 3."""
        tracker = BatteryCapabilityTracker(real_store)
        for _ in range(5):
            tracker.record_tick(EMSMode.GRID_CHARGING, 30.0, 3700.0)
        assert await tracker.charge_power_kw(30.0, fallback_kw=9.6) == 9.6   # 5 < 6 samples
        tracker.record_tick(EMSMode.GRID_CHARGING, 30.0, 3700.0)
        assert await tracker.charge_power_kw(30.0, fallback_kw=9.6) == pytest.approx(3.7)

    @pytest.mark.asyncio
    async def test_config_value_stays_the_ceiling(self, real_store):
        yesterday = date.today() - timedelta(days=1)
        await real_store.upsert_capability_today(yesterday, "mid", 12000.0, 10)
        await real_store.rollover_capability_day(yesterday, min_samples=1)
        tracker = BatteryCapabilityTracker(real_store)
        assert await tracker.charge_power_kw(50.0, fallback_kw=9.6) == pytest.approx(9.6)

    @pytest.mark.asyncio
    async def test_restore_reloads_today_and_rolls_over_skipped_days(self, real_store):
        two_days_ago = date.today() - timedelta(days=2)
        await real_store.upsert_capability_today(two_days_ago, "mid", 3600.0, 10)
        await real_store.upsert_capability_today(date.today(), "mid", 3700.0, 8)
        tracker = BatteryCapabilityTracker(real_store)
        await tracker.restore()
        assert await real_store.query_capability_history("mid", 14, 1) == [3600.0]
        assert await real_store.pending_capability_days(date.today()) == []
        assert tracker._today_stats["mid"] == (3700.0, 8)
        tracker.record_tick(EMSMode.GRID_CHARGING, 50.0, 1500.0)
        assert tracker._today_stats["mid"] == (3700.0, 9)          # merged, not overwritten

    @pytest.mark.asyncio
    async def test_charge_power_kw_falls_back_for_empty_bucket(self, real_store):
        """A bucket a user's battery_max_soc never reaches (e.g. "high") must
        stay on the config fallback, not borrow a neighbouring bucket."""
        for i in range(3):
            day = date.today() - timedelta(days=i + 1)
            await real_store.upsert_capability_today(day, "mid", 3000.0, 10)
            await real_store.rollover_capability_day(day, min_samples=1)
        tracker = BatteryCapabilityTracker(real_store)
        kw = await tracker.charge_power_kw(95.0, fallback_kw=4.2)   # "high" bucket, empty
        assert kw == 4.2


class TestSignFixV421:
    """v4.2.1: the raw battery_power (positive = discharge) was recorded, so a
    grid charge never raised the learned maximum – miniEMS then planned with
    ~0.1 kW and stopped grid charging altogether."""

    @pytest.fixture
    async def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
        s = EnergyStore()
        await s.open()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_controller_passes_charge_power_positive(self, make_config, fake_ws):
        from test_ems_controller_update import build_controller
        ctrl, *_ = build_controller(make_config, fake_ws)
        ctrl._mode = EMSMode.GRID_CHARGING
        recorded = []
        ctrl._capability.record_tick = lambda mode, soc, w, req=None: recorded.append((mode, w))
        fake_ws.values.update({"sensor.pv": 0.0, "sensor.load": 500.0, "sensor.grid": 6000.0,
                               "sensor.soc": 50.0, "sensor.batp": -5500.0, "sensor.price": 0.30})
        await ctrl.update()
        assert recorded and recorded[0][1] == 5500.0

    @pytest.mark.asyncio
    async def test_wrong_sign_history_is_purged_once(self, db):
        for i in range(4):
            await db.upsert_capability_today(date.today() - timedelta(days=i + 1), "mid", 112.0, 50)
            await db.rollover_capability_day(date.today() - timedelta(days=i + 1), 6)
        tracker = BatteryCapabilityTracker(db)
        assert await tracker.charge_power_kw(50.0, 0.5) == pytest.approx(0.112)   # before
        assert await tracker.purge_wrong_sign_history() is True
        assert await tracker.charge_power_kw(50.0, 9.0) == 9.0
        assert await tracker.purge_wrong_sign_history() is False   # only once

    @pytest.mark.asyncio
    async def test_implausibly_low_learned_value_is_ignored(self, db):
        for i in range(4):
            day = date.today() - timedelta(days=i + 1)
            await db.upsert_capability_today(day, "mid", 112.0, 50)
            await db.rollover_capability_day(day, 6)
        tracker = BatteryCapabilityTracker(db)
        assert await tracker.charge_power_kw(50.0, 9.0) == 9.0           # 0.112 < 20 % of 9
        assert await tracker.charge_power_kw(50.0, 0.4) == pytest.approx(0.112)
