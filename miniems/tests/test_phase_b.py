"""Phase B end to end through EMSController.update() (phase_b.py wiring)."""
import pytest
from freezegun import freeze_time

from const import EMSMode
from ev_controller import EVController
from heating_controller import HeatingController
from resources import ResourceCoordinator

from test_ems_controller_update import _NIEDRIG_0206_TIMESLOTS, build_controller

PHASES = "sensor.l1_a,sensor.l2_a,sensor.l3_a"


class FakeCaller:
    def __init__(self):
        self.calls = []
        self.active_token = "t"

    async def call(self, domain, service, data):
        self.calls.append((domain, service, data))
        return True


def base_values(fake_ws, **kw):
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 500.0, "sensor.grid": 500.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30, **kw,
    })
    fake_ws.attributes["sensor.price"] = _NIEDRIG_0206_TIMESLOTS


@pytest.mark.asyncio
async def test_all_off_adds_nothing(make_config, fake_ws):
    ctrl, *_ = build_controller(make_config, fake_ws)
    ctrl._init_phase_b(EVController(ctrl._cfg, fake_ws, None),
                       HeatingController(ctrl._cfg, fake_ws, None, None),
                       ResourceCoordinator(ctrl._cfg, fake_ws), None)
    base_values(fake_ws)
    result = await ctrl.update()
    for key in ("fuse", "ev", "heating", "best_start"):
        assert key not in result


@pytest.mark.asyncio
async def test_fuse_guard_caps_the_grid_charge(make_config, fake_ws):
    ctrl, _, inverter, _ = build_controller(
        make_config, fake_ws, mode_dwell_sec=0, cheap_rate_threshold_eur=0.5,
        battery_max_charge_current_a=100, battery_max_soc=90,
        fuse_guard_enabled=True, fuse_rated_a=25.0, phase_current_entities=PHASES,
        battery_control_enabled=True)
    cfg = ctrl._cfg
    ctrl._init_phase_b(None, None, ResourceCoordinator(cfg, fake_ws), None)
    base_values(fake_ws, **{"sensor.price": 0.05})
    for e in PHASES.split(","):
        fake_ws.values[e] = 21.5                 # 22.5 A working limit → 1 A left per phase
    with freeze_time("2026-08-15 03:00:00"):
        result = await ctrl.update()
    assert result["mode"] == EMSMode.GRID_CHARGING.value
    assert result["fuse"]["headroom_a"] == pytest.approx(1.0, abs=0.1)
    # V1 alone asks for ~1667 W; the fuse leaves 3 × 230 V × 1 A = 690 W.
    assert inverter.last_grid_charge_power_w == pytest.approx(690, abs=5)


@pytest.mark.asyncio
async def test_ev_charging_holds_the_battery(make_config, fake_ws):
    ctrl, _, inverter, _ = build_controller(
        make_config, fake_ws, mode_dwell_sec=0,
        ev_charge_power_entity="sensor.wb_power", ev_plugged_entity="binary_sensor.wb",
        ev_mode_entity="input_select.ev", ev_control_enabled=True)
    caller = FakeCaller()
    ev = EVController(ctrl._cfg, fake_ws, caller)
    ctrl._init_phase_b(ev, None, None, None)
    base_values(fake_ws, **{"sensor.wb_power": 7000.0})
    fake_ws.state_cache["binary_sensor.wb"] = {"state": "on"}
    fake_ws.state_cache["input_select.ev"] = {"state": "Jetzt"}
    with freeze_time("2026-08-15 20:00:00"):
        result = await ctrl.update()
    assert result["mode"] == EMSMode.HOLD_FOR_PEAK.value
    assert "ev charging" in result["mode_reason"]
    assert result["ev"]["command"]["source"] == "now"
    assert caller.calls == []                     # simulation


@pytest.mark.asyncio
async def test_heating_and_best_start_status(make_config, fake_ws):
    ctrl, *_ = build_controller(
        make_config, fake_ws, heating_control_enabled=True,
        heating_offset_entity="number.offset", heating_room_temp_entity="",
        flex_devices="Waschmaschine:1.2:2")
    ctrl._init_phase_b(None, HeatingController(ctrl._cfg, fake_ws, None, FakeCaller()),
                       None, None)
    base_values(fake_ws)
    with freeze_time("2026-08-15 10:00:00"):
        result = await ctrl.update()
    assert result["heating"]["control"] == "simulation"
    assert result["heating"]["reason"].startswith("startup")
    assert result["best_start"][0]["name"] == "Waschmaschine"


@pytest.mark.asyncio
async def test_failsafe_includes_live_phase_b_actuators(make_config, fake_ws):
    ctrl, *_ = build_controller(
        make_config, fake_ws, heating_control_enabled=True, heating_control_simulation=False,
        heating_offset_entity="number.offset")
    ctrl._init_phase_b(None, HeatingController(ctrl._cfg, fake_ws, None, FakeCaller()),
                       None, None)
    spec = ctrl._failsafe_spec()
    assert {"service": "number.set_value",
            "data": {"entity_id": "number.offset", "value": 0.0}} in spec["actions"]


@pytest.mark.asyncio
async def test_fast_tick_reapplies_on_overload(make_config, fake_ws):
    ctrl, _, inverter, _ = build_controller(
        make_config, fake_ws, fuse_guard_enabled=True, fuse_rated_a=25.0,
        phase_current_entities=PHASES, battery_control_enabled=True)
    ctrl._init_phase_b(None, None, ResourceCoordinator(ctrl._cfg, fake_ws), None)
    ctrl._mode = EMSMode.GRID_CHARGING
    ctrl._last_readings = {"pv_w": 0.0, "load_w": 500.0, "grid_w": 9000.0, "bat_w": -8000.0}
    for e in PHASES.split(","):
        fake_ws.values[e] = 31.0                 # above 1.2 × 25 A
    await ctrl.fast_tick()
    assert inverter.applied_modes == [EMSMode.GRID_CHARGING]
    assert inverter.last_grid_charge_power_w == 0.0
