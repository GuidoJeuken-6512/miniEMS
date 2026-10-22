"""bridge_planner.plan_bridge: how much to buy in the current cheap window."""
from datetime import datetime, timedelta, timezone

import pytest

from bridge_planner import PlanSlot, plan_bridge

TZ = timezone(timedelta(hours=1))
NIEDRIG, STANDARD, HOCH = 0.2744, 0.3444, 0.45


def _rate(h: float) -> float:
    if 2 <= h < 6 or 12 <= h < 16:
        return NIEDRIG
    if 17 <= h < 20:
        return HOCH
    return STANDARD


def make_slots(start_hour: int, hours: int, load_kw: float = 1.0, pv_kw=lambda h: 0.0,
               rate=_rate) -> list[PlanSlot]:
    """30-min slots from start_hour on 2026-12-10, constant load, PV by hour."""
    t0 = datetime(2026, 12, 10, start_hour, 0, tzinfo=TZ)
    slots = []
    for i in range(hours * 2):
        s = t0 + timedelta(minutes=30 * i)
        h = s.hour + s.minute / 60
        slots.append(PlanSlot(s, s + timedelta(minutes=30), rate(h),
                              load_kw * 0.5, pv_kw(h) * 0.5))
    return slots


class TestBasics:
    def test_no_slots(self):
        assert plan_bridge([], 5, 5, 0.9, 0.02, 3.0).need_kwh == 0.0

    def test_unknown_current_rate(self):
        slots = make_slots(2, 4, rate=lambda h: None)
        assert plan_bridge(slots, 5, 5, 0.9, 0.02, 3.0).reason == "no current rate"


class TestNightWindow:
    def test_night_charge_bridges_morning_standard(self):
        # 02:00, 1 kW load, no PV: 06–12 STANDARD needs 6 kWh. Battery has 2.
        slots = make_slots(2, 12)
        plan = plan_bridge(slots, stored_kwh=2.0, free_kwh=15.0, eta=0.92,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        assert plan.cheap_until == datetime(2026, 12, 10, 6, 0, tzinfo=TZ)
        assert plan.bridge_until == datetime(2026, 12, 10, 12, 0, tzinfo=TZ)
        assert plan.need_kwh == pytest.approx(4.0, abs=0.06)
        assert plan.shortfall_kwh == pytest.approx(4.0, abs=0.01)

    def test_full_enough_battery_needs_nothing(self):
        plan = plan_bridge(make_slots(2, 12), stored_kwh=7.0, free_kwh=8.0, eta=0.92,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        assert plan.need_kwh == 0.0
        assert plan.reason == "battery + PV cover the expensive period"

    def test_pv_in_bridge_reduces_need(self):
        sunny = make_slots(2, 12, pv_kw=lambda h: 2.0 if 9 <= h < 12 else 0.0)
        plan = plan_bridge(sunny, stored_kwh=2.0, free_kwh=15.0, eta=0.92,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        # 06–09: 3 kWh deficit, 09–12 surplus → only 3 − 2 = 1 kWh to buy
        assert plan.need_kwh == pytest.approx(1.0, abs=0.06)

    def test_spread_too_thin_does_not_charge(self):
        plan = plan_bridge(make_slots(2, 12), stored_kwh=0.0, free_kwh=15.0, eta=0.80,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        # 0.8 × 0.3444 − 0.2744 < 0.02
        assert plan.need_kwh == 0.0
        assert plan.reason == "expensive period does not pay for storage"


class TestCaps:
    def test_capped_by_free_capacity(self):
        plan = plan_bridge(make_slots(2, 12, load_kw=3.0), stored_kwh=0.0, free_kwh=5.0,
                           eta=0.92, min_margin_eur_kwh=0.02, charge_kw=5.0)
        assert plan.need_kwh == pytest.approx(5.0)
        assert plan.capped_by == "free_capacity"

    def test_capped_by_charge_time(self):
        plan = plan_bridge(make_slots(5, 8), stored_kwh=0.0, free_kwh=15.0,
                           eta=0.92, min_margin_eur_kwh=0.02, charge_kw=2.0)
        # only 05:00–06:00 left at 2 kW → at most 2 kWh
        assert plan.need_kwh == pytest.approx(2.0)
        assert plan.capped_by == "charge_time"


class TestNoonWindowAndDeferral:
    def test_noon_bridges_evening_until_night_window(self):
        # 12:00, 1 kW: 16–02 is expensive (10 h) → 10 kWh, PV none.
        plan = plan_bridge(make_slots(12, 16), stored_kwh=3.0, free_kwh=20.0, eta=0.92,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        assert plan.bridge_until == datetime(2026, 12, 11, 2, 0, tzinfo=TZ)
        assert plan.need_kwh == pytest.approx(7.0, abs=0.06)

    def test_equally_cheap_window_before_expensive_slots_defers(self):
        # Flat tariff except a cheap block now and an equally cheap one right after
        rate = lambda h: NIEDRIG if h < 8 else STANDARD
        plan = plan_bridge(make_slots(2, 4, rate=rate), stored_kwh=0, free_kwh=10, eta=0.92,
                           min_margin_eur_kwh=0.02, charge_kw=5.0)
        assert plan.need_kwh == 0.0
        assert plan.reason == "no expensive period ahead"

    def test_cheaper_window_ahead_buys_only_the_remainder(self):
        # now STANDARD 21:00; NIEDRIG 02–06 (4 h × 1 kW = 4 kWh deliverable)
        # lies in the same cheap run; HOCH 17–20 is the worth-bridging stretch.
        rate = lambda h: NIEDRIG if 2 <= h < 6 else (HOCH if 17 <= h < 20 else STANDARD)
        slots = make_slots(21, 24, load_kw=0.0, rate=rate)
        # load only in HOCH: 3 h × 3 kW = 9 kWh → buy 9 − 4 = 5 kWh now
        slots = [s if not (17 <= s.start.hour < 20) else
                 PlanSlot(s.start, s.end, s.rate_eur_kwh, 1.5, 0.0) for s in slots]
        plan = plan_bridge(slots, stored_kwh=0.0, free_kwh=20.0, eta=0.95,
                           min_margin_eur_kwh=0.02, charge_kw=1.0)
        assert plan.reason == "cheaper window ahead"
        assert plan.need_kwh == pytest.approx(5.0, abs=0.06)
