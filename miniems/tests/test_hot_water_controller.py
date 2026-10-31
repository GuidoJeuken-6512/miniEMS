"""hot_water_controller.py: decision order, setpoint mapping, ad hoc, writes."""
from datetime import datetime, timedelta, timezone

import pytest

from bridge_planner import PlanSlot
from const import HW_ADHOC_MAX_SEC, HWMode
from hot_water_controller import HotWaterController

TZ = timezone(timedelta(hours=1))
NOW = datetime(2026, 10, 29, 5, 0, tzinfo=TZ)   # Thursday, 1.5 h before the morning target


class FakeCaller:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str, dict]] = []
        self.active_token = "t"

    async def call(self, domain, service, data):
        self.calls.append((domain, service, data))
        return True


@pytest.fixture
def cfg(make_config):
    return make_config(
        boil_temp_entity="sensor.boil",
        hw_target_entity="sensor.boil_target",
        outdoor_temp_entity="sensor.outdoor",
        hw_write_service="lambda_heat_pumps.write_register",
        hw_write_data='{"register": "boil1_target", "value": {value}}',
        hw_min_off_sec=1800,
    )


def make(cfg, fake_ws, boil=46.0, enabled=False, sim=True):
    cfg.hw_control_enabled = enabled
    cfg.hw_control_simulation = sim
    fake_ws.values.update({"sensor.boil": boil, "sensor.outdoor": 8.5})
    caller = FakeCaller()
    return HotWaterController(cfg, fake_ws, None, caller), caller


def slots(now=NOW, n=12, cheap=(0,), rate=0.30, cheap_rate=0.10):
    out = []
    for i in range(n):
        a = now + timedelta(minutes=30 * i)
        out.append(PlanSlot(a, a + timedelta(minutes=30), cheap_rate if i in cheap else rate, 0.0, 0.0))
    return out


async def tick(ctrl, fake_ws, now=NOW, boil=None, grid_w=0.0, sl=None):
    if boil is not None:
        fake_ws.values["sensor.boil"] = boil
    await ctrl.update(now, sl if sl is not None else slots(now), True, True, grid_w, 0.30)


