"""EMSController: mode decision logic, staleness gates, debounce, warnings."""
from datetime import datetime, timedelta, timezone

import pytest

from const import EMSMode
from ems_controller import EMSController, ModeDecision
from solcast_client import SolcastClient


class FakeOptimizer:
    def __init__(self) -> None:
        self._efficiency = 0.9
        self._discharge_tariff = None   # via EMSController._discharge_tariff, not read here
        self.startup_warnings: list[str] = []

    def is_cheap_rate(self, price):
        return price is not None and price < 0.10

    def price_tier(self, price):
        return "low" if price is not None and price < 0.10 else "high"

    def today_efficiency(self):
        return self._efficiency

    async def avg_discharge_tariff_eur_kwh(self):
        return 0.35

    def today_load_total_kwh(self):
        return 5.0

    def record_tick(self, **kwargs):
        pass

    async def flush_to_db(self):
        pass

    def get_startup_warnings(self):
        return list(self.startup_warnings)

    async def summary_with_db(self):
        return {}


def make_controller(make_config, fake_ws, **cfg_overrides):
    cfg = make_config(**cfg_overrides)
    ctrl = EMSController(cfg, fake_ws, FakeOptimizer())
    # Real SolcastClient against fake_ws – it's a trivial passthrough, so
    # tests just set fake_ws.values[cfg.solcast_*_entity] directly instead of
    # hand-rolling a stub with its own, easy-to-desync entity mapping.
    ctrl._solcast = SolcastClient(cfg, fake_ws)
    # battery_power_entity backs the SoC-liveness check in _decide() – tests
    # that pass bat_soc as a plain parameter still need this entity "alive".
    fake_ws.stale[cfg.battery_power_entity] = False
    return ctrl


@pytest.fixture
def now():
    return datetime(2026, 8, 27, 12, 0, 0, tzinfo=timezone.utc).astimezone()


