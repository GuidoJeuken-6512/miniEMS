"""CostOptimizer: tick accounting, lifetime-counter anchoring, discharge tariff."""
from datetime import date, datetime, timedelta, timezone

import pytest

from cost_optimizer import CostOptimizer


class FakeStore:
    def __init__(self) -> None:
        self.days: dict[str, dict] = {}
        self.recent_days: list[dict] = []
        self.month: dict = {}
        self.year: dict = {}

    async def load_day(self, day):
        return self.days.get(str(day), {})

    async def upsert_day(self, day, fields):
        self.days.setdefault(str(day), {}).update(fields)

    async def query_recent_days(self, n):
        return self.recent_days

    async def query_month(self, year_month):
        return self.month

    async def query_year(self, year):
        return self.year


@pytest.fixture
def opt(make_config):
    cfg = make_config(
        pv_power_entity="sensor.pv", grid_power_entity="sensor.grid",
        load_power_entity="sensor.load", battery_power_entity="sensor.bat",
        cheap_rate_threshold_eur=0.10, medium_rate_threshold_eur=0.20,
        feed_in_tariff_eur_kwh=0.08,
    )
    return CostOptimizer(cfg, FakeStore())


class TestRecordTickBasics:
    def test_grid_import_accumulates_kwh_and_cost(self, opt):
        opt.record_tick(
            grid_power_w=1000.0, pv_power_w=0.0, load_power_w=1000.0, battery_power_w=0.0,
            price_eur_kwh=0.30, interval_sec=3600,
        )
        assert opt.today_grid_import_kwh() == pytest.approx(1.0)
        assert opt.today_grid_cost_eur() == pytest.approx(0.30)

    def test_pv_used_kwh_is_min_of_pv_and_load(self, opt):
        opt.record_tick(
            grid_power_w=0.0, pv_power_w=2000.0, load_power_w=500.0, battery_power_w=0.0,
            price_eur_kwh=0.30, interval_sec=3600,
        )
        assert opt.today_pv_used_kwh() == pytest.approx(0.5)
        assert opt.today_pv_saved_eur() == pytest.approx(0.15)

    def test_load_cost_uses_current_price_every_tick(self, opt):
        opt.record_tick(
            grid_power_w=0.0, pv_power_w=0.0, load_power_w=1000.0, battery_power_w=0.0,
            price_eur_kwh=0.20, interval_sec=3600,
        )
        assert opt.today_load_cost_eur() == pytest.approx(0.20)
        assert opt.today_load_total_kwh() == pytest.approx(1.0)

    def test_peak_pv_tracks_maximum_not_sum(self, opt):
        # Steps of exactly 500 W stay under SensorValidator's spike threshold
        # (delta > 500 W AND ratio > 50%), so none of these get rejected.
        for pv in (500, 1000, 1500, 2000, 2500, 3000):
            opt.record_tick(grid_power_w=0, pv_power_w=pv, load_power_w=0, battery_power_w=0,
                             price_eur_kwh=0.1, interval_sec=30)
        # A drop back down IS a spike and is rejected (validated value -> 0.0),
        # so it must not pull the tracked peak back down.
        opt.record_tick(grid_power_w=0, pv_power_w=200, load_power_w=0, battery_power_w=0,
                         price_eur_kwh=0.1, interval_sec=30)
        assert opt._peak_pv_w[date.today()] == 3000

    def test_price_tier_accumulation_low_medium_high(self, opt):
        # load_power_w held constant across ticks so SensorValidator never
        # rejects a reading here – only price varies between ticks.
        opt.record_tick(grid_power_w=0, pv_power_w=0, load_power_w=1000, battery_power_w=0,
                         price_eur_kwh=0.05, interval_sec=3600)   # low
        opt.record_tick(grid_power_w=0, pv_power_w=0, load_power_w=1000, battery_power_w=0,
                         price_eur_kwh=0.15, interval_sec=3600)   # medium
        opt.record_tick(grid_power_w=0, pv_power_w=0, load_power_w=1000, battery_power_w=0,
                         price_eur_kwh=0.25, interval_sec=3600)   # high
        today = date.today()
        assert opt._kwh_low_rate[today] == pytest.approx(1.0)
        assert opt._kwh_medium_rate[today] == pytest.approx(1.0)
        assert opt._kwh_high_rate[today] == pytest.approx(1.0)

    def test_feed_in_from_negative_grid_power(self, opt):
        opt.record_tick(grid_power_w=-1000.0, pv_power_w=2000.0, load_power_w=0.0, battery_power_w=0.0,
                         price_eur_kwh=0.30, interval_sec=3600)
        assert opt.today_feed_in_kwh() == pytest.approx(1.0)
        assert opt.today_feed_in_revenue_eur() == pytest.approx(0.08)

    def test_feed_in_prefers_ha_sensor_over_accumulation(self, opt):
        opt.record_tick(grid_power_w=-1000.0, pv_power_w=2000.0, load_power_w=0.0, battery_power_w=0.0,
                         price_eur_kwh=0.30, interval_sec=3600, feed_in_kwh_ha=42.0)
        assert opt.today_feed_in_kwh() == 42.0

    def test_grid_charge_derived_from_battery_and_pv_surplus(self, opt):
        # PV surplus 500 W, battery charging at 800 W (bat_w negative = charging)
        # -> 300 W must come from the grid
        opt.record_tick(grid_power_w=300.0, pv_power_w=1500.0, load_power_w=1000.0, battery_power_w=-800.0,
                         price_eur_kwh=0.30, interval_sec=3600)
        assert opt.today_grid_charge_kwh() == pytest.approx(0.3)

    def test_no_grid_charge_when_pv_surplus_covers_battery(self, opt):
        opt.record_tick(grid_power_w=0.0, pv_power_w=2000.0, load_power_w=500.0, battery_power_w=-800.0,
                         price_eur_kwh=0.30, interval_sec=3600)
        assert opt.today_grid_charge_kwh() == 0.0

    def test_discharge_weighted_accumulation_only_while_discharging(self, opt):
        opt.record_tick(grid_power_w=0, pv_power_w=0, load_power_w=1000, battery_power_w=1000.0,
                         price_eur_kwh=0.40, interval_sec=3600)   # discharging
        opt.record_tick(grid_power_w=0, pv_power_w=1000, load_power_w=0, battery_power_w=-500.0,
                         price_eur_kwh=0.10, interval_sec=3600)   # charging, must not count
        assert opt._discharge_kwh_ticks[date.today()] == pytest.approx(1.0)
        assert opt._discharge_value_eur[date.today()] == pytest.approx(0.40)

    def test_spike_is_not_accumulated(self, opt):
        opt.record_tick(grid_power_w=0, pv_power_w=1000, load_power_w=0, battery_power_w=0,
                         price_eur_kwh=0.1, interval_sec=30)
        # A 50x spike on pv must be rejected by SensorValidator and treated as 0
        opt.record_tick(grid_power_w=0, pv_power_w=50000, load_power_w=0, battery_power_w=0,
                         price_eur_kwh=0.1, interval_sec=30)
        assert opt._peak_pv_w[date.today()] == 1000

    def test_efficiency_from_production_and_losses(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, today_production_kwh_ha=10.0, today_losses_kwh_ha=1.0,
        )
        assert opt.today_efficiency() == pytest.approx(0.9)

    def test_efficiency_none_when_production_zero(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, today_production_kwh_ha=0.0, today_losses_kwh_ha=0.0,
        )
        assert opt.today_efficiency() is None

    def test_bilanz_computed_when_ha_sensors_available(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=1000, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=3600, load_total_kwh_ha=5.0, grid_import_kwh_ha=8.0, bat_discharge_kwh_ha=2.0,
        )
        # bilanz = grid_import - load + bat_discharge = 8 - 5 + 2 = 5
        assert opt.today_grid_charge_kwh_bilanz() == pytest.approx(5.0)


