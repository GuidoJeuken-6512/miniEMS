"""A10 – failsafe: the add-on publishes its reset calls, the integration runs them."""
import importlib.util
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from const import EMSMode

from test_ems_controller import make_controller

# integration/failsafe.py has no Home Assistant imports – loaded by path,
# because importing the integration package would pull in homeassistant.
_spec = importlib.util.spec_from_file_location(
    "miniems_failsafe",
    Path(__file__).parents[1] / "rootfs" / "usr" / "bin" / "integration" / "failsafe.py",
)
failsafe = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(failsafe)

T0 = datetime(2026, 11, 16, 12, 0, tzinfo=timezone.utc)
SPEC = {"timeout_min": 15, "actions": [
    {"service": "switch.turn_off", "data": {"entity_id": "switch.grid"}},
]}


class TestWatch:
    def test_fires_once_after_timeout(self):
        w = failsafe.FailsafeWatch()
        w.on_success({"failsafe": SPEC}, T0)
        assert w.due(T0 + timedelta(minutes=14)) is None
        actions = w.due(T0 + timedelta(minutes=15))
        assert actions == SPEC["actions"]
        assert w.due(T0 + timedelta(minutes=30)) is None     # once per outage

    def test_recovery_rearms(self):
        w = failsafe.FailsafeWatch()
        w.on_success({"failsafe": SPEC}, T0)
        w.due(T0 + timedelta(minutes=20))
        assert w.on_success({"failsafe": SPEC}, T0 + timedelta(minutes=25)) is True
        assert w.due(T0 + timedelta(minutes=41)) is not None

    def test_nothing_without_a_good_status(self):
        w = failsafe.FailsafeWatch(SPEC)
        assert w.due(T0 + timedelta(days=1)) is None

    def test_status_without_spec_disarms(self):
        w = failsafe.FailsafeWatch()
        w.on_success({"failsafe": SPEC}, T0)
        w.on_success({}, T0 + timedelta(minutes=1))           # simulation again
        assert w.due(T0 + timedelta(hours=1)) is None

    @pytest.mark.parametrize("raw", [
        None, {}, {"timeout_min": 0, "actions": SPEC["actions"]},
        {"timeout_min": 10, "actions": [{"service": "nodot", "data": {}}]},
        {"timeout_min": "x", "actions": SPEC["actions"]},
    ])
    def test_invalid_specs(self, raw):
        assert failsafe.valid_spec(raw) is None


class TestAddonSpec:
    def test_inverter_actions_only_when_live(self, make_config, fake_ws):
        from inverter_controller import InverterController
        sim = InverterController(make_config(battery_control_enabled=True,
                                             battery_control_simulation=True), "", ws_client=fake_ws)
        assert sim.failsafe_actions() == []
        cfg = make_config(battery_control_enabled=True, battery_control_simulation=False,
                          grid_export_limit_entity="number.export", grid_export_limit_normal_w=8000)
        live = InverterController(cfg, "", ws_client=fake_ws)
        services = [(a["service"], a["data"]["entity_id"]) for a in live.failsafe_actions()]
        assert ("switch.turn_off", cfg.grid_charge_switch_entity) in services
        assert ("number.set_value", cfg.inverter_charge_current_entity) in services
        assert ("number.set_value", "number.export") in services
        values = {a["data"]["entity_id"]: a["data"].get("value") for a in live.failsafe_actions()}
        assert values[cfg.battery_discharging_current_entity] == cfg.battery_max_discharge_current_a

    def test_hot_water_action_writes_floor(self, make_config, fake_ws):
        from hot_water_controller import HotWaterController
        cfg = make_config(boil_temp_entity="sensor.boil", hw_control_enabled=True,
                          hw_control_simulation=False, hw_idle_setpoint_c=35.0)
        hw = HotWaterController(cfg, fake_ws, None, None)
        actions = hw.failsafe_actions()
        assert actions[0]["service"] == "lambda_heat_pumps.write_modbus_register"
        assert actions[0]["data"]["register_address"] == 2050

    def test_controller_spec(self, make_config, fake_ws):
        class Inv:
            def failsafe_actions(self):
                return [{"service": "switch.turn_off", "data": {"entity_id": "switch.g"}}]

        ctrl = make_controller(make_config, fake_ws, failsafe_timeout_min=10)
        assert ctrl._failsafe_spec() is None                  # nothing live
        ctrl._inverter = Inv()
        assert ctrl._failsafe_spec()["timeout_min"] == 10
        off = make_controller(make_config, fake_ws, failsafe_timeout_min=0)
        off._inverter = Inv()
        assert off._failsafe_spec() is None


class TestWriteBudget:
    @pytest.mark.asyncio
    async def test_budget_freezes_stretched_current(self, make_config, fake_ws):
        from inverter_controller import InverterController
        cfg = make_config(battery_control_enabled=True, battery_control_simulation=True,
                          write_budget_per_hour=2, battery_voltage_entity="sensor.v")
        fake_ws.values["sensor.v"] = 50.0
        inv = InverterController(cfg, "", ws_client=fake_ws)
        await inv.apply_mode(EMSMode.GRID_CHARGING, 2000)     # 40 A
        await inv.apply_mode(EMSMode.GRID_CHARGING, 3000)     # 60 A – second change
        first = inv.charge_current_target_a
        await inv.apply_mode(EMSMode.GRID_CHARGING, 1000)     # budget used: stays
        assert inv.charge_current_target_a == first
        assert inv.target_changes_today["charge"] == 2
        # a mode's fixed safety value always goes through
        await inv.apply_mode(EMSMode.IDLE)
        assert inv.charge_current_target_a == cfg.battery_max_charge_current_a

    @pytest.mark.asyncio
    async def test_no_budget_by_default(self, make_config, fake_ws):
        from inverter_controller import InverterController
        cfg = make_config(battery_control_enabled=True, battery_voltage_entity="sensor.v")
        fake_ws.values["sensor.v"] = 50.0
        inv = InverterController(cfg, "", ws_client=fake_ws)
        for w in (2000, 3000, 1000, 2500):
            await inv.apply_mode(EMSMode.GRID_CHARGING, w)
        assert inv.charge_current_target_a == 50
