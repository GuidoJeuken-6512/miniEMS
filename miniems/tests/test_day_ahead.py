"""A9 – day-ahead prices: band instead of equality, capacity, slot mean, V1 run."""
from datetime import datetime, timedelta, timezone

import pytest

from bridge_planner import PlanSlot, plan_bridge
from energy_plan import compute_energy_plan
from price_curve import PriceCurve

from test_energy_value import octopus_rate
from test_price_curve import _AttrWS

TZ = timezone(timedelta(hours=1))
START = datetime(2026, 11, 16, 0, 0, tzinfo=TZ)


def quarter_curve(prices_ct):
    """15-min exchange prices in ct, from START."""
    rates = [{"start": (START + timedelta(minutes=15 * i)).isoformat(),
              "end": (START + timedelta(minutes=15 * (i + 1))).isoformat(), "value": v}
             for i, v in enumerate(prices_ct)]
    return PriceCurve.from_entity(_AttrWS({"rates": rates}), "sensor.p", "rates")


def slots(rate, n=48, start=START, load=0.5):
    out, t = [], start
    for _ in range(n):
        out.append(PlanSlot(t, t + timedelta(minutes=30), rate(t), load, 0.0))
        t += timedelta(minutes=30)
    return out


class TestCurve:
    def test_mean_rate_of_a_slot(self):
        curve = quarter_curve([20, 30, 40, 40])
        assert curve.mean_rate(START, START + timedelta(minutes=30)) == pytest.approx(0.25)

    def test_mean_rate_with_a_hole(self):
        curve = quarter_curve([20, 30])
        assert curve.mean_rate(START, START + timedelta(hours=1)) is None

    def test_run_end_spans_the_cheap_run(self):
        curve = quarter_curve([20, 20.5, 21, 20, 35, 36])
        end = curve.run_end(START, tolerance_eur=0.01, horizon=timedelta(hours=6))
        assert end == START + timedelta(hours=1)
        assert curve.window_end(START) == START + timedelta(minutes=15)

    def test_cheap_hours_between(self):
        curve = quarter_curve([20, 35, 20, 20, 35])
        assert curve.cheap_hours_between(START, START + timedelta(hours=2), 0.21) == pytest.approx(0.75)


class TestBridge:
    def _evening(self, t):
        """Cheap night until 06, expensive day with one cheap quarter of an hour at 10:00."""
        if t.hour < 6:
            return 0.20
        if t.hour == 10 and t.minute == 0:
            return 0.20
        return 0.35

    def test_single_cheap_slot_ends_bridge_without_capacity_check(self):
        p = plan_bridge(slots(self._evening), stored_kwh=1.0, free_kwh=8.0, eta=0.9,
                        min_margin_eur_kwh=0.02, charge_kw=3.0)
        assert p.bridge_until == START.replace(hour=10)

    def test_capacity_check_bridges_past_it(self):
        p = plan_bridge(slots(self._evening), stored_kwh=1.0, free_kwh=8.0, eta=0.9,
                        min_margin_eur_kwh=0.02, charge_kw=3.0, check_capacity=True)
        assert p.bridge_until is None or p.bridge_until > START.replace(hour=10, minute=30)
        assert p.need_kwh > plan_bridge(slots(self._evening), 1.0, 8.0, 0.9, 0.02, 3.0).need_kwh

    def test_band_merges_nearly_equal_rates(self):
        def rate(t):
            return 0.200 if t.hour < 3 else (0.205 if t.hour < 6 else 0.35)
        strict = plan_bridge(slots(rate), 1.0, 8.0, 0.9, 0.02, 3.0)
        band = plan_bridge(slots(rate), 1.0, 8.0, 0.9, 0.02, 3.0, tolerance_eur=0.01)
        assert strict.cheap_until == START.replace(hour=3)
        assert band.cheap_until == START.replace(hour=6)

    def test_octopus_identical_with_day_ahead_settings(self):
        for hour in (2, 12, 16, 22):
            s = slots(octopus_rate, n=72, start=START.replace(hour=hour))
            off = plan_bridge(s, 2.0, 6.0, 0.9, 0.02, 3.0)
            on = plan_bridge(s, 2.0, 6.0, 0.9, 0.02, 3.0, tolerance_eur=0.01, check_capacity=True)
            assert on.need_kwh == pytest.approx(off.need_kwh)
            assert (on.cheap_until, on.bridge_until) == (off.cheap_until, off.bridge_until)

    def test_horizon_end_is_visible(self):
        s = slots(lambda t: 0.20 if t.hour < 2 else 0.35, n=8)
        p = plan_bridge(s, 0.0, 8.0, 0.9, 0.02, 3.0)
        assert p.ends_at_price_horizon is True


class TestEnergyPlan:
    def test_baseline_mean_instead_of_deadline_price(self):
        # cheap night, very cheap midday at the deadline: the deadline price
        # would reject the night charge, the mean of the dear slots does not
        prices = [20] * 24 + [40] * 24 + [5] * 48
        curve = quarter_curve(prices)
        now, deadline = START, START + timedelta(hours=13)
        kw = dict(now=now, deadline=deadline, bat_kwh_free=4.0, predicted_pv_tomorrow_kwh=0.0,
                  margin_factor=1.2, charge_kw=3.0, curve=curve)
        assert compute_energy_plan(**kw).feasible is False
        assert compute_energy_plan(**kw, baseline_mean=True).feasible is True


class TestController:
    def test_tolerance_band_only_with_switch(self, make_config, fake_ws):
        from test_ems_controller import make_controller
        off = make_controller(make_config, fake_ws)
        assert off._rate_tolerance() == pytest.approx(0.0005)
        on = make_controller(make_config, fake_ws, day_ahead_enabled=True)
        assert on._rate_tolerance() == pytest.approx(0.01)
