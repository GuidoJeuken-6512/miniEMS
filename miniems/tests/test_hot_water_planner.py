"""hot_water_planner.py: whole charges to the shut-off temperature, priced per kWh of heat."""
from datetime import datetime, timedelta

import pytest

from hot_water_model import HotWaterModel
from hot_water_planner import HWRequirement, HWSlot, plan_hot_water

T0 = datetime(2026, 10, 29, 20, 0)   # Thursday evening
KWH_PER_K = 200 * 1.163 / 1000        # 200 l boiler


def model(loss=0.0, heat_kw=4.0, cop=4.0):
    return HotWaterModel(cop_a=cop, cop_b=0.0, cop_ref_temp_c=8.5, cop_source="prior",
                         cop_hours=0.0, loss_coeff=loss, loss_source="prior", loss_segments=0,
                         heat_kw_th=heat_kw, heat_source="config")


def slots(n=24, rate=0.30, rates=None, pv=None, battery=None):
    out = []
    for i in range(n):
        a = T0 + timedelta(minutes=30 * i)
        r = rates.get(i, rate) if rates else rate
        b = (battery or {}).get(i, {})
        out.append(HWSlot(a, a + timedelta(minutes=30), r, (pv or {}).get(i), 8.0, **b))
    return out


def run(sl, boil=46.0, m=None, reqs=(), floor=45.0, shutoff=51.0, feed_in=0.08, tol=5.0):
    return plan_hot_water(T0, sl, boil, m or model(), floor_c=floor, shutoff_c=shutoff,
                          volume_l=200.0, feed_in_eur_kwh=feed_in, requirements=list(reqs),
                          tolerance_pct=tol)


def morning():
    return T0 + timedelta(hours=10, minutes=30)   # 06:30


def test_nothing_needed_when_above_floor_without_loss():
    plan = run(slots())
    assert plan.feasible
    assert plan.windows == []
    assert not plan.charge_now
    assert plan.decision.startswith("wait")


def test_whole_charge_runs_to_shutoff():
    plan = run(slots(rates={6: 0.15, 7: 0.15}), reqs=[HWRequirement(morning(), 50.0, "morning")])
    assert len(plan.windows) == 1
    w = plan.windows[0]
    assert w.goal_c == pytest.approx(51.0)
    assert w.heat_kwh_th == pytest.approx(5.0 * KWH_PER_K, rel=0.01)   # 46 → 51 °C
    assert w.cost_th_eur_kwh == pytest.approx(0.15 / 4.0)               # cheap slot ÷ COP


def test_waits_for_cheap_window_when_it_is_reached_in_time():
    # loss (~0.33 K/h) brings 48 °C to the floor around 05:00 – after the cheap window at 02:00
    plan = run(slots(rates={12: 0.10, 13: 0.10}), boil=48.0, m=model(loss=0.012))
    # inside the cheap window the later slot wins (less standby loss until needed)
    assert plan.windows and plan.windows[0].start == T0 + timedelta(hours=6, minutes=30)
    assert plan.windows[0].cost_th_eur_kwh == pytest.approx(0.10 / 4.0)
    assert not plan.charge_now
    assert plan.decision.startswith("wait – enough until 02:30")
    assert plan.now_cost_th_eur_kwh == pytest.approx(0.30 / 4.0)


def test_charges_now_when_the_cheap_window_comes_too_late():
    # 45.5 °C with loss: the floor is hit long before the cheap window at 08:00
    plan = run(slots(rates={i: 0.10 for i in range(20, 24)}), boil=45.5, m=model(loss=0.02))
    assert plan.charge_now
    assert plan.windows[0].start == T0
    assert plan.decision.startswith("charge now until 51 °C")


def test_tolerance_prefers_now_when_later_is_only_slightly_cheaper():
    sl = slots(rates={6: 0.29})                       # only ~3 % cheaper later
    reqs = [HWRequirement(T0 + timedelta(hours=3, minutes=30), 50.0, "morning")]
    assert run(sl, reqs=reqs, tol=5.0).charge_now
    assert not run(sl, reqs=reqs, tol=1.0).charge_now


def test_morning_requirement_rules_out_too_early_charges():
    # a charge at 21:00 would lose too much by 06:30 – the plan charges late
    plan = run(slots(rates={2: 0.10}), reqs=[HWRequirement(morning(), 50.0, "morning")],
               m=model(loss=0.01))
    assert plan.feasible
    assert plan.windows[-1].start >= T0 + timedelta(hours=8)
    assert plan.temp_at(morning()) >= 50.0 - 1e-6


def test_pv_slot_beats_grid_slot():
    plan = run(slots(rate=0.30, pv={20: 5.0}), reqs=[HWRequirement(morning(), 50.0, "morning")])
    assert [w.source for w in plan.windows] == ["pv"]
    assert plan.windows[0].rate_eur_kwh == pytest.approx(0.08)
    assert plan.pv_elec_by_end


def test_pv_into_a_not_full_battery_costs_the_refill_price():
    bat = {20: dict(battery_full=False, battery_has_energy=True, refill_eur_kwh=0.20)}
    plan = run(slots(rate=0.30, pv={20: 5.0}, battery=bat),
               reqs=[HWRequirement(morning(), 50.0, "morning")])
    assert plan.windows[0].rate_eur_kwh == pytest.approx(0.20)   # max(feed-in, refill)


def test_battery_makes_evening_heat_cheaper_than_grid():
    bat = {i: dict(battery_full=False, battery_has_energy=True, refill_eur_kwh=0.12)
           for i in range(24)}
    plan = run(slots(rate=0.30, battery=bat), boil=45.5, m=model(loss=0.02))
    assert plan.windows[0].source == "battery"
    assert plan.windows[0].cost_th_eur_kwh == pytest.approx(0.12 / 4.0)


def test_blocked_battery_falls_back_to_grid():
    bat = {i: dict(battery_full=False, battery_has_energy=True, battery_blocked=True,
                   refill_eur_kwh=0.12) for i in range(24)}
    plan = run(slots(rate=0.30, battery=bat), boil=45.5, m=model(loss=0.02))
    assert plan.windows[0].source == "grid"
    assert plan.windows[0].rate_eur_kwh == pytest.approx(0.30)


def test_infeasible_without_prices():
    plan = run(slots(rate=None), boil=40.0)
    assert not plan.feasible


def test_planned_electricity_matches_heat_over_cop():
    plan = run(slots(), reqs=[HWRequirement(morning(), 50.0, "morning")])
    heat = sum(w.heat_kwh_th for w in plan.windows)
    assert sum(plan.planned_elec_by_end.values()) == pytest.approx(heat / 4.0)


def test_empty_slots():
    plan = run([])
    assert not plan.feasible
    assert plan.as_dict()["windows"] == []
