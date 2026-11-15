"""A2–A4: value of a kWh (energy_value.py) and its use in EMSController."""
from datetime import datetime, timedelta, timezone

import pytest

from bridge_planner import PlanSlot
from const import EMSMode
from energy_value import assess_energy, should_hold

from test_ems_controller import make_controller

TZ = timezone(timedelta(hours=1))
L, S, H = 0.274414, 0.3444, 0.3944


def octopus_rate(t: datetime) -> float:
    h = t.hour
    if 2 <= h < 6 or 12 <= h < 16:
        return L
    if 18 <= h < 21:
        return H
    return S


def slots_from(start: datetime, hours: float, load=lambda t: 0.4, pv=lambda t: 0.0):
    out = []
    t = start
    end = start + timedelta(hours=hours)
    while t < end:
        out.append(PlanSlot(t, t + timedelta(minutes=30), octopus_rate(t), load(t), pv(t)))
        t += timedelta(minutes=30)
    return out


def at(hour: int, day: int = 16) -> datetime:
    return datetime(2026, 11, day, hour, 0, tzinfo=TZ)


class TestAssessEnergy:
    """docs/roadmap/gesamtplan.md §10, A3: same price, SoC 20 % vs 70 %."""

    def test_low_soc_buys_high_soc_does_not(self):
        slots = slots_from(at(2), 20)               # NIEDRIG 02–06, then STANDARD until 12
        low = assess_energy(slots, usable_kwh=0.5, free_kwh=8.0, eta=0.9, min_margin_eur_kwh=0.02)
        high = assess_energy(slots, usable_kwh=5.5, free_kwh=2.5, eta=0.9, min_margin_eur_kwh=0.02)
        assert low.later_slots == 12                # 06:00–12:00
        assert low.pays and low.buy_kwh == pytest.approx(4.8 - 0.5)
        assert low.first_value_eur_kwh == pytest.approx(S)
        assert not high.pays

    def test_hold_serves_the_dearest_hours_first(self):
        slots = slots_from(at(16), 10)                # STANDARD 16–18, HOCH 18–21, STANDARD
        by_clock = assess_energy(slots, usable_kwh=1.6, free_kwh=5.0, eta=0.9)
        by_price = assess_energy(slots, usable_kwh=1.6, free_kwh=5.0, eta=0.9, hold=True)
        # the clock spends the energy on 16–18 STANDARD, the hold keeps it for HOCH
        assert by_clock.first_value_eur_kwh == pytest.approx(H)
        assert by_price.first_value_eur_kwh == pytest.approx(S)
        assert by_price.uncovered_kwh == pytest.approx(by_clock.uncovered_kwh)

    def test_spread_too_small_does_not_pay(self):
        slots = slots_from(at(2), 20)
        v = assess_energy(slots, usable_kwh=0.0, free_kwh=8.0, eta=0.75, min_margin_eur_kwh=0.02)
        assert not v.pays                            # 0.75 × 34.4 − 27.4 < 2 ct

    def test_wear_cost_reduces_the_margin(self):
        slots = slots_from(at(2), 20)
        v = assess_energy(slots, usable_kwh=0.0, free_kwh=8.0, eta=0.9,
                          min_margin_eur_kwh=0.02, wear_eur_kwh=0.03)
        assert not v.pays

    def test_pv_later_covers_the_need(self):
        slots = slots_from(at(2), 20, pv=lambda t: 1.0 if 7 <= t.hour < 12 else 0.0)
        v = assess_energy(slots, usable_kwh=0.5, free_kwh=8.0, eta=0.9, min_margin_eur_kwh=0.02)
        assert v.buy_kwh < 1.0

    def test_buy_capped_by_free_capacity(self):
        slots = slots_from(at(2), 20, load=lambda t: 2.0)
        v = assess_energy(slots, usable_kwh=0.0, free_kwh=3.0, eta=0.9)
        assert v.buy_kwh == pytest.approx(3.0)

    def test_no_rate_gives_none(self):
        assert assess_energy([PlanSlot(at(2), at(2) + timedelta(minutes=30), None, 0.4, 0)],
                             1.0, 1.0, 0.9) is None


class TestShouldHold:
    """§10, A4: winter day without PV, 6 kWh in HOCH."""

    @staticmethod
    def _slots(start):
        return slots_from(start, 30, load=lambda t: 1.0 if 18 <= t.hour < 21 else 0.2)

    def test_holds_at_16_when_soc_50(self):
        hold, v = should_hold(self._slots(at(16)), usable_kwh=3.5, free_kwh=4.5,
                              eta_discharge=0.95, min_spread_eur_kwh=0.03, holding=False)
        assert hold
        assert v.held_for_kwh == pytest.approx(6.0)

    def test_discharges_once_hoch_starts(self):
        hold, _ = should_hold(self._slots(at(18)), usable_kwh=3.5, free_kwh=4.5,
                              eta_discharge=0.95, min_spread_eur_kwh=0.03, holding=True)
        assert not hold

    def test_no_hold_with_soc_95(self):
        hold, _ = should_hold(self._slots(at(16)), usable_kwh=8.0, free_kwh=0.0,
                              eta_discharge=0.95, min_spread_eur_kwh=0.03, holding=False)
        assert not hold

    def test_hysteresis_keeps_holding_slightly_above(self):
        slots = self._slots(at(16))
        # available 6.2 > need 6.0: would not enter, but keeps holding
        assert not should_hold(slots, 6.2, 2.0, 1.0, 0.03, holding=False)[0]
        assert should_hold(slots, 6.2, 2.0, 1.0, 0.03, holding=True)[0]

    def test_small_spread_never_holds(self):
        hold, _ = should_hold(self._slots(at(16)), 1.0, 7.0, 0.95, min_spread_eur_kwh=0.10,
                              holding=False)
        assert not hold                              # HOCH is only 5 ct above STANDARD