class TestLifetimeCounterAnchoring:
    def test_cold_start_anchors_from_daily_sensor(self, opt):
        today = date.today()
        # lifetime=100, daily-so-far=10 -> anchor=90 -> delta=10
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=100.0, grid_import_kwh_ha=10.0,
        )
        assert opt.today_grid_import_kwh() == pytest.approx(10.0)

    def test_delta_increases_as_total_rises(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=100.0, grid_import_kwh_ha=10.0,
        )
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=102.5, grid_import_kwh_ha=12.5,
        )
        assert opt.today_grid_import_kwh() == pytest.approx(12.5)

    def test_single_tick_backwards_glitch_is_ignored(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=100.0, grid_import_kwh_ha=10.0,
        )
        # A single bad read below the anchor must not re-anchor or move the delta
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=5.0, grid_import_kwh_ha=10.0,
        )
        assert opt.today_grid_import_kwh() == pytest.approx(10.0)   # unchanged from tick 1

    def test_persistent_drop_is_treated_as_a_real_reset(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=100.0, grid_import_kwh_ha=10.0,
        )
        # Two consecutive readings below the anchor -> genuine reset, re-anchor
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=1.0, grid_import_kwh_ha=0.5,
        )
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=2.0, grid_import_kwh_ha=1.5,
        )
        # After re-anchoring at (1.0 - 0.5)=0.5, a total of 2.0 gives delta 1.5
        assert opt.today_grid_import_kwh() == pytest.approx(1.5)

    def test_no_lifetime_counter_falls_back_to_ha_daily_sensor(self, opt):
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_kwh_ha=7.0,   # no *_total_kwh_ha
        )
        assert opt.today_grid_import_kwh() == pytest.approx(7.0)

    def test_day_rollover_anchors_fresh_without_daily_sensor_bootstrap(self, opt):
        today = date.today()
        opt._total_anchor["grid_import"] = (today - timedelta(days=1), 50.0)
        opt.record_tick(
            grid_power_w=0, pv_power_w=0, load_power_w=0, battery_power_w=0, price_eur_kwh=0.1,
            interval_sec=30, grid_import_total_kwh_ha=120.0, grid_import_kwh_ha=999.0,
        )
        # New day: anchor = current total (120), NOT total - daily_sensor,
        # so delta is 0 right at rollover, not "999" from yesterday's daily sensor.
        assert opt.today_grid_import_kwh() == pytest.approx(0.0)


