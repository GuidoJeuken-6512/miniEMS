"""EMSController × Überbrückungsplanung (v3.3.0)."""
from datetime import datetime, timedelta, timezone

import pytest

from const import EMSMode
from ems_controller import EMSController
from load_forecast import LoadModel
from solcast_client import SolcastClient
from test_ems_controller import FakeOptimizer

TZ = timezone(timedelta(hours=1))
PRICE = "sensor.price"
TODAY = "sensor.solcast_today"
TOMORROW = "sensor.solcast_tomorrow"


class FakeForecaster:
    def __init__(self, load_kw_h: float = 1.0, days: int = 20, min_days: int = 14) -> None:
        self.model = LoadModel([load_kw_h] * 24, 0.0, 0.0, 15.0, False, days, days * 24, 0.0)
        self._min_days = min_days
        self.refreshed = 0

    @property
    def ready(self) -> bool:
        return self.model.days >= self._min_days

    async def refresh(self, now):
        self.refreshed += 1

    def predict_kwh(self, start, minutes, temp_c):
        return self.model.predict_hour_kwh(start.hour, temp_c) * minutes / 60.0


def _timeslots():
    return {"timeslots": [
        {"name": "NIEDRIG", "rate": "27.44", "activation_rules": [
            {"from_time": "02:00:00", "to_time": "06:00:00"},
            {"from_time": "12:00:00", "to_time": "16:00:00"}]},
        {"name": "STANDARD", "rate": "34.44", "activation_rules": [
            {"from_time": "06:00:00", "to_time": "12:00:00"},
            {"from_time": "16:00:00", "to_time": "02:00:00"}]},
    ]}


def _solcast(day: datetime, pv_kw=lambda h: 0.0):
    entries = []
    for i in range(48):
        s = day + timedelta(minutes=30 * i)
        entries.append({"period_start": s.isoformat(), "pv_estimate": pv_kw(s.hour),
                        "pv_estimate10": pv_kw(s.hour)})
    return entries


def make(make_config, fake_ws, *, enabled=True, forecaster=None, pv_kw=lambda h: 0.0,
         day=datetime(2026, 12, 10, tzinfo=TZ)):
    cfg = make_config(
        load_forecast_enabled=enabled, electricity_price_entity=PRICE,
        solcast_today_entity=TODAY, solcast_tomorrow_entity=TOMORROW,
        solcast_last_fetch_entity="", battery_voltage_entity="",
        pv_forecast_scenario="estimate", grid_charge_min_margin_eur_kwh=0.02,
    )
    fake_ws.attributes[PRICE] = _timeslots()
    fake_ws.attributes[TODAY] = {"detailedForecast": _solcast(day, pv_kw)}
    fake_ws.attributes[TOMORROW] = {"detailedForecast": _solcast(day + timedelta(days=1), pv_kw)}
    fake_ws.stale[cfg.battery_power_entity] = False
    ctrl = EMSController(cfg, fake_ws, FakeOptimizer(),
                         load_forecaster=forecaster or FakeForecaster())
    ctrl._solcast = SolcastClient(cfg, fake_ws)
    ctrl._learned_charge_kw = 5.0
    return ctrl


async def _plan(ctrl, now, soc=30.0, price=0.2744, cap=20.0):
    ctrl._battery_model.capacity_kwh = cap
    free = ctrl._battery_model.free_to_charge_kwh(soc)
    use = ctrl._battery_model.useable_kwh(soc)
    await ctrl._update_bridge_plan(None, price, free, use, now)
    return free