class TestDecision:
    async def test_handoff_without_boiler_temperature(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws)
        fake_ws.values["sensor.boil"] = None
        await tick(ctrl, fake_ws)
        assert ctrl.mode is HWMode.HANDOFF
        assert ctrl.status(NOW)["setpoint"]["target_c"] == 45.0   # heat pump regulates itself

    async def test_protect_below_minimum(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws)
        await tick(ctrl, fake_ws, boil=43.0)
        st = ctrl.status(NOW)
        assert ctrl.mode is HWMode.PROTECT
        assert st["charge"]["goal_c"] == 50.0                 # shut-off = hw_morning_target_c
        assert st["setpoint"]["target_c"] == 53.0             # impulse: boiler + 10 K

    async def test_plan_charges_in_cheap_slot_before_morning(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws, boil=46.0)
        await tick(ctrl, fake_ws)
        st = ctrl.status(NOW)
        assert ctrl.mode is HWMode.CHARGING
        assert st["charge"]["goal_c"] == 50.0                 # shut-off, not the plan peak
        assert st["setpoint"]["target_c"] == 56.0             # impulse: 46 + 10 (> 50 + 2)
        assert st["plan"]["requirements"][0]["min_c"] == 49.0   # shut-off − 1 K slack
        assert all(w["goal_c"] <= 50.0 for w in st["plan"]["windows"])   # planned only to shut-off

    async def test_plan_charge_ends_at_shutoff_not_at_setpoint(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws, boil=46.0)
        await tick(ctrl, fake_ws)
        assert ctrl.mode is HWMode.CHARGING
        later = NOW + timedelta(minutes=10)
        await tick(ctrl, fake_ws, now=later, boil=49.9, sl=slots(later))
        assert ctrl.mode is HWMode.CHARGING                   # below shut-off: keeps going
        await tick(ctrl, fake_ws, now=later, boil=50.0, sl=slots(later))
        assert ctrl.mode is HWMode.IDLE
        assert ctrl.status(later)["setpoint"]["target_c"] == 35.0

    async def test_idle_writes_idle_setpoint(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        cfg.hw_idle_setpoint_c = 32.0
        ctrl, _ = make(cfg, fake_ws, boil=48.0)
        await tick(ctrl, fake_ws, sl=slots(cheap=()))
        assert ctrl.mode is HWMode.IDLE
        assert ctrl.status(NOW)["setpoint"]["target_c"] == 32.0   # fixed, far below 45

    async def test_goal_reached_stops_and_pauses(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws, boil=46.0)
        await tick(ctrl, fake_ws)
        goal = ctrl.status(NOW)["charge"]["goal_c"]
        later = NOW + timedelta(minutes=20)
        await tick(ctrl, fake_ws, now=later, boil=goal + 0.1, sl=slots(later))
        assert ctrl.mode is HWMode.IDLE
        assert ctrl.status(later)["setpoint"]["target_c"] == cfg.hw_idle_setpoint_c   # 35
        # inside min-off time a new need does not restart (only protection would)
        await tick(ctrl, fake_ws, now=later + timedelta(minutes=5), boil=46.0, sl=slots(later))
        assert ctrl.mode is HWMode.IDLE
        assert "pause" in ctrl.status()["reason"]

    async def test_pv_export_triggers_surplus_charge(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, _ = make(cfg, fake_ws, boil=48.0)
        await tick(ctrl, fake_ws, grid_w=-2500.0, sl=slots(cheap=()))
        assert ctrl.mode is HWMode.PV
        assert ctrl.status(NOW)["charge"]["goal_c"] == cfg.hw_max_temp_c   # PV runs to 55
        assert ctrl.status(NOW)["setpoint"]["target_c"] == 58.0   # max(48 + 10, 55 + 2)


class TestAdhoc:
    async def test_adhoc_impulse_then_off_at_shutoff(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, _ = make(cfg, fake_ws, boil=47.0)
        status = await ctrl.request_adhoc(NOW)
        assert status["active"] is True
        assert status["setpoint_c"] == 57.0   # impulse: 47 + 10
        assert status["target_c"] == 50.0     # shut-off = hw_morning_target_c
        assert ctrl.mode is HWMode.MANUAL     # decided at once, not on the next tick
        await tick(ctrl, fake_ws, boil=48.0, sl=slots(cheap=()))
        assert ctrl.status(NOW)["setpoint"]["target_c"] == 57.0   # fixed at the press
        later = NOW + timedelta(minutes=30)
        await tick(ctrl, fake_ws, now=later, boil=49.9, sl=slots(later, cheap=()))
        assert ctrl.mode is HWMode.MANUAL     # not yet at shut-off
        await tick(ctrl, fake_ws, now=later, boil=50.0, sl=slots(later, cheap=()))
        assert ctrl.mode is HWMode.IDLE
        assert ctrl.status(later)["adhoc"]["active"] is False
        assert ctrl.status(later)["setpoint"]["target_c"] == 35.0   # WW off

    async def test_adhoc_impulse_ignores_boost_delta(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        cfg.hw_boost_delta_k = 15.0
        ctrl, _ = make(cfg, fake_ws, boil=47.0)
        await ctrl.request_adhoc(NOW)
        assert ctrl.status(NOW)["setpoint"]["target_c"] == 57.0

    async def test_adhoc_impulse_at_least_shutoff_plus_margin(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        cfg.hot_water_min_temp_c = 30.0   # keep protection out of the way
        cfg.hw_day_target_c = 30.0
        ctrl, _ = make(cfg, fake_ws, boil=35.0)
        assert (await ctrl.request_adhoc(NOW))["setpoint_c"] == 52.0   # 35 + 10 < 50 + 2

    async def test_adhoc_ignored_when_already_at_shutoff(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, caller = make(cfg, fake_ws, boil=50.5, enabled=True, sim=False)
        status = await ctrl.request_adhoc(NOW)
        assert status["active"] is False
        assert status["info"] == "already at shut-off temperature"
        assert ctrl.mode is not HWMode.MANUAL
        assert caller.calls == []

    async def test_adhoc_writes_immediately(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, caller = make(cfg, fake_ws, boil=47.0, enabled=True, sim=False)
        await ctrl.request_adhoc(NOW)
        assert caller.calls[-1] == ("lambda_heat_pumps", "write_register",
                                    {"register": "boil1_target", "value": 570})

    async def test_confirmed_setpoint_rewritten_when_heat_pump_drifts(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, caller = make(cfg, fake_ws, boil=48.0, enabled=True, sim=False)
        fake_ws.values["sensor.boil_target"] = 35.0   # stale read: looks confirmed already
        await tick(ctrl, fake_ws, sl=slots(cheap=()))
        assert ctrl.mode is HWMode.IDLE
        assert caller.calls == []                      # matched → nothing sent
        fake_ws.values["sensor.boil_target"] = 45.0   # real value arrives (shutdown write)
        await tick(ctrl, fake_ws, now=NOW + timedelta(seconds=30), sl=slots(cheap=()))
        assert caller.calls[-1][2] == {"register": "boil1_target", "value": 350}

    async def test_cancel(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, _ = make(cfg, fake_ws, boil=47.0)
        await ctrl.request_adhoc(NOW)
        await tick(ctrl, fake_ws, sl=slots(cheap=()))
        assert (await ctrl.cancel_adhoc(NOW))["active"] is False
        assert ctrl.mode is HWMode.IDLE
        await tick(ctrl, fake_ws, now=NOW + timedelta(minutes=1), sl=slots(cheap=()))
        assert ctrl.mode is HWMode.IDLE

    async def test_timeout(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, _ = make(cfg, fake_ws, boil=47.0)
        await ctrl.request_adhoc(NOW)
        await tick(ctrl, fake_ws, sl=slots(cheap=()))
        late = NOW + timedelta(seconds=HW_ADHOC_MAX_SEC + 60)
        await tick(ctrl, fake_ws, now=late, sl=slots(late, cheap=()))
        assert ctrl.mode is not HWMode.MANUAL
        assert ctrl.status(late)["adhoc"]["active"] is False

    async def test_protection_wins_over_adhoc(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws, boil=40.0)
        await ctrl.request_adhoc(NOW)
        await tick(ctrl, fake_ws)
        assert ctrl.mode is HWMode.PROTECT


class TestPlannedPvCharge:
    async def test_planned_pv_charge_is_not_stopped_by_grid_import(self, cfg, fake_ws):
        # PV surplus in the first slot makes the plan charge now from PV
        sl = [PlanSlot(s.start, s.end, s.rate_eur_kwh, 0.0, 5.0 if i == 0 else 0.0)
              for i, s in enumerate(slots(cheap=()))]
        ctrl, _ = make(cfg, fake_ws, boil=46.0)
        await tick(ctrl, fake_ws, sl=sl)
        assert ctrl.mode is HWMode.PV
        later = NOW + timedelta(minutes=10)
        # cloud: importing from the grid again, and the re-plan no longer has a window now
        await tick(ctrl, fake_ws, now=later, boil=47.0, grid_w=800.0, sl=slots(later, cheap=()))
        assert ctrl.mode is HWMode.PV                  # keeps going to shut-off

    async def test_spontaneous_pv_charge_stops_when_surplus_is_gone(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        ctrl, _ = make(cfg, fake_ws, boil=48.0)
        await tick(ctrl, fake_ws, grid_w=-2500.0, sl=slots(cheap=()))
        assert ctrl.mode is HWMode.PV
        later = NOW + timedelta(minutes=10)
        await tick(ctrl, fake_ws, now=later, grid_w=500.0, sl=slots(later, cheap=()))
        assert ctrl.mode is HWMode.IDLE


class _Log:
    def __init__(self):
        self.entries = []

    async def append(self, entry):
        self.entries.append(entry)


class TestEventLog:
    async def test_plan_charge_logs_start_and_end_with_heat_cost(self, cfg, fake_ws):
        log = _Log()
        fake_ws.values.update({"sensor.boil": 46.0, "sensor.outdoor": 8.5})
        ctrl = HotWaterController(cfg, fake_ws, None, FakeCaller(), event_log=log)
        await tick(ctrl, fake_ws)                          # plan: charge now in the cheap slot
        assert ctrl.mode is HWMode.CHARGING
        start = [e for e in log.entries if e.state == "hw_on"]
        assert len(start) == 1 and start[0].entry_type == "hw_decision"
        assert start[0].price_eur_kwh == pytest.approx(0.10 / ctrl.model.cop_at(8.5), rel=0.01)
        assert "46.0 → 50 °C" in start[0].reason
        later = NOW + timedelta(minutes=20)
        await tick(ctrl, fake_ws, now=later, boil=50.0, sl=slots(later))
        end = [e for e in log.entries if e.state == "hw_off"]
        assert end and "goal 50 °C reached: 46.0 → 50.0 °C" in end[-1].reason
        assert "kWh_th" in end[-1].reason and end[-1].price_eur_kwh is not None

    async def test_wait_decision_is_logged_only_at_a_tariff_change(self, cfg, fake_ws):
        cfg.hw_morning_time = "23:59"
        log = _Log()
        fake_ws.values.update({"sensor.boil": 48.0, "sensor.outdoor": 8.5})
        ctrl = HotWaterController(cfg, fake_ws, None, FakeCaller(), event_log=log)
        await tick(ctrl, fake_ws, sl=slots(cheap=()))
        t2 = NOW + timedelta(minutes=10)
        await tick(ctrl, fake_ws, now=t2, sl=slots(t2, cheap=()))   # re-plan, same tariff
        assert [e for e in log.entries if e.state == "hw_wait"] == []
        t3 = NOW + timedelta(minutes=40)                             # next slot is dearer
        await tick(ctrl, fake_ws, now=t3, sl=slots(t3, cheap=(), rate=0.35))
        waits = [e for e in log.entries if e.state == "hw_wait"]
        assert len(waits) == 1
        assert waits[0].reason.startswith("tariff change 35.0 ct: wait")


class TestWrites:
    async def test_control_off_writes_nothing(self, cfg, fake_ws):
        ctrl, caller = make(cfg, fake_ws, boil=43.0, enabled=False)
        await tick(ctrl, fake_ws)
        assert caller.calls == []

    async def test_simulation_writes_nothing(self, cfg, fake_ws, caplog):
        ctrl, caller = make(cfg, fake_ws, boil=43.0, enabled=True, sim=True)
        caplog.set_level("INFO")
        await tick(ctrl, fake_ws)
        assert caller.calls == []
        assert "[SIM] lambda_heat_pumps.write_register" in caplog.text
        assert "'value': 530" in caplog.text

    async def test_live_write_scaled_and_confirmed(self, cfg, fake_ws):
        ctrl, caller = make(cfg, fake_ws, boil=43.0, enabled=True, sim=False)
        await tick(ctrl, fake_ws)
        assert caller.calls == [("lambda_heat_pumps", "write_register",
                                 {"register": "boil1_target", "value": 530})]
        assert ctrl.status(NOW)["setpoint"]["confirmed"] is False
        fake_ws.values["sensor.boil_target"] = 53.0
        await tick(ctrl, fake_ws, now=NOW + timedelta(seconds=30))
        assert ctrl.status()["setpoint"]["confirmed"] is True
        assert [e["outcome"] for e in ctrl.pop_write_events()] == ["confirmed"]

    async def test_live_without_service_writes_nothing(self, cfg, fake_ws):
        cfg.hw_write_service = ""
        ctrl, caller = make(cfg, fake_ws, boil=43.0, enabled=True, sim=False)
        await tick(ctrl, fake_ws)
        assert caller.calls == []

    async def test_restore_hands_back_day_target(self, cfg, fake_ws):
        ctrl, caller = make(cfg, fake_ws, boil=43.0, enabled=True, sim=False)
        await ctrl.restore_safe_defaults()
        assert caller.calls[-1][2]["value"] == 450


class TestCoupling:
    async def test_only_live_couples(self, cfg, fake_ws):
        ctrl, _ = make(cfg, fake_ws, boil=46.0, enabled=True, sim=True)
        await tick(ctrl, fake_ws)
        assert not ctrl.couples
        assert ctrl.load_delta_kwh(NOW + timedelta(minutes=30), 0.5) == 0.0
        cfg.hw_control_simulation = False
        assert ctrl.couples
        planned = sum(ctrl.plan.planned_elec_by_end.values())
        assert planned > 0
        total = sum(ctrl.load_delta_kwh(end, 0.5) for end in ctrl.plan.planned_elec_by_end)
        assert total == pytest.approx(planned)   # no flat share learned yet

    async def test_disabled_without_boiler_entity(self, make_config, fake_ws):
        ctrl = HotWaterController(make_config(), fake_ws, None, FakeCaller())
        assert not ctrl.enabled
        await ctrl.update(NOW, None, False, False, None, None)
        assert ctrl.plan is None