class TestDecideSafety:
    def test_missing_soc_returns_idle_urgent(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws)
        d = ctrl._decide(pv_w=100, load_w=50, bat_soc=None, price=0.1, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.IDLE
        assert d.urgent is True
        assert "soc" in d.reason

    def test_stale_battery_power_returns_idle_urgent(self, make_config, fake_ws, now):
        cfg_kwargs = dict(battery_power_entity="sensor.bat")
        ctrl = make_controller(make_config, fake_ws, **cfg_kwargs)
        fake_ws.stale["sensor.bat"] = True
        d = ctrl._decide(pv_w=100, load_w=50, bat_soc=50.0, price=0.1, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.IDLE
        assert d.urgent is True

    def test_soc_below_min_triggers_protect(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, battery_min_soc=15)
        d = ctrl._decide(pv_w=0, load_w=0, bat_soc=10.0, price=0.1, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.PROTECT_BATTERY
        assert d.urgent is True

    def test_protect_hysteresis_keeps_protecting_until_above_band(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, battery_min_soc=15, battery_soc_hysteresis_pct=2)
        ctrl._mode = EMSMode.PROTECT_BATTERY
        # 16% is above min_soc(15) but still within the +2% hysteresis band
        d = ctrl._decide(pv_w=0, load_w=0, bat_soc=16.0, price=0.1, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.PROTECT_BATTERY

    def test_protect_hysteresis_releases_above_band(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, battery_min_soc=15, battery_soc_hysteresis_pct=2)
        ctrl._mode = EMSMode.PROTECT_BATTERY
        d = ctrl._decide(pv_w=0, load_w=0, bat_soc=20.0, price=0.5, bat_kwh_free=1.0, now=now)
        assert d.mode is not EMSMode.PROTECT_BATTERY

    def test_full_battery_returns_idle(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, battery_max_soc=95)
        d = ctrl._decide(pv_w=1000, load_w=0, bat_soc=95.0, price=0.5, bat_kwh_free=0.0, now=now)
        assert d.mode is EMSMode.IDLE
        assert d.reason == "battery full"


class TestDecidePvSurplus:
    def test_surplus_above_threshold_charges_from_pv(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_surplus_threshold_w=200,
                                pv_export_priority_enabled=False)
        d = ctrl._decide(pv_w=1000, load_w=200, bat_soc=50.0, price=0.5, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.PV_CHARGING

    def test_surplus_below_threshold_falls_through(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_surplus_threshold_w=500,
                                grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6)
        d = ctrl._decide(pv_w=300, load_w=200, bat_soc=50.0, price=0.5, bat_kwh_free=1.0, now=now)
        assert d.mode is EMSMode.IDLE   # no surplus, price not cheap -> no grid charge either

    def test_stale_pv_sensor_disables_surplus_check(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_power_entity="sensor.pv")
        fake_ws.stale["sensor.pv"] = True
        d = ctrl._decide(pv_w=1000, load_w=0, bat_soc=50.0, price=0.5, bat_kwh_free=1.0, now=now)
        assert d.mode is not EMSMode.PV_CHARGING

    def test_export_hold_active_gives_export_surplus_mode(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, pv_surplus_threshold_w=200,
            pv_export_priority_enabled=True, pv_charge_backstop_hour=23,
            pv_export_min_soc_pct=30, solcast_remaining_today_entity="sensor.remaining",
        )
        fake_ws.values["sensor.remaining"] = 100.0   # huge remaining forecast -> hold
        d = ctrl._decide(pv_w=1000, load_w=200, bat_soc=50.0, price=0.5, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.EXPORT_SURPLUS


class TestShouldHoldPvCharge:
    def test_disabled_never_holds(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=False)
        hold, reason = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        assert hold is False
        assert reason == "export priority off"

    def test_backstop_hour_forces_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=True,
                                pv_charge_backstop_hour=10)
        hold, reason = ctrl._should_hold_pv_charge(50.0, 5.0, now)   # now.hour == 12
        assert hold is False
        assert reason == "time backstop"

    def test_low_soc_forces_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=True,
                                pv_charge_backstop_hour=23, pv_export_min_soc_pct=30)
        hold, reason = ctrl._should_hold_pv_charge(20.0, 5.0, now)
        assert hold is False
        assert reason == "soc below export floor"

    def test_no_free_capacity_forces_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=True,
                                pv_charge_backstop_hour=23, pv_export_min_soc_pct=30)
        hold, reason = ctrl._should_hold_pv_charge(50.0, 0.01, now)
        assert hold is False
        assert reason == "no free capacity"

    def test_missing_forecast_forces_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, pv_export_priority_enabled=True,
                                pv_charge_backstop_hour=23, pv_export_min_soc_pct=30,
                                solcast_remaining_today_entity="")
        hold, reason = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        assert hold is False
        assert reason == "forecast unavailable"

    def test_large_remaining_forecast_holds(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, pv_export_priority_enabled=True, pv_charge_backstop_hour=23,
            pv_export_min_soc_pct=30, solcast_remaining_today_entity="sensor.remaining",
            pv_charge_margin_factor=1.2, pv_charge_hysteresis_frac=0.10,
        )
        fake_ws.values["sensor.remaining"] = 100.0
        hold, reason = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        assert hold is True
        assert reason == "forecast above battery+load need"

    def test_small_remaining_forecast_releases_hold(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, pv_export_priority_enabled=True, pv_charge_backstop_hour=23,
            pv_export_min_soc_pct=30, solcast_remaining_today_entity="sensor.remaining",
        )
        fake_ws.values["sensor.remaining"] = 0.01
        hold, reason = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        assert hold is False
        assert reason == "forecast below battery+load need"

    def test_hysteresis_makes_entering_hold_harder_than_leaving(self, make_config, fake_ws, now):
        """Same remaining forecast: holds when already holding, doesn't enter fresh."""
        ctrl = make_controller(
            make_config, fake_ws, pv_export_priority_enabled=True, pv_charge_backstop_hour=23,
            pv_export_min_soc_pct=30, solcast_remaining_today_entity="sensor.remaining",
            pv_charge_margin_factor=1.0, pv_charge_hysteresis_frac=0.10,
        )
        # need = bat_kwh_free(5) + remaining_load(0) = 5; target = 5 * 1.0 = 5
        # remaining forecast right between the two thresholds (4.5 / 5.5)
        fake_ws.values["sensor.remaining"] = 5.2
        ctrl._mode = EMSMode.PV_CHARGING   # not currently holding
        hold_not_holding, _ = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        ctrl._mode = EMSMode.EXPORT_SURPLUS   # currently holding
        hold_holding, _ = ctrl._should_hold_pv_charge(50.0, 5.0, now)
        assert hold_not_holding is False   # 5.2 < 5.5 (enter threshold) -> does not enter
        assert hold_holding is True        # 5.2 > 4.5 (exit threshold) -> keeps holding


class TestShouldGridCharge:
    def test_missing_price_never_charges(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws)
        assert ctrl._should_grid_charge(None, 5.0, now) is False

    def test_stale_price_never_charges(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, electricity_price_entity="sensor.price")
        fake_ws.stale["sensor.price"] = True
        assert ctrl._should_grid_charge(0.05, 5.0, now) is False

    def test_expensive_price_never_charges(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, electricity_price_entity="sensor.price",
                                cheap_rate_threshold_eur=0.10)
        fake_ws.values["sensor.price"] = 0.05
        assert ctrl._should_grid_charge(0.50, 5.0, now) is False

    def test_no_free_capacity_never_charges(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, electricity_price_entity="sensor.price",
                                cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0)
        fake_ws.values["sensor.price"] = 0.05
        assert ctrl._should_grid_charge(0.05, 0.5, now) is False

    def test_sufficient_remaining_forecast_blocks_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            solcast_remaining_today_entity="sensor.remaining", pv_charge_margin_factor=1.0,
        )
        fake_ws.values["sensor.price"] = 0.05
        fake_ws.values["sensor.remaining"] = 100.0
        assert ctrl._should_grid_charge(0.05, 5.0, now) is False

    def test_remaining_forecast_exhausted_falls_through_to_dark_window(self, make_config, fake_ws):
        """remaining <= grid_charge_min_free_kwh means "today's PV is spent" –
        that alone doesn't trigger a charge, it falls through to the same
        tomorrow-forecast/dark-window fallback as no forecast at all."""
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            solcast_remaining_today_entity="sensor.remaining", pv_charge_margin_factor=1.0,
            grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.05
        fake_ws.values["sensor.remaining"] = 0.1
        night = datetime(2026, 8, 27, 23, 0, tzinfo=timezone.utc).astimezone()
        noon = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc).astimezone()
        assert ctrl._should_grid_charge(0.05, 5.0, night) is True
        assert ctrl._should_grid_charge(0.05, 5.0, noon) is False

    def test_tomorrow_forecast_sufficient_skips_dark_window_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            pv_charge_margin_factor=1.0, solcast_tomorrow_entity="sensor.tomorrow",
            grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.05
        fake_ws.values["sensor.tomorrow"] = 100.0
        fake_ws.stale_daily["sensor.tomorrow"] = False
        night = datetime(2026, 8, 27, 23, 0, tzinfo=timezone.utc).astimezone()
        assert ctrl._should_grid_charge(0.05, 5.0, night) is False

    def test_dark_window_charges_without_any_forecast(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.05
        night = datetime(2026, 8, 27, 23, 0, tzinfo=timezone.utc).astimezone()
        assert ctrl._should_grid_charge(0.05, 5.0, night) is True

    def test_outside_dark_window_no_charge_without_forecast(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.05
        noon = datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc).astimezone()
        assert ctrl._should_grid_charge(0.05, 5.0, noon) is False

    def test_economic_gate_blocks_unprofitable_charge(self, make_config, fake_ws, now):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            grid_charge_min_margin_eur_kwh=0.02, grid_charge_dark_start_hour=21,
            grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.30
        ctrl._discharge_tariff = 0.31   # 0.9*0.31 - 0.30 = -0.021 -> below margin
        assert ctrl._should_grid_charge(0.30, 5.0, now) is False

    def test_economic_gate_skipped_when_tariff_unknown(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, electricity_price_entity="sensor.price",
            cheap_rate_threshold_eur=0.50, grid_charge_min_free_kwh=1.0,
            grid_charge_dark_start_hour=21, grid_charge_dark_end_hour=6,
        )
        fake_ws.values["sensor.price"] = 0.05
        ctrl._discharge_tariff = None   # unknown -> gate skipped, not fail-closed
        night = datetime(2026, 8, 27, 23, 0, tzinfo=timezone.utc).astimezone()
        assert ctrl._should_grid_charge(0.05, 5.0, night) is True


class TestForecastRemainingKwh:
    def test_none_without_solcast_client(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, solcast_remaining_today_entity="sensor.r")
        ctrl._solcast = None
        assert ctrl._forecast_remaining_kwh() is None

    def test_none_without_configured_entity(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, solcast_remaining_today_entity="")
        fake_ws.values["sensor.r"] = 5.0   # would resolve if the entity were wired
        assert ctrl._forecast_remaining_kwh() is None

    def test_none_when_stale(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, solcast_remaining_today_entity="sensor.r",
                                forecast_max_age_sec=100)
        fake_ws.values["sensor.r"] = 5.0
        fake_ws.stale["sensor.r"] = True
        assert ctrl._forecast_remaining_kwh() is None

    def test_none_when_negative(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, solcast_remaining_today_entity="sensor.r")
        fake_ws.values["sensor.r"] = -1.0
        assert ctrl._forecast_remaining_kwh() is None

    def test_returns_value_when_fresh(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, solcast_remaining_today_entity="sensor.r")
        fake_ws.values["sensor.r"] = 3.5
        assert ctrl._forecast_remaining_kwh() == 3.5

    def test_none_when_solcast_data_itself_stale(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, solcast_remaining_today_entity="sensor.r",
            solcast_last_fetch_entity="sensor.last_fetch",
        )
        fake_ws.values["sensor.r"] = 3.5
        fake_ws.datetimes["sensor.last_fetch"] = datetime.now(timezone.utc) - timedelta(hours=100)
        assert ctrl._forecast_remaining_kwh() is None


class TestCommitDebounce:
    def test_same_mode_no_pending(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._mode = EMSMode.IDLE
        result = ctrl._commit(ModeDecision(EMSMode.IDLE, "no action"), now)
        assert result is EMSMode.IDLE
        assert ctrl._pending is None

    def test_urgent_applies_immediately(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, mode_dwell_sec=300)
        ctrl._mode = EMSMode.IDLE
        result = ctrl._commit(ModeDecision(EMSMode.PROTECT_BATTERY, "soc below min", urgent=True), now)
        assert result is EMSMode.PROTECT_BATTERY

    def test_zero_dwell_applies_immediately(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, mode_dwell_sec=0)
        ctrl._mode = EMSMode.IDLE
        result = ctrl._commit(ModeDecision(EMSMode.PV_CHARGING, "surplus"), now)
        assert result is EMSMode.PV_CHARGING

    def test_new_mode_waits_out_dwell(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, mode_dwell_sec=300)
        ctrl._mode = EMSMode.IDLE
        result = ctrl._commit(ModeDecision(EMSMode.PV_CHARGING, "surplus"), now)
        assert result is EMSMode.IDLE   # still waiting
        assert ctrl._pending == (EMSMode.PV_CHARGING, now)

    def test_mode_applied_after_dwell_elapses(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, mode_dwell_sec=300)
        ctrl._mode = EMSMode.IDLE
        ctrl._commit(ModeDecision(EMSMode.PV_CHARGING, "surplus"), now)
        later = now + timedelta(seconds=301)
        result = ctrl._commit(ModeDecision(EMSMode.PV_CHARGING, "surplus"), later)
        assert result is EMSMode.PV_CHARGING

    def test_flip_flopping_proposal_restarts_the_timer(self, make_config, fake_ws, now):
        ctrl = make_controller(make_config, fake_ws, mode_dwell_sec=300)
        ctrl._mode = EMSMode.IDLE
        ctrl._commit(ModeDecision(EMSMode.PV_CHARGING, "surplus"), now)
        mid = now + timedelta(seconds=200)
        ctrl._commit(ModeDecision(EMSMode.GRID_CHARGING, "cheap"), mid)
        assert ctrl._pending == (EMSMode.GRID_CHARGING, mid)
        # Even after the *original* dwell window, the new proposal hasn't waited long enough
        after_original_window = now + timedelta(seconds=301)
        result = ctrl._commit(ModeDecision(EMSMode.GRID_CHARGING, "cheap"), after_original_window)
        assert result is EMSMode.IDLE


class TestInverterWriteStatus:
    def test_ok_without_inverter(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        assert ctrl._inverter_write_status() == "ok"

    def test_ok_when_all_confirmed(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._inverter = _FakeInverter(longest_pending_sec=0, write_unconfirmed=0, write_errors=0)
        assert ctrl._inverter_write_status() == "ok"

    def test_warning_when_unconfirmed(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, inverter_write_stuck_threshold_sec=1800)
        ctrl._inverter = _FakeInverter(longest_pending_sec=10, write_unconfirmed=1, write_errors=0)
        assert ctrl._inverter_write_status() == "warning"

    def test_error_when_stuck_past_threshold(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, inverter_write_stuck_threshold_sec=1800)
        ctrl._inverter = _FakeInverter(longest_pending_sec=1801, write_unconfirmed=1, write_errors=0)
        assert ctrl._inverter_write_status() == "error"


class _FakeInverter:
    def __init__(self, longest_pending_sec, write_unconfirmed, write_errors):
        self.longest_pending_sec = longest_pending_sec
        self.write_unconfirmed = write_unconfirmed
        self.write_errors = write_errors
        self.stuck_channel_labels = []

    def pop_write_events(self):
        return []


class TestGridChargePays:
    def test_true_when_unknown_tariff_or_efficiency(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._discharge_tariff = None
        assert ctrl._grid_charge_pays(0.30) is True

    def test_true_when_margin_sufficient(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, grid_charge_min_margin_eur_kwh=0.02)
        ctrl._discharge_tariff = 0.40
        # eff=0.9 (FakeOptimizer default) -> 0.9*0.40 - 0.20 = 0.16 > 0.02
        assert ctrl._grid_charge_pays(0.20) is True

    def test_false_when_margin_insufficient(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, grid_charge_min_margin_eur_kwh=0.02)
        ctrl._discharge_tariff = 0.21
        # 0.9*0.21 - 0.20 = -0.011 < 0.02
        assert ctrl._grid_charge_pays(0.20) is False


class TestBuildSensorWarnings:
    def test_missing_entity_produces_config_warning(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_soc_entity="")
        warnings = ctrl._build_sensor_warnings()
        assert any("Battery SoC entity not set" in w for w in warnings)

    def test_unavailable_soc_produces_warning(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_soc_entity="sensor.soc")
        warnings = ctrl._build_sensor_warnings()
        assert any("Battery SoC" in w and "unavailable" in w for w in warnings)

    def test_stale_required_sensor_warns_with_age(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, pv_power_entity="sensor.pv",
                                battery_soc_entity="")
        fake_ws.values["sensor.pv"] = 100.0
        fake_ws.ages["sensor.pv"] = 999999.0
        warnings = ctrl._build_sensor_warnings()
        assert any("PV power" in w and "stale" in w for w in warnings)

    def test_peak_time_entity_uses_datetime_presence_not_value(self, make_config, fake_ws):
        """A timestamp-valued sensor must not be flagged unavailable via get_state_value."""
        ctrl = make_controller(
            make_config, fake_ws, battery_soc_entity="",
            solcast_peak_time_today_entity="sensor.peak_today",
        )
        # get_state_value would return None for an ISO string; the check must
        # use get_state_datetime instead, or this always false-alarms.
        fake_ws.datetimes["sensor.peak_today"] = datetime.now(timezone.utc)
        fake_ws.stale_daily["sensor.peak_today"] = False
        warnings = ctrl._build_sensor_warnings()
        assert not any("Solcast peak time today" in w and "unavailable" in w for w in warnings)

    def test_peak_time_entity_missing_datetime_warns_unavailable(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, battery_soc_entity="",
            solcast_peak_time_today_entity="sensor.peak_today",
        )
        warnings = ctrl._build_sensor_warnings()
        assert any("Solcast peak time today" in w and "unavailable" in w for w in warnings)

    def test_peak_time_entity_stale_daily_warns(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws, battery_soc_entity="",
            solcast_peak_time_today_entity="sensor.peak_today",
        )
        fake_ws.datetimes["sensor.peak_today"] = datetime.now(timezone.utc)
        fake_ws.stale_daily["sensor.peak_today"] = True
        warnings = ctrl._build_sensor_warnings()
        assert any("Solcast peak time today" in w and "no update today" in w for w in warnings)

    def test_inverter_write_errors_produce_warning(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, battery_soc_entity="")
        ctrl._inverter = _FakeInverter(longest_pending_sec=0, write_unconfirmed=0, write_errors=2)
        warnings = ctrl._build_sensor_warnings()
        assert any("failed write" in w for w in warnings)

    def test_no_warnings_when_everything_fresh(self, make_config, fake_ws):
        ctrl = make_controller(
            make_config, fake_ws,
            battery_soc_entity="sensor.soc", pv_power_entity="sensor.pv",
            battery_power_entity="sensor.batp", grid_power_entity="sensor.grid",
            load_power_entity="sensor.load", electricity_price_entity="sensor.price",
            solcast_remaining_today_entity="", solcast_today_entity="",
            solcast_tomorrow_entity="", solcast_peak_time_today_entity="",
            solcast_peak_time_tomorrow_entity="", solcast_last_fetch_entity="",
        )
        for e in ("sensor.soc", "sensor.pv", "sensor.batp", "sensor.grid", "sensor.load", "sensor.price"):
            fake_ws.values[e] = 1.0
            fake_ws.ages[e] = 1.0
            fake_ws.stale[e] = False
        assert ctrl._build_sensor_warnings() == []
