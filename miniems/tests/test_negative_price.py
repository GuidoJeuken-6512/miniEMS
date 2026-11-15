"""A5 – negative prices, unremunerated feed-in, feed-in limit, export limit."""
from datetime import datetime, timedelta, timezone

import pytest

from const import EMSMode, HWMode

from test_ems_controller import make_controller

TZ = timezone(timedelta(hours=1))
NOON = datetime(2026, 6, 16, 12, 0, tzinfo=TZ)


def _ctrl(make_config, fake_ws, **kw):
    base = dict(battery_control_enabled=True, pv_export_priority_enabled=True,
                pv_charge_backstop_hour=17, pv_export_min_soc_pct=30)
    base.update(kw)
    ctrl = make_controller(make_config, fake_ws, **base)
    cfg = ctrl._cfg
    fake_ws.values[cfg.solcast_remaining_today_entity] = 30.0   # forecast says: hold
    ctrl._remaining_load_kwh = 2.0
    return ctrl


class TestPvHold:
    def test_hold_as_before_when_switch_off(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, solcast_peak_time_today_entity="")
        hold, reason = ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=-0.05)
        assert hold and reason == "forecast above battery+load need"

    def test_negative_price_releases_hold(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True,
                     solcast_peak_time_today_entity="")
        assert ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=-0.05) == (False, "negative price")

    def test_unpaid_feed_in_releases_hold(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True,
                     feed_in_price_entity="sensor.feed_in", solcast_peak_time_today_entity="")
        fake_ws.values["sensor.feed_in"] = 0.0
        assert ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=0.30) == (
            False, "feed-in not remunerated")
        fake_ws.values["sensor.feed_in"] = 0.08
        assert ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=0.30)[0] is True

    def test_feed_in_limit_releases_hold(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, feed_in_limit_w=4000, solcast_peak_time_today_entity="")
        assert ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=0.3, surplus_w=5000) == (
            False, "feed-in limit")
        assert ctrl._should_hold_pv_charge(60.0, 4.0, NOON, price=0.3, surplus_w=3000)[0] is True

    def test_decide_stores_pv_at_negative_price(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True,
                     solcast_peak_time_today_entity="")
        # 30 kWh PV left, 2 kWh load: the PV fills the battery → no grid charge
        d = ctrl._decide(pv_w=5000, load_w=500, bat_soc=60.0, price=-0.02, bat_kwh_free=4.0, now=NOON)
        assert d.mode is EMSMode.PV_CHARGING
        assert d.reason == "negative price"


class TestNegativeGridCharge:
    def test_charges_when_pv_cannot_fill(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True)
        fake_ws.values[ctrl._cfg.solcast_remaining_today_entity] = 1.0
        assert ctrl._negative_price_charge(-0.01, 6.0, NOON) is True
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=-0.01, bat_kwh_free=6.0, now=NOON)
        assert d.mode is EMSMode.GRID_CHARGING and d.reason == "negative price"

    def test_leaves_room_for_pv(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True)
        fake_ws.values[ctrl._cfg.solcast_remaining_today_entity] = 12.0
        assert ctrl._negative_price_charge(-0.01, 6.0, NOON) is False

    def test_off_positive_or_disabled(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True)
        fake_ws.values[ctrl._cfg.solcast_remaining_today_entity] = 0.0
        assert ctrl._negative_price_charge(0.01, 6.0, NOON) is False
        off = _ctrl(make_config, fake_ws, negative_price_enabled=False)
        assert off._negative_price_charge(-0.01, 6.0, NOON) is False

    def test_full_battery_unpaid_signal(self, make_config, fake_ws):
        ctrl = _ctrl(make_config, fake_ws, negative_price_enabled=True, battery_max_soc=95)
        assert ctrl._update_negative_price_actuators(-0.01, 95.0) is True
        assert ctrl._update_negative_price_actuators(-0.01, 80.0) is False
        assert ctrl._update_negative_price_actuators(0.20, 95.0) is False


class TestExportLimit:
    @pytest.mark.asyncio
    async def test_writes_zero_and_back(self, make_config, fake_ws):
        from inverter_controller import InverterController
        cfg = make_config(battery_control_enabled=True, battery_control_simulation=True,
                          grid_export_limit_entity="number.export", grid_export_limit_normal_w=8000)
        inv = InverterController(cfg, "", ws_client=fake_ws)
        await inv.apply_export_limit(True)
        assert inv._channels["export"].target == 0
        await inv.apply_export_limit(False)
        assert inv._channels["export"].target == 8000

    @pytest.mark.asyncio
    async def test_nothing_without_normal_value(self, make_config, fake_ws):
        from inverter_controller import InverterController
        cfg = make_config(battery_control_enabled=True, grid_export_limit_entity="number.export")
        inv = InverterController(cfg, "", ws_client=fake_ws)
        await inv.apply_export_limit(True)
        assert inv._channels["export"].target is None


class TestHotWater:
    def test_negative_price_heats_to_max(self, make_config, fake_ws):
        from hot_water_controller import HotWaterController
        cfg = make_config(boil_temp_entity="sensor.boil", hw_max_temp_c=60.0,
                          hot_water_min_temp_c=40.0)
        hw = HotWaterController(cfg, fake_ws, None, None)
        hw._boil_c = 48.0
        hw.set_negative_price(True)
        hw._decide(NOON, grid_w=200.0)
        assert hw._mode is HWMode.PV
        assert hw._charge.goal_c == 60.0
        # importing at a negative price must not stop the charge
        hw._check_stop(NOON + timedelta(minutes=5), 50.0, grid_w=500.0)
        assert hw._charge is not None
