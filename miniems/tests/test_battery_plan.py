"""A6 – battery day plan (battery_plan.py) and its use in EMSController."""
import time
from datetime import datetime, timedelta, timezone

import pytest

from battery_plan import Action, BatteryPlan, PlanStep, plan_battery
from bridge_planner import PlanSlot
from const import EMSMode

from test_ems_controller import make_controller
from test_energy_value import octopus_rate

TZ = timezone(timedelta(hours=1))
L, S, H = 0.274414, 0.3444, 0.3944


def day_slots(start, n=72, load=lambda t: 0.4, pv=lambda t: 0.0, rate=octopus_rate):
    out, t = [], start
    for _ in range(n):
        e = t + timedelta(minutes=30)
        out.append(PlanSlot(t, e, rate(t), load(t), pv(t)))
        t = e
    return out


def _plan(slots, stored=2.0, **kw):
    args = dict(stored_kwh=stored, capacity_kwh=8.0, charge_kw=4.0, discharge_kw=5.0,
                eta_charge=0.95, eta_discharge=0.95, feed_in_eur_kwh=0.08)
    args.update(kw)
    return plan_battery(slots[0].start, slots, **args)


class TestPlan:
    def test_never_dearer_than_self_use(self):
        for start_hour in (0, 7, 13, 19):
            start = datetime(2026, 11, 16, start_hour, tzinfo=TZ)
            p = _plan(day_slots(start, load=lambda t: 1.0 if 18 <= t.hour < 21 else 0.4))
            assert p.cost_eur <= p.baseline_cost_eur + 1e-6

    def test_winter_day_charges_cheap_and_spends_in_hoch(self):
        start = datetime(2026, 11, 16, 13, tzinfo=TZ)
        p = _plan(day_slots(start, load=lambda t: 1.0 if 18 <= t.hour < 21 else 0.4))
        charged = [s for s in p.steps if s.grid_charge_kwh > 0]
        assert charged and all(s.rate_eur_kwh == pytest.approx(L) for s in charged)
        hoch = [s for s in p.steps if s.rate_eur_kwh == pytest.approx(H)]
        assert all(s.action in (Action.AUTO, Action.EXPORT) for s in hoch)
        assert p.savings_eur > 0

    def test_flat_tariff_never_charges(self):
        start = datetime(2026, 11, 16, 0, tzinfo=TZ)
        p = _plan(day_slots(start, rate=lambda t: 0.30))
        assert not any(s.grid_charge_kwh > 0 for s in p.steps)

    def test_feed_in_limit_keeps_room_for_the_peak(self):
        """Midday PV above the limit: export in the morning, absorb the peak."""
        start = datetime(2026, 6, 16, 6, tzinfo=TZ)

        def pv(t):
            return 3.0 if 11 <= t.hour < 14 else (1.0 if 7 <= t.hour < 18 else 0.0)

        flat = lambda t: 0.30  # noqa: E731
        free = _plan(day_slots(start, n=36, pv=pv, load=lambda t: 0.2, rate=flat), stored=1.0)
        limited = _plan(day_slots(start, n=36, pv=pv, load=lambda t: 0.2, rate=flat), stored=1.0,
                        feed_in_limit_kw=3.0)
        morning = [s for s in limited.steps if 7 <= s.start.hour < 11]
        assert any(s.action is Action.EXPORT for s in morning)
        assert limited.cost_eur <= limited.baseline_cost_eur + 1e-6
        assert free.cost_eur <= limited.cost_eur + 1e-6

    def test_missing_input(self):
        start = datetime(2026, 11, 16, 0, tzinfo=TZ)
        p = _plan(day_slots(start), charge_kw=0)
        assert not p.steps and p.reason

    def test_fast_enough(self):
        start = datetime(2026, 11, 16, 0, tzinfo=TZ)
        t0 = time.perf_counter()
        _plan(day_slots(start))
        assert time.perf_counter() - t0 < 1.0


def _step(action, now, kwh=0.0):
    return PlanStep(now - timedelta(minutes=10), now + timedelta(minutes=20), action, 4.0, kwh,
                    kwh, 0.0, L)


class TestController:
    def _ctrl(self, make_config, fake_ws, action, kwh=0.0, enabled=True):
        ctrl = make_controller(make_config, fake_ws, battery_plan_enabled=enabled)
        now = datetime(2026, 11, 16, 3, 0, tzinfo=TZ)
        ctrl._battery_plan = BatteryPlan(computed_at=now, style="careful",
                                         steps=[_step(action, now, kwh)])
        return ctrl, now

    def test_charge(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.CHARGE, kwh=2.0)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=L, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.GRID_CHARGING and d.reason.startswith("battery plan")
        assert ctrl._grid_charge_power_w(5.0, now) == pytest.approx(2.0 / (20 / 60) * 1000)

    def test_hold(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.HOLD)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=L, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.HOLD_FOR_PEAK

    def test_auto_blocks_legacy_grid_charge(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.AUTO)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=0.05, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.IDLE and d.reason == "battery plan: self-use"

    def test_shadow_does_not_decide(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.HOLD, enabled=False)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=S, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.IDLE and not d.reason.startswith("battery plan")

    def test_stale_plan_does_not_decide(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.HOLD)
        ctrl._battery_plan.computed_at = now - timedelta(hours=1)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=40.0, price=S, bat_kwh_free=5.0, now=now)
        assert d.mode is EMSMode.IDLE

    def test_safety_first(self, make_config, fake_ws):
        ctrl, now = self._ctrl(make_config, fake_ws, Action.CHARGE, kwh=2.0)
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=5.0, price=L, bat_kwh_free=8.0, now=now)
        assert d.mode is EMSMode.PROTECT_BATTERY

    @pytest.mark.asyncio
    async def test_update_builds_shadow_plan(self, make_config, fake_ws):
        from test_energy_value import FakeForecaster
        from test_price_curve import _octopus_timeslots
        ctrl = make_controller(make_config, fake_ws, battery_voltage_entity="sensor.v")
        fake_ws.values["sensor.v"] = 50.0
        ctrl._forecaster = FakeForecaster(kwh_per_hour=0.8)
        fake_ws.attributes[ctrl._cfg.electricity_price_entity] = _octopus_timeslots()

        class Solcast:
            def pv_slots(self, scenario, weight):
                start = datetime(2026, 11, 16, 0, tzinfo=TZ)
                return {start + timedelta(minutes=30 * i):
                        (start + timedelta(minutes=30 * (i + 1)), 0.0) for i in range(96)}

        ctrl._solcast = Solcast()
        ctrl._solcast_data_stale = lambda: False
        now = datetime(2026, 11, 16, 13, 5, tzinfo=TZ)
        await ctrl._update_battery_plan(now, L, None, bat_kwh_use=2.0, bat_kwh_free=6.0)
        assert ctrl._battery_plan is not None and ctrl._battery_plan.steps
        assert ctrl._battery_plan_dict()["status"] == "shadow"
