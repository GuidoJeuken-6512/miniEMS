"""Minimum charge, compressor-aware switching and the "charge now" bonus (v3.7.0).

Regression cases from 2026-11-14 (Saturday), where miniEMS started four hot-water
charges, two of them tiny top-ups: 12:40 49.3 → 50 °C while the compressor was
heating, 15:20 49.4 → 50 °C from standby (an extra compressor start).
"""
from datetime import datetime, timedelta

from hot_water_model import HotWaterModel
from hot_water_planner import HWRequirement, HWSlot, plan_hot_water

DAY = datetime(2026, 11, 14)
LOW, MEDIUM, HIGH = 0.2744, 0.3444, 0.40     # low < cheap threshold 0.30
CHEAP = 0.30


def model(loss=0.0, draws=None, heat_kw=6.0):
    return HotWaterModel(cop_a=3.0, cop_b=0.1, cop_ref_temp_c=7.0, cop_source="prior",
                         cop_hours=0.0, loss_coeff=loss, loss_source="prior", loss_segments=0,
                         heat_kw_th=heat_kw, heat_source="config",
                         draw_profile=dict(draws or {}))


def tariff(t: datetime) -> float:
    """Fixed time-of-use windows: low 04:30–05:00 and 15:30–16:00, evening high."""
    hm = (t.hour, t.minute)
    if hm in ((4, 30), (15, 30)):
        return LOW
    if 17 <= t.hour < 21:
        return HIGH
    return MEDIUM


def slots(now: datetime, hours=24, pv_until=None):
    """pv_until: until then the slot costs LOW – like 2026-11-14 midday, when PV
    (valued at the battery refill price) was as cheap as the low window."""
    out = []
    start = now
    end = now.replace(minute=0, second=0) + timedelta(minutes=30 if now.minute < 30 else 60)
    while start < now + timedelta(hours=hours):
        grid = start.replace(minute=0 if start.minute < 30 else 30, second=0)
        rate = LOW if pv_until is not None and start < pv_until else tariff(grid)
        out.append(HWSlot(start, end, rate, None, 7.0))
        start, end = end, end + timedelta(minutes=30)
    return out


def plan(now, boil, m, *, running=None, new=True, reqs=(), tol=5.0, pv_until=None):
    extra = dict(min_charge_k=3.0, min_charge_running_k=1.5, compressor_running=running,
                 cheap_rate_eur=CHEAP) if new else {}
    return plan_hot_water(now, slots(now, pv_until=pv_until), boil, m, floor_c=45.0,
                          shutoff_c=50.0,
                          volume_l=300.0, feed_in_eur_kwh=0.079, requirements=list(reqs),
                          tolerance_pct=tol, **extra)


# 49.3 °C falls to the 45 °C floor in ~10 h
LOSS = 4.3 / 10 / (47 - 20)


def test_old_rules_reproduce_the_1240_top_up():
    """Without the new rules the planner tops up 49.3 → 50 °C at once."""
    now = DAY.replace(hour=12, minute=40)
    p = plan(now, 49.3, model(LOSS), new=False, pv_until=DAY.replace(hour=15))
    assert p.charge_now
    assert p.windows[0].goal_c - p.windows[0].from_c < 1.0


def test_no_top_up_at_1240_while_compressor_heats():
    """49.3 °C is less than 1.5 K below shut-off: no start, even with the compressor on."""
    now = DAY.replace(hour=12, minute=40)
    p = plan(now, 49.3, model(LOSS), running=True, pv_until=DAY.replace(hour=15))
    assert not p.charge_now
    assert p.now_start_max_c == 48.5
    assert all(w.goal_c - w.from_c >= 3.0 - 1e-6 for w in p.windows)


def test_no_extra_compressor_start_at_1520():
    now = DAY.replace(hour=15, minute=20)
    p = plan(now, 49.4, model(LOSS), running=False)
    assert not p.charge_now
    assert p.now_start_max_c == 47.0


def test_charges_now_at_the_floor():
    """09:55 case: 45.0 °C, the floor would be missed in the next slot – charge now."""
    now = DAY.replace(hour=9, minute=55)
    p = plan(now, 45.0, model(LOSS), running=True)
    assert p.charge_now
    assert p.windows[0].goal_c == 50.0


def test_night_charge_lands_just_before_the_requirement():
    """02:25 case: no "now" bonus without a real shortfall – the charge moves later."""
    now = DAY.replace(hour=2, minute=25)
    morning = DAY.replace(hour=6, minute=30)
    p = plan(now, 47.4, model(LOSS), running=True,
             reqs=[HWRequirement(morning, 49.0, "morning")])
    assert p.feasible
    assert not p.charge_now
    assert p.windows and now < p.windows[0].start < morning
    assert p.windows[0].from_c <= 47.0 + 1e-6


def test_switching_while_compressor_runs_is_used():
    """48 °C, a big draw at 18:00 would break the floor; only the running
    compressor's smaller minimum charge allows a top-up now."""
    now = DAY.replace(hour=13, minute=0)
    m = model(draws={(False, 18): 4.0})
    running = plan(now, 48.0, m, running=True)
    assert running.charge_now
    off = plan(now, 48.0, m, running=False)
    assert not off.charge_now                  # falls back to a charge just before the draw
    assert off.feasible and off.windows
    assert off.windows[0].start > now


def test_fallback_when_no_minimum_charge_can_meet_a_requirement():
    """48 °C without loss never gets 3 K below shut-off – the morning target 49 °C
    is still met by a smaller charge (fallback margin 0.5 K)."""
    now = DAY.replace(hour=2, minute=0)
    morning = DAY.replace(hour=6, minute=30)
    p = plan(now, 48.0, model(0.0), running=False,
             reqs=[HWRequirement(morning, 49.0, "morning")])
    assert p.feasible
    assert p.windows and p.windows[0].goal_c == 50.0


def test_now_bonus_only_without_a_cheap_window_that_still_helps():
    """Tolerance would pick "now" (equal price), but the low window at 15:30 still
    prevents the violation – so the planner waits for it."""
    now = DAY.replace(hour=13, minute=0)
    p = plan(now, 46.5, model(LOSS), running=False, tol=50.0)
    assert not p.charge_now
    assert any(w.start.hour == 15 and w.start.minute == 30 for w in p.windows)
