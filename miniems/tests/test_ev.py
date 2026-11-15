"""B3–B5 – EV: wallbox interface, override, planning, PV surplus."""
from datetime import datetime, timedelta, timezone

import pytest

from energy_task import TaskSlot
from ev_controller import EVController, EVMode, _parse_departure, parse_mode
from ev_planner import (
    CHARGE_LOSS_FACTOR, UNKNOWN_SOC_PCT, EVTarget, deadline_for, energy_need_kwh, plan_ev,
)
from ev_surplus import (
    ADJUST_SEC, MIN_RUN_SEC, SWITCH_LOCK_SEC, SurplusRegulator,
)

TZ = timezone(timedelta(hours=1))
EVENING = datetime(2026, 11, 16, 18, 0, tzinfo=TZ)

EV_ENT = dict(
    ev_charge_power_entity="sensor.wb_power",
    ev_plugged_entity="binary_sensor.wb_plugged",
    ev_enable_entity="switch.wb_enable",
    ev_current_entity="number.wb_current",
    ev_phases_entity="select.wb_phases",
    ev_mode_entity="input_select.ev_mode",
    ev_status_entity="sensor.wb_status",
)


class FakeCaller:
    def __init__(self):
        self.calls = []
        self.active_token = "t"

    async def call(self, domain, service, data):
        self.calls.append((domain, service, data))
        return True


def night_slots(prices, start=EVENING, base_w=500.0, limit_w=None):
    out = []
    for i, p in enumerate(prices):
        s = start + timedelta(minutes=30 * i)
        out.append(TaskSlot(s, s + timedelta(minutes=30), p, base_load_w=base_w,
                            feed_in_eur_kwh=0.08, limit_w=limit_w))
    return out


# ----------------------------------------------------------------------
# B4 – planning
# ----------------------------------------------------------------------

class TestPlanning:
    def test_energy_need_includes_losses(self):
        need, assumed = energy_need_kwh(30, 80, 60)
        assert need == pytest.approx(0.5 * 60 * CHARGE_LOSS_FACTOR)
        assert not assumed

    def test_unknown_soc_assumes_20_percent(self):
        need, assumed = energy_need_kwh(None, 80, 50, charged_kwh=5)
        assert assumed
        assert need == pytest.approx((80 - UNKNOWN_SOC_PCT) / 100 * 50 * CHARGE_LOSS_FACTOR - 5)

    def test_full_target_one_hour_earlier(self):
        dep = EVENING + timedelta(hours=13)
        assert deadline_for(EVTarget(50, 100, 50, dep)) == dep - timedelta(hours=1)
        assert deadline_for(EVTarget(50, 90, 50, dep)) == dep

    def test_plans_into_the_cheapest_night_slots(self):
        prices = [0.34] * 16 + [0.27] * 8 + [0.34] * 4          # 18:00 … 08:00
        sl = night_slots(prices)
        tgt = EVTarget(40, 80, 50, departure=EVENING + timedelta(hours=14))
        plan = plan_ev(tgt, sl, min_w=1380, max_w=11000, now=EVENING)
        assert plan.feasible
        cheap = sum(p for p, s in zip(plan.power_w, sl) if s.price_eur_kwh < 0.3)
        assert cheap == pytest.approx(sum(plan.power_w))
        assert plan.need_kwh == pytest.approx(0.4 * 50 * CHARGE_LOSS_FACTOR)

    def test_morning_minimum_comes_first(self):
        # Cheap only after 06:00, the morning minimum must come from dearer hours.
        prices = [0.34] * 24 + [0.10] * 4
        sl = night_slots(prices)
        tgt = EVTarget(20, 80, 50, departure=EVENING + timedelta(hours=14), morning_pct=50,
                       morning_at=EVENING + timedelta(hours=12))
        plan = plan_ev(tgt, sl, min_w=1380, max_w=11000, now=EVENING)
        before_morning = sum(p * s.hours for p, s in zip(plan.power_w, sl)
                             if s.end <= tgt.morning_at) / 1000
        assert before_morning >= 0.3 * 50 * CHARGE_LOSS_FACTOR - 0.05
        assert plan.morning_ok

    def test_not_reachable_is_reported(self):
        sl = night_slots([0.30] * 2, limit_w=6000)
        tgt = EVTarget(10, 100, 80, departure=EVENING + timedelta(hours=2))
        plan = plan_ev(tgt, sl, min_w=1380, max_w=11000, now=EVENING)
        assert not plan.feasible
        assert "not reachable" in plan.reason

    def test_fuse_power_per_slot(self):
        sl = night_slots([0.27] * 20, base_w=3000, limit_w=3 * 230 * 31.5)
        tgt = EVTarget(20, 80, 50, departure=EVENING + timedelta(hours=10))
        plan = plan_ev(tgt, sl, min_w=1380, max_w=11000, now=EVENING)
        assert max(plan.power_w) <= 3 * 230 * 31.5 - 3000 + 1