class TestPlanBuild:
    @pytest.mark.asyncio
    async def test_disabled_builds_nothing(self, make_config, fake_ws):
        fc = FakeForecaster()
        ctrl = make(make_config, fake_ws, enabled=False, forecaster=fc)
        await _plan(ctrl, datetime(2026, 12, 10, 2, 0, tzinfo=TZ))
        assert ctrl._bridge_plan is None and fc.refreshed == 0
        assert ctrl._bridge_status == "disabled"

    @pytest.mark.asyncio
    async def test_night_plan_covers_morning(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        # SoC 30 % of 20 kWh, min 15 % → 3 kWh usable; 06–12 needs 6 kWh
        await _plan(ctrl, datetime(2026, 12, 10, 2, 0, tzinfo=TZ))
        plan = ctrl._bridge_plan
        assert ctrl._bridge_status == "active"
        assert plan.need_kwh == pytest.approx(3.0, abs=0.1)
        assert plan.cheap_until == datetime(2026, 12, 10, 6, 0, tzinfo=TZ)

    @pytest.mark.asyncio
    async def test_missing_detailed_forecast_falls_back(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        fake_ws.attributes[TODAY] = {}
        fake_ws.attributes[TOMORROW] = {}
        await _plan(ctrl, datetime(2026, 12, 10, 2, 0, tzinfo=TZ))
        assert ctrl._bridge_plan is None
        assert ctrl._bridge_status == "solcast detailedForecast missing"

    @pytest.mark.asyncio
    async def test_learning_status_not_in_control(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws, forecaster=FakeForecaster(days=5))
        await _plan(ctrl, datetime(2026, 12, 10, 2, 0, tzinfo=TZ))
        assert ctrl._bridge_status == "learning (5/14 days)"
        assert not ctrl._bridge_in_control()

    @pytest.mark.asyncio
    async def test_nowcast_scales_near_slots(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws, pv_kw=lambda h: 2.0 if 8 <= h < 14 else 0.0)
        now = datetime(2026, 12, 10, 12, 0, tzinfo=TZ)
        ctrl._battery_model.capacity_kwh = 20.0
        await ctrl._update_bridge_plan(1000.0, 0.2744, 10.0, 3.0, now)  # measured 1 kW vs 2 kW
        first = ctrl._bridge_plan.slots[0]
        assert first.pv_kwh == pytest.approx(0.5, abs=0.01)


class TestDecision:
    @pytest.mark.asyncio
    async def test_grid_charges_when_plan_needs(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now)
        d = ctrl._decide(None, None, 30.0, 0.2744, free, now)
        assert d.mode is EMSMode.GRID_CHARGING
        assert d.reason.startswith("bridge plan: 3.0 kWh by 06:00")

    @pytest.mark.asyncio
    async def test_no_charge_when_battery_suffices(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now, soc=60.0)
        assert ctrl._decide(None, None, 60.0, 0.2744, free, now).mode is EMSMode.IDLE

    @pytest.mark.asyncio
    async def test_plan_ignores_absolute_cheap_threshold(self, make_config, fake_ws):
        # FakeOptimizer.is_cheap_rate is "< 0.10"; 27 ct would never pass it.
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now)
        assert ctrl._should_grid_charge(0.2744, free, now) is True

    @pytest.mark.asyncio
    async def test_running_charge_continues_until_small_rest(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now, soc=42.0)        # need ≈ 0.6 kWh < 1.0 start
        assert ctrl._should_grid_charge(0.2744, free, now) is False
        ctrl._mode = EMSMode.GRID_CHARGING
        assert ctrl._should_grid_charge(0.2744, free, now) is True

    @pytest.mark.asyncio
    async def test_small_pv_surplus_does_not_block_needed_charge(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 12, 0, tzinfo=TZ)
        free = await _plan(ctrl, now)
        d = ctrl._decide(1500.0, 1000.0, 30.0, 0.2744, free, now)   # 500 W surplus
        assert d.mode is EMSMode.GRID_CHARGING

    @pytest.mark.asyncio
    async def test_stale_price_still_fails_closed(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now)
        fake_ws.stale[PRICE] = True
        assert ctrl._should_grid_charge(0.2744, free, now) is False

    @pytest.mark.asyncio
    async def test_power_stretched_over_cheap_window(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        now = datetime(2026, 12, 10, 2, 0, tzinfo=TZ)
        free = await _plan(ctrl, now)
        w = ctrl._grid_charge_power_w(free, now)
        # 3 kWh over 4 h × 0.8 → ~937 W
        assert w == pytest.approx(3.0 / 3.2 * 1000, rel=0.05)

    @pytest.mark.asyncio
    async def test_status_dict(self, make_config, fake_ws):
        ctrl = make(make_config, fake_ws)
        await _plan(ctrl, datetime(2026, 12, 10, 2, 0, tzinfo=TZ))
        d = ctrl._bridge_plan_dict()
        assert d["in_control"] is True and d["plan"]["need_kwh"] > 0
        assert d["model"]["days"] == 20


class TestRecording:
    @pytest.mark.asyncio
    async def test_heat_pump_phases_summed(self, make_config, fake_ws):
        calls = []

        class Rec:
            async def record_tick(self, now, load_w, hp_w, temp):
                calls.append((load_w, hp_w, temp))

        cfg = make_config(heat_pump_power_entity="sensor.l1, sensor.l2,sensor.l3",
                          outdoor_temp_entity="sensor.out")
        fake_ws.values.update({"sensor.l1": 100.0, "sensor.l2": 200.0, "sensor.l3": 300.0,
                               "sensor.out": -3.0})
        ctrl = EMSController(cfg, fake_ws, FakeOptimizer(), hourly_recorder=Rec())
        await ctrl._record_hourly(datetime(2026, 12, 10, 2, tzinfo=TZ), 1500.0)
        assert calls == [(1500.0, 600.0, -3.0)]

    @pytest.mark.asyncio
    async def test_missing_phase_gives_none(self, make_config, fake_ws):
        calls = []

        class Rec:
            async def record_tick(self, now, load_w, hp_w, temp):
                calls.append(hp_w)

        cfg = make_config(heat_pump_power_entity="sensor.l1,sensor.l2")
        fake_ws.values["sensor.l1"] = 100.0
        ctrl = EMSController(cfg, fake_ws, FakeOptimizer(), hourly_recorder=Rec())
        await ctrl._record_hourly(datetime(2026, 12, 10, 2, tzinfo=TZ), 1500.0)
        assert calls == [None]