class FakeForecaster:
    class Model:
        days = 30

    def __init__(self, kwh_per_hour=0.8):
        self.model = self.Model()
        self.ready = True
        self._kwh = kwh_per_hour

    async def refresh(self, now):
        pass

    def predict_kwh(self, start, minutes, temp_c):
        return self._kwh * minutes / 60.0


class TestController:
    def test_hold_decision_after_grid_charge_check(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, discharge_hold_enabled=True)
        ctrl._hold = (True, "hold for peak: test")
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=50.0, price=S, bat_kwh_free=4.0, now=at(16))
        assert d.mode is EMSMode.HOLD_FOR_PEAK
        assert d.reason.startswith("hold for peak")

    def test_hold_ignored_when_switch_off(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, discharge_hold_enabled=False)
        ctrl._hold = (True, "hold for peak: test")
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=50.0, price=S, bat_kwh_free=4.0, now=at(16))
        assert d.mode is EMSMode.IDLE

    def test_grid_charge_wins_over_hold(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, discharge_hold_enabled=True,
                               grid_charge_dark_start_hour=0, grid_charge_dark_end_hour=23)
        ctrl._hold = (True, "hold for peak: test")
        d = ctrl._decide(pv_w=0, load_w=500, bat_soc=30.0, price=0.05, bat_kwh_free=6.0, now=at(3))
        assert d.mode is EMSMode.GRID_CHARGING

    @pytest.mark.asyncio
    async def test_update_energy_value_builds_hold(self, make_config, fake_ws):
        from test_price_curve import _octopus_timeslots
        ctrl = make_controller(make_config, fake_ws, discharge_hold_enabled=True,
                               soc_aware_gate_enabled=True)
        ctrl._forecaster = FakeForecaster(kwh_per_hour=1.2)
        fake_ws.attributes[ctrl._cfg.electricity_price_entity] = _octopus_timeslots()
        await ctrl._update_energy_value(at(13), L, None, bat_kwh_use=2.0, bat_kwh_free=6.0)
        assert ctrl._value_slots
        assert ctrl._hold[0] is True
        assert ctrl._buy_value is not None and ctrl._buy_value.pays
        assert ctrl._energy_value_dict()["holding"] is True

    @pytest.mark.asyncio
    async def test_update_energy_value_inert_when_off(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._forecaster = FakeForecaster()
        await ctrl._update_energy_value(at(13), L, None, 2.0, 6.0)
        assert ctrl._value_slots is None and ctrl._energy_value_dict() is None

    def test_soc_aware_gate_replaces_legacy(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, soc_aware_gate_enabled=True)
        ctrl._discharge_tariff = 0.10            # legacy would refuse: 0.9 × 0.10 − 0.27 < 0
        ctrl._buy_value = assess_energy(slots_from(at(2), 20), 0.5, 8.0, eta=0.9,
                                        min_margin_eur_kwh=0.02)
        assert ctrl._grid_charge_pays_legacy(L) is False
        assert ctrl._grid_charge_pays(L) is True

    def test_load_between_sums_model(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        assert ctrl._load_between(at(0), at(6)) is None
        ctrl._forecaster = FakeForecaster(kwh_per_hour=0.5)
        assert ctrl._load_between(at(0), at(6)) == pytest.approx(3.0)
        assert ctrl._load_between(at(6), at(6)) == 0.0

    def test_v2_does_not_defer_when_battery_is_needed(self, make_config, fake_ws):
        from test_price_curve import _octopus_timeslots
        cfg = dict(load_profile_enabled=True, battery_voltage_entity="sensor.v",
                   solcast_peak_time_today_entity="sensor.peak_today",
                   solcast_peak_time_tomorrow_entity="sensor.peak_tomorrow")
        ctrl = make_controller(make_config, fake_ws, **cfg)
        fake_ws.attributes[ctrl._cfg.electricity_price_entity] = _octopus_timeslots()
        fake_ws.values["sensor.v"] = 50.0
        fake_ws.datetimes["sensor.peak_tomorrow"] = at(12, day=17)
        now = at(12)                                  # afternoon NIEDRIG, next NIEDRIG at 02:00
        # Evening load before 02:00 → not deferring
        ctrl._value_slots = slots_from(now, 20, load=lambda t: 0.5)
        assert ctrl._should_defer_grid_charge(3.0, now) is False
        # No load before 02:00 → deferring is free, as before
        ctrl._value_slots = slots_from(now, 20, load=lambda t: 0.0)
        assert ctrl._should_defer_grid_charge(3.0, now) is True


class TestInverterHold:
    @pytest.mark.asyncio
    async def test_hold_blocks_discharge_like_protect(self, make_config, fake_ws):
        from inverter_controller import InverterController
        cfg = make_config(battery_control_enabled=True, battery_control_simulation=True)
        inv = InverterController(cfg, "", ws_client=fake_ws)
        await inv.apply_mode(EMSMode.HOLD_FOR_PEAK)
        assert inv.discharge_current_target_a == 0
        assert inv.charge_current_target_a == cfg.battery_max_charge_current_a