# ----------------------------------------------------------------------
# B5 – PV surplus
# ----------------------------------------------------------------------

def regulator(can_switch=True):
    return SurplusRegulator(6, 16, 3, 230.0, 690.0, can_switch)


class TestSurplus:
    def test_starts_single_phase_at_minimum(self):
        r = regulator()
        assert not r.step(0, 1200).charging
        cmd = r.step(30, 1500)
        assert cmd.charging and cmd.phases == 1 and cmd.current_a == 6

    def test_switch_to_three_phases_from_18a(self):
        r = regulator()
        r.step(0, 3000)
        cmd = r.step(ADJUST_SEC, 18 * 230 + 10)
        assert cmd.phases == 3
        assert cmd.current_a == pytest.approx(6)          # 4150 W / 690 → 6 A

    def test_switch_lock(self):
        r = regulator()
        r.step(0, 3000)
        r.step(ADJUST_SEC, 4200)                           # → 3 phases
        cmd = r.step(2 * ADJUST_SEC, 2500)                 # 3.6 A three-phase, locked
        assert cmd.phases == 3
        cmd = r.step(ADJUST_SEC + SWITCH_LOCK_SEC + 1, 3000)   # 4.3 A < 5.5 A → back
        assert cmd.phases == 1

    def test_variable_sun_stays_single_phase(self):
        r = regulator()
        r.step(0, 1500)
        r.step(60, 6000)
        cmd = r.step(ADJUST_SEC, 4500)
        assert cmd.phases == 1

    def test_minimum_run_then_pause_with_zero_amps(self):
        r = regulator()
        r.step(0, 2000)
        cmd = r.step(ADJUST_SEC, 300)
        assert cmd.charging and cmd.current_a == 6         # min run time
        cmd = r.step(MIN_RUN_SEC + ADJUST_SEC, 300)
        assert not cmd.charging and cmd.current_a == 0

    def test_fast_adjust_when_buying_expensive(self):
        r = regulator()
        r.step(0, 3000)
        assert r.step(70, 1700, expensive_import=False).current_a == 13   # not yet
        assert r.step(70, 1700, expensive_import=True).current_a == 7

    def test_fuse_cap(self):
        r = regulator(can_switch=False)
        cmd = r.step(0, 9000, cap_w=4140)
        assert cmd.current_a == 6


# ----------------------------------------------------------------------
# B3 – controller
# ----------------------------------------------------------------------

def make_ev(make_config, fake_ws, caller=None, **kw):
    cfg = make_config(**{**EV_ENT, **kw})
    return EVController(cfg, fake_ws, caller or FakeCaller()), cfg


def plug(fake_ws, mode="Auto", power=0.0):
    fake_ws.state_cache["binary_sensor.wb_plugged"] = {"state": "on"}
    fake_ws.state_cache["input_select.ev_mode"] = {"state": mode}
    fake_ws.values["sensor.wb_power"] = power


