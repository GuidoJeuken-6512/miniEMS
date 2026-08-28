"""InverterController: mode application, write confirm/retry, simulation."""
import asyncio

import pytest
from aioresponses import aioresponses

from const import EMSMode, HA_SERVICES_URL
from inverter_controller import InverterController


class FakeWS:
    """Read-only double: state_cache for switch, get_state_value for numbers,
    get_state_attribute for the live min/max/step device_control.py reads."""

    def __init__(self) -> None:
        self.state_cache: dict = {}
        self.values: dict = {}
        self.attributes: dict = {}

    def get_state_value(self, entity_id):
        return self.values.get(entity_id)

    def get_state_attribute(self, entity_id, attribute):
        return self.attributes.get(entity_id, {}).get(attribute)


@pytest.fixture
def ws():
    return FakeWS()


def make_ctrl(make_config, ws, simulation=True, **overrides):
    defaults = dict(
        battery_control_enabled=True, battery_control_simulation=simulation,
        grid_charge_switch_entity="switch.grid_charge",
        inverter_charge_current_entity="number.charge_current",
        battery_discharging_current_entity="number.discharge_current",
        battery_max_charge_current_a=185, battery_max_discharge_current_a=185,
        export_hold_charge_current_a=0,
    )
    defaults.update(overrides)
    cfg = make_config(**defaults)
    return InverterController(cfg, supervisor_token="sup-token", ws_client=ws)


class TestSimulationMode:
    @pytest.mark.asyncio
    async def test_grid_charging_sets_expected_targets(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.charge_current_target_a == 185
        assert ctrl.discharge_current_target_a == 0
        assert ctrl.charge_current_limit_a == 185   # sim confirms immediately
        assert ctrl.discharge_current_limit_a == 0

    @pytest.mark.asyncio
    async def test_grid_charging_power_override_is_used(self, make_config, ws):
        """V1 (docs/roadmap/energiefahrplan.md): EMSController can request a
        stretched power target (Watts) instead of full blast. No live
        battery_voltage configured here -> device_control.py falls back to
        its assumed 48V, so 2016 W -> 2016/48 = 42 A."""
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=2016.0)
        assert ctrl.charge_current_target_a == 42
        assert ctrl.charge_current_limit_a == 42

    @pytest.mark.asyncio
    async def test_grid_charging_power_override_uses_live_voltage_when_available(self, make_config, ws):
        ws.values["sensor.deye8k_battery_voltage"] = 50.0
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1000.0)
        assert ctrl.charge_current_target_a == 20   # 1000 / 50

    @pytest.mark.asyncio
    async def test_grid_charging_none_override_keeps_full_current(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=None)
        assert ctrl.charge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_grid_charging_power_override_never_exceeds_configured_max(self, make_config, ws):
        """The user's configured cap always wins as the outer bound, even
        against a huge requested Watt target."""
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1_000_000.0)
        assert ctrl.charge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_grid_charging_respects_a_live_max_below_the_configured_cap(self, make_config, ws):
        """A BMS-imposed live max (e.g. 65 A, below the configured 185 A)
        must tighten the effective ceiling, not be ignored."""
        ws.attributes["number.charge_current"] = {"min": 0, "max": 65, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1_000_000.0)
        assert ctrl.charge_current_target_a == 65

    @pytest.mark.asyncio
    async def test_grid_charging_live_max_above_configured_cap_does_not_loosen_it(self, make_config, ws):
        """A live max ABOVE the configured cap must never let the effective
        ceiling exceed what the user configured."""
        ws.attributes["number.charge_current"] = {"min": 0, "max": 350, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1_000_000.0)
        assert ctrl.charge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_pv_charging_sets_expected_targets(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.PV_CHARGING)
        assert ctrl.charge_current_target_a == 185
        assert ctrl.discharge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_export_surplus_blocks_charge_only(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.EXPORT_SURPLUS)
        assert ctrl.charge_current_target_a == 0
        assert ctrl.discharge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_protect_battery_blocks_discharge_only(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.PROTECT_BATTERY)
        assert ctrl.charge_current_target_a == 185
        assert ctrl.discharge_current_target_a == 0

    @pytest.mark.asyncio
    async def test_idle_allows_both_directions(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.IDLE)
        assert ctrl.charge_current_target_a == 185
        assert ctrl.discharge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_disabled_battery_control_is_a_noop(self, make_config, ws):
        cfg = make_config(battery_control_enabled=False)
        ctrl = InverterController(cfg, supervisor_token="t", ws_client=ws)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.charge_current_target_a is None

    @pytest.mark.asyncio
    async def test_sim_never_calls_the_real_service(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        with aioresponses() as m:
            # No mock registered -> any real HTTP call raises aioresponses.ClientConnectionError
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        # Reaching here without an exception proves no HTTP call was made.


class TestWriteConfirmation:
    @pytest.mark.asyncio
    async def test_confirmed_once_ha_state_matches(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 185.0
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "on"}
        with aioresponses() as m:
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.write_unconfirmed == 0
        assert ctrl.charge_current_limit_a == 185

    @pytest.mark.asyncio
    async def test_unconfirmed_when_ha_state_does_not_match_yet(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 0.0   # not yet applied
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "off"}
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=200)
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.write_unconfirmed > 0
        assert ctrl.charge_current_limit_a is None   # not confirmed

    @pytest.mark.asyncio
    async def test_http_rejection_counts_as_write_error(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 0.0
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "off"}
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=400)
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.write_errors == 1

    @pytest.mark.asyncio
    async def test_no_resend_within_confirm_timeout(self, make_config, ws):
        """A second apply_mode() call for the same target must not re-POST
        while still inside INVERTER_WRITE_CONFIRM_TIMEOUT_SEC of the last try."""
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 0.0
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "off"}
        call_count = {"n": 0}

        def _cb(url, **kwargs):
            call_count["n"] += 1

        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=200, callback=_cb)
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)   # same target, immediately again
        assert call_count["n"] == 1

    @pytest.mark.asyncio
    async def test_401_falls_back_to_long_lived_token(self, make_config, ws):
        cfg_kwargs = dict(long_lived_token="")
        ctrl = InverterController(
            make_config(battery_control_enabled=True, battery_control_simulation=False,
                        grid_charge_switch_entity="switch.grid_charge",
                        inverter_charge_current_entity="", battery_discharging_current_entity=""),
            supervisor_token="sup-token", long_lived_token="llt-token", ws_client=ws,
        )
        ws.state_cache["switch.grid_charge"] = {"state": "off"}
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=401)
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=200)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl._active_token == "llt-token"

    @pytest.mark.asyncio
    async def test_blank_entity_is_skipped(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False, grid_charge_switch_entity="")
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)   # must not raise