class TestDischargeTariff:
    @pytest.mark.asyncio
    async def test_configured_value_wins(self, opt):
        opt._cfg.avg_discharge_tariff_eur_kwh = 0.35
        assert await opt.avg_discharge_tariff_eur_kwh() == 0.35

    @pytest.mark.asyncio
    async def test_derived_from_history_when_not_configured(self, opt):
        opt._store.recent_days = [
            {"discharge_value_eur": 3.0, "discharge_kwh_ticks": 10.0},
            {"discharge_value_eur": 2.0, "discharge_kwh_ticks": 5.0},
        ]
        value = await opt.avg_discharge_tariff_eur_kwh()
        assert value == pytest.approx(5.0 / 15.0)

    @pytest.mark.asyncio
    async def test_none_without_any_discharge_history(self, opt):
        opt._store.recent_days = []
        assert await opt.avg_discharge_tariff_eur_kwh() is None

    @pytest.mark.asyncio
    async def test_below_cheap_threshold_is_rejected_as_implausible(self, opt):
        opt._cfg.cheap_rate_threshold_eur = 0.10
        opt._store.recent_days = [
            {"discharge_value_eur": 0.5, "discharge_kwh_ticks": 10.0},   # 0.05 €/kWh
        ]
        assert await opt.avg_discharge_tariff_eur_kwh() is None

    @pytest.mark.asyncio
    async def test_result_is_cached_per_day(self, opt):
        opt._store.recent_days = [{"discharge_value_eur": 3.0, "discharge_kwh_ticks": 10.0}]
        first = await opt.avg_discharge_tariff_eur_kwh()
        opt._store.recent_days = []   # change underlying data
        second = await opt.avg_discharge_tariff_eur_kwh()
        assert first == second   # cache hit, not recomputed


