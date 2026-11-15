"""B2 – energy tasks, distributor, coupling rule."""
from datetime import datetime, timedelta, timezone

import pytest

from const import EMSMode
from ems_controller import ModeDecision
from energy_task import (
    EnergyTask, SurplusState, TaskSlot, distribute_surplus, plan_tasks, surplus_order,
)

T0 = datetime(2026, 11, 16, 18, 0, tzinfo=timezone(timedelta(hours=1)))


def slots(prices, limit_w=None, base_w=0.0, pv_w=None):
    out = []
    for i, p in enumerate(prices):
        s = T0 + timedelta(minutes=30 * i)
        out.append(TaskSlot(s, s + timedelta(minutes=30), p, base_load_w=base_w,
                            pv_surplus_w=(pv_w[i] if pv_w else 0.0), feed_in_eur_kwh=0.08,
                            limit_w=limit_w))
    return out


class TestPlanTasks:
    def test_takes_the_cheapest_slots_before_the_deadline(self):
        sl = slots([0.30, 0.20, 0.10, 0.12, 0.05])
        task = EnergyTask("ev", 5.5, 1400, 11000, deadline=sl[3].end)
        plan = plan_tasks([task], sl)["ev"]
        assert plan.feasible
        assert plan.power_w[4] == 0.0                # after the deadline
        assert plan.power_w[2] == pytest.approx(11000)
        assert plan.power_w[0] == 0.0
        assert plan.energy_kwh == pytest.approx(5.5)

    def test_tightest_deadline_first(self):
        sl = slots([0.10, 0.10, 0.30, 0.30], limit_w=11000)
        early = EnergyTask("early", 5.0, 1000, 11000, deadline=sl[1].end)
        late = EnergyTask("late", 5.0, 1000, 11000, deadline=sl[3].end)
        plans = plan_tasks([late, early], sl)
        assert plans["early"].feasible
        assert sum(plans["early"].power_w[:2]) > 0
        # The cheap slots are taken by the earlier task; the later one fills the rest.
        assert plans["late"].energy_kwh == pytest.approx(5.0, rel=0.01)

    def test_fuse_limit_minus_base_load(self):
        sl = slots([0.10] * 4, limit_w=8000, base_w=3000)
        task = EnergyTask("ev", 20.0, 1400, 11000, deadline=sl[-1].end)
        plan = plan_tasks([task], sl)["ev"]
        assert max(plan.power_w) <= 5000 + 1e-6
        assert not plan.feasible
        assert "do not fit" in plan.reason

    def test_must_task_drops_the_reserve(self):
        sl = slots([0.10] * 2, limit_w=8000)
        task = EnergyTask("ev", 8.0, 1400, 11000, deadline=sl[-1].end, kind="must")
        with_reserve_only = plan_tasks([EnergyTask("ev", 8.0, 1400, 11000, deadline=sl[-1].end)],
                                       sl, reserve_w=1500)["ev"]
        assert not with_reserve_only.feasible
        must = plan_tasks([task], sl, reserve_w=1500)["ev"]
        assert must.feasible
        assert max(must.power_w) <= 8000 + 1e-6

    def test_pv_surplus_makes_a_slot_cheaper(self):
        sl = slots([0.25, 0.25], pv_w=[0, 6000])
        plan = plan_tasks([EnergyTask("ev", 3.0, 1400, 6000, deadline=sl[-1].end)], sl)["ev"]
        assert plan.power_w[1] > 0 and plan.power_w[0] == 0.0
        assert plan.cost_eur == pytest.approx(3.0 * 0.08)

    def test_non_interruptible_block(self):
        sl = slots([0.30, 0.10, 0.12, 0.40, 0.05])
        task = EnergyTask("wm", 2.0, 2000, 2000, deadline=sl[3].end, interruptible=False,
                          min_run_slots=2)
        plan = plan_tasks([task], sl)["wm"]
        assert plan.feasible
        assert [p > 0 for p in plan.power_w] == [False, True, True, False, False]


class TestSurplus:
    def base(self, **kw):
        d = dict(surplus_w=5000, battery_soc=30, battery_priority_soc=50,
                 battery_max_charge_w=3000, battery_full=False, ev_min_w=1400,
                 ev_max_w=11000, ev_wants=True)
        d.update(kw)
        return SurplusState(**d)

    def test_battery_first_below_priority_soc(self):
        share = distribute_surplus(self.base())
        assert share.battery_w == 3000 and share.ev_w == 2000
        assert surplus_order(self.base())[0] == "battery_priority"

    def test_ev_first_above_priority_soc(self):
        share = distribute_surplus(self.base(battery_soc=70))
        assert share.ev_w == 5000 and share.battery_w == 0

    def test_ev_priority_switch(self):
        share = distribute_surplus(self.base(charge_priority="ev"))
        assert share.ev_w == 5000

    def test_too_little_for_the_ev_goes_to_the_battery(self):
        share = distribute_surplus(self.base(surplus_w=1000, battery_soc=70))
        assert share.ev_w == 0 and share.battery_w == 1000

    def test_rest_is_exported(self):
        share = distribute_surplus(self.base(surplus_w=20000, battery_soc=70))
        assert share.export_w == 20000 - 11000 - 3000


class FakeEV:
    def __init__(self, controlling=True, charging=True):
        self.controlling = controlling
        self.charging = charging
        self.configured = True


class TestCoupling:
    def _ctrl(self, make_config, fake_ws, ev, **kw):
        from test_ems_controller import make_controller
        ctrl = make_controller(make_config, fake_ws, **kw)
        ctrl._ev = ev
        return ctrl

    def test_ev_charging_blocks_discharge(self, make_config, fake_ws):
        ctrl = self._ctrl(make_config, fake_ws, FakeEV())
        d = ctrl._couple_ev(ModeDecision(EMSMode.IDLE, "no action"))
        assert d.mode is EMSMode.HOLD_FOR_PEAK
        assert "ev charging" in d.reason

    @pytest.mark.parametrize("mode", [EMSMode.GRID_CHARGING, EMSMode.PROTECT_BATTERY])
    def test_modes_that_already_block_stay(self, make_config, fake_ws, mode):
        ctrl = self._ctrl(make_config, fake_ws, FakeEV())
        assert ctrl._couple_ev(ModeDecision(mode, "x")).mode is mode

    def test_regression_without_ev_control(self, make_config, fake_ws):
        """Only the battery as a task: decisions identical to before."""
        for ev in (None, FakeEV(controlling=False), FakeEV(charging=False)):
            ctrl = self._ctrl(make_config, fake_ws, ev)
            d = ModeDecision(EMSMode.IDLE, "no action")
            assert ctrl._couple_ev(d) is d

    def test_allow_mode_leaves_the_battery_alone(self, make_config, fake_ws):
        ctrl = self._ctrl(make_config, fake_ws, FakeEV(), ev_battery_mode="allow")
        d = ModeDecision(EMSMode.IDLE, "no action")
        assert ctrl._couple_ev(d) is d