class TestErrorWindowAndPendingState:
    def test_write_errors_prunes_old_entries(self, make_config, ws):
        import time
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ctrl._write_error_times.append(time.monotonic() - 999999)   # ancient
        assert ctrl.write_errors == 0

    def test_longest_pending_sec_zero_when_all_confirmed(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        assert ctrl.longest_pending_sec == 0.0

    def test_stuck_channel_labels_empty_when_confirmed(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        assert ctrl.stuck_channel_labels == []

    @pytest.mark.asyncio
    async def test_pop_write_events_returns_and_clears(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 185.0
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "on"}
        with aioresponses() as m:
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        events = ctrl.pop_write_events()
        assert len(events) >= 1
        assert ctrl.pop_write_events() == []   # cleared


class TestRestoreSafeDefaults:
    @pytest.mark.asyncio
    async def test_restore_sets_safe_state(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING)   # leaves discharge blocked
        await ctrl.restore_safe_defaults()
        assert ctrl.discharge_current_target_a == 185   # unblocked again

    @pytest.mark.asyncio
    async def test_restore_noop_when_battery_control_disabled(self, make_config, ws):
        cfg = make_config(battery_control_enabled=False)
        ctrl = InverterController(cfg, supervisor_token="t", ws_client=ws)
        await ctrl.restore_safe_defaults()   # must not raise

    @pytest.mark.asyncio
    async def test_restore_logs_unconfirmed_channel_as_shutdown_event(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 0.0   # never confirms
        ws.values["number.discharge_current"] = 0.0
        ws.state_cache["switch.grid_charge"] = {"state": "off"}
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/switch/turn_on", status=200)
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
            ctrl.pop_write_events()   # clear the "sent" events first
            await ctrl.restore_safe_defaults()
        events = ctrl.pop_write_events()
        assert any(e["outcome"] == "unconfirmed_at_shutdown" for e in events)