class TestRoi:
    def test_roi_positive_when_savings_exceed_cost(self, opt):
        opt._grid_charge_kwh_bilanz[date.today()] = 5.0
        opt._efficiency[date.today()] = 0.9
        opt._cfg.avg_discharge_tariff_eur_kwh = 0.35
        opt._grid_charge_cost_eur[date.today()] = 1.0
        roi = opt.today_grid_charge_roi_eur()
        # usable = 5*0.9=4.5, saving=4.5*0.35=1.575, roi=1.575-1.0=0.575
        assert roi == pytest.approx(0.575)

    def test_roi_none_without_bilanz(self, opt):
        assert opt.today_grid_charge_roi_eur() is None

    def test_roi_none_without_efficiency(self, opt):
        opt._grid_charge_kwh_bilanz[date.today()] = 5.0
        assert opt.today_grid_charge_roi_eur() is None

    def test_roi_none_without_configured_discharge_tariff(self, opt):
        opt._grid_charge_kwh_bilanz[date.today()] = 5.0
        opt._efficiency[date.today()] = 0.9
        opt._cfg.avg_discharge_tariff_eur_kwh = 0.0
        assert opt.today_grid_charge_roi_eur() is None


class TestRateHelpers:
    def test_is_cheap_rate(self, opt):
        assert opt.is_cheap_rate(0.05) is True
        assert opt.is_cheap_rate(0.15) is False
        assert opt.is_cheap_rate(None) is False

    def test_price_tier(self, opt):
        assert opt.price_tier(0.05) == "low"
        assert opt.price_tier(0.15) == "medium"
        assert opt.price_tier(0.30) == "high"
        assert opt.price_tier(None) is None


class TestSummary:
    def test_summary_basic_fields(self, opt):
        opt.record_tick(grid_power_w=1000, pv_power_w=0, load_power_w=1000, battery_power_w=0,
                         price_eur_kwh=0.30, interval_sec=3600)
        s = opt.summary()
        assert s["today_grid_cost_eur"] == pytest.approx(0.30)
        assert "today_grid_charge_roi_eur" not in s   # no bilanz/efficiency yet

    def test_summary_includes_bilanz_and_roi_when_available(self, opt):
        opt._grid_charge_kwh_bilanz[date.today()] = 5.0
        opt._efficiency[date.today()] = 0.9
        opt._cfg.avg_discharge_tariff_eur_kwh = 0.35
        s = opt.summary()
        assert "today_grid_charge_kwh_bilanz" in s
        assert "today_efficiency_pct" in s
        assert "today_grid_charge_roi_eur" in s

    @pytest.mark.asyncio
    async def test_summary_with_db_adds_month_and_year(self, opt):
        opt._store.month = {"grid_cost_eur": 12.0}
        opt._store.year = {"grid_cost_eur": 100.0}
        s = await opt.summary_with_db()
        assert s["month_grid_cost_eur"] == 12.0
        assert s["year_grid_cost_eur"] == 100.0


class TestRestoreToday:
    @pytest.mark.asyncio
    async def test_restore_repopulates_accumulators(self, opt):
        today = date.today()
        opt._store.days[str(today)] = {
            "grid_import_kwh": 3.0, "grid_cost_eur": 0.9, "peak_pv_w": 2500.0,
        }
        await opt.restore_today()
        assert opt.today_grid_import_kwh() == 3.0
        assert opt._peak_pv_w[today] == 2500.0

    @pytest.mark.asyncio
    async def test_restore_noop_when_no_row_exists(self, opt):
        await opt.restore_today()   # must not raise
        assert opt.today_grid_import_kwh() == 0.0

    @pytest.mark.asyncio
    async def test_restore_detects_downtime_gap(self, opt):
        today = date.today()
        old_ts = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
        opt._store.days[str(today)] = {"last_flush_ts": old_ts}
        opt._cfg.update_interval_sec = 30
        await opt.restore_today()
        assert any("Data gap" in w for w in opt.get_startup_warnings())

    @pytest.mark.asyncio
    async def test_restore_no_gap_warning_for_recent_flush(self, opt):
        today = date.today()
        recent_ts = datetime.now(timezone.utc).isoformat()
        opt._store.days[str(today)] = {"last_flush_ts": recent_ts}
        opt._cfg.update_interval_sec = 30
        await opt.restore_today()
        assert opt.get_startup_warnings() == []
