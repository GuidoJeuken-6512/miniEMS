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
        kw = await tracker.charge_power_kw(50.0, fallback_kw=99.0)
        assert kw == pytest.approx(3.0)   # median(2000, 3000, 4000) W -> 3.0 kW

    @pytest.mark.asyncio
    async def test_charge_power_kw_falls_back_below_min_days(self, real_store):
        yesterday = date.today() - timedelta(days=1)
        await real_store.upsert_capability_today(yesterday, "mid", 3000.0, 10)
        await real_store.rollover_capability_day(yesterday, min_samples=1)
        # Only one qualifying day – BATTERY_CAPABILITY_MIN_DAYS (3) not met.
        tracker = BatteryCapabilityTracker(real_store)
        kw = await tracker.charge_power_kw(50.0, fallback_kw=4.2)
        assert kw == 4.2

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