class TestController:
    def test_mode_words(self):
        assert parse_mode("Jetzt") is EVMode.NOW
        assert parse_mode("Nur PV") is EVMode.PV
        assert parse_mode("Stopp") is EVMode.STOP
        assert parse_mode("unknown") is EVMode.AUTO
        assert parse_mode(None) is EVMode.AUTO

    def test_kw_per_amp_from_max_power(self, make_config, fake_ws):
        ev, _ = make_ev(make_config, fake_ws, ev_max_charge_kw=7.4, ev_max_current_a=16)
        assert ev.w_per_a(3) == pytest.approx(462.5)       # two-phase on-board charger
        assert ev.w_per_a(1) == 230.0

    def test_departure_parsing(self):
        assert _parse_departure("07:15:00", EVENING) == EVENING.replace(hour=7, minute=15) + timedelta(days=1)
        dt = _parse_departure("2026-11-17 06:00:00", EVENING)
        assert dt == datetime(2026, 11, 17, 6, 0, tzinfo=TZ)
        assert _parse_departure("2026-11-01 06:00:00", EVENING) is None   # in the past

    @pytest.mark.asyncio
    async def test_unplugged_writes_nothing(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True,
                        ev_control_simulation=False)
        fake_ws.state_cache["binary_sensor.wb_plugged"] = {"state": "off"}
        await ev.update(EVENING, slots=None, grid_w=0, t=0)
        assert caller.calls == []
        assert ev.status(EVENING)["command"]["source"] == "unplugged"

    @pytest.mark.asyncio
    async def test_stop_override(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True,
                        ev_control_simulation=False)
        plug(fake_ws, "Stopp")
        fake_ws.state_cache["switch.wb_enable"] = {"state": "on"}
        await ev.update(EVENING, slots=None, grid_w=0, t=0)
        assert ("switch", "turn_off", {"entity_id": "switch.wb_enable"}) in caller.calls

    @pytest.mark.asyncio
    async def test_now_override_respects_the_fuse(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True,
                        ev_control_simulation=False)
        plug(fake_ws, "Jetzt")
        await ev.update(EVENING, slots=None, grid_w=0, cap_w=5000, t=0)
        cmd = ev.status(EVENING)["command"]
        assert cmd["current_a"] == 7                      # 5000 W / 690 W/A
        assert ("number", "set_value", {"entity_id": "number.wb_current", "value": 7.0}) in caller.calls

    @pytest.mark.asyncio
    async def test_simulation_sends_nothing(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True)
        plug(fake_ws, "Jetzt")
        await ev.update(EVENING, slots=None, grid_w=0, t=0)
        assert caller.calls == []
        assert ev.failsafe_actions() == []

    @pytest.mark.asyncio
    async def test_control_off_only_reads(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller)
        plug(fake_ws, "Jetzt", power=7000)
        await ev.update(EVENING, slots=None, grid_w=0, t=0)
        assert caller.calls == []
        assert ev.charging and ev.status(EVENING)["power_w"] == 7000

    @pytest.mark.asyncio
    async def test_auto_follows_the_plan(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True,
                        ev_control_simulation=False, ev_morning_soc_pct=0)
        plug(fake_ws, "Auto")
        fake_ws.values["sensor.ev_soc"] = 40
        sl = night_slots([0.27] * 4 + [0.34] * 24)
        await ev.update(EVENING, slots=sl, grid_w=500, t=0)
        cmd = ev.status(EVENING)["command"]
        assert cmd["source"] == "plan" and cmd["current_a"] == 16
        assert ev.extra_load_kwh(sl[0].start, sl[0].end) > 0

    @pytest.mark.asyncio
    async def test_failsafe_hands_the_wallbox_back(self, make_config, fake_ws):
        ev, _ = make_ev(make_config, fake_ws, ev_control_enabled=True,
                        ev_control_simulation=False)
        actions = ev.failsafe_actions()
        assert {"service": "switch.turn_on", "data": {"entity_id": "switch.wb_enable"}} in actions
        assert {"service": "number.set_value",
                "data": {"entity_id": "number.wb_current", "value": 16.0}} in actions

    @pytest.mark.asyncio
    async def test_hanging_charge_gets_a_turn_on(self, make_config, fake_ws):
        caller = FakeCaller()
        ev, _ = make_ev(make_config, fake_ws, caller, ev_control_enabled=True,
                        ev_control_simulation=False)
        plug(fake_ws, "Jetzt")
        fake_ws.state_cache["switch.wb_enable"] = {"state": "on"}
        fake_ws.state_cache["sensor.wb_status"] = {"state": "waiting"}
        for t in (0, 30, 200):
            await ev.update(EVENING, slots=None, grid_w=0, t=t)
        turn_ons = [c for c in caller.calls if c[1] == "turn_on"]
        assert len(turn_ons) == 1
        assert ev.status(EVENING)["stuck_retries"] == 1
