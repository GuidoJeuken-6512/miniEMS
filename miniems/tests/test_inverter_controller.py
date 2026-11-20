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
        inverter_grid_charge_current_entity="number.grid_charge_current",
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
        """V1 (docs/technical/energiefahrplan.md): EMSController can request a
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
    async def test_pv_charging_respects_a_live_charge_max_below_configured(self, make_config, ws):
        """I5b: live min/max/step clamping now applies to every mode's
        charge target, not only GRID_CHARGING's - a BMS-imposed charge
        limit must be respected during PV_CHARGING too."""
        ws.attributes["number.charge_current"] = {"min": 0, "max": 65, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.PV_CHARGING)
        assert ctrl.charge_current_target_a == 65

    @pytest.mark.asyncio
    async def test_pv_charging_respects_a_live_discharge_max_below_configured(self, make_config, ws):
        """I5b: the discharge side gets the same treatment, via its own
        LiveLimitsCache - independent of the charge entity's."""
        ws.attributes["number.discharge_current"] = {"min": 0, "max": 65, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.PV_CHARGING)
        assert ctrl.discharge_current_target_a == 65
        assert ctrl.charge_current_target_a == 185   # charge side unaffected

    @pytest.mark.asyncio
    async def test_discharge_live_max_above_configured_cap_does_not_loosen_it(self, make_config, ws):
        ws.attributes["number.discharge_current"] = {"min": 0, "max": 350, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.IDLE)
        assert ctrl.discharge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_export_surplus_hold_is_clamped_too(self, make_config, ws):
        """export_hold_charge_current_a (typically 0, "block charging") also
        goes through the same clamp - a live min above 0 would raise it, but
        the common case (min=0) leaves it unchanged."""
        ws.attributes["number.charge_current"] = {"min": 0, "max": 65, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True, export_hold_charge_current_a=0)
        await ctrl.apply_mode(EMSMode.EXPORT_SURPLUS)
        assert ctrl.charge_current_target_a == 0

    @pytest.mark.asyncio
    async def test_discharge_limits_cache_is_independent_of_charge_cache(self, make_config, ws):
        """A momentarily unavailable discharge max must not be affected by
        (or affect) the charge entity's cached ceiling."""
        ws.attributes["number.charge_current"] = {"min": 0, "max": 65, "step": 1}
        ws.attributes["number.discharge_current"] = {"min": 0, "max": 100, "step": 1}
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.IDLE)
        assert ctrl.charge_current_target_a == 65
        assert ctrl.discharge_current_target_a == 100
        # Now the discharge max goes briefly unavailable - must hold at 100,
        # not fall back to the 185 configured default.
        ws.attributes["number.discharge_current"] = {"min": 0, "max": None, "step": 1}
        await ctrl.apply_mode(EMSMode.IDLE)
        assert ctrl.discharge_current_target_a == 100
        assert ctrl.charge_current_target_a == 65   # unaffected by the discharge cache

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
        ws.values["number.grid_charge_current"] = 185.0
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
        ctrl._writer.error_times.append(time.monotonic() - 999999)   # ancient
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



class TestGridChargeCurrent:
    """Bugfix: the Deye's separate grid-charge limit (Battery Grid Charging
    Current) stayed at 0 A, so GRID_CHARGING charged nothing – the smaller of
    it and the general charge current applies."""

    @pytest.mark.asyncio
    async def test_grid_charging_writes_the_grid_limit(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        ws.values["number.charge_current"] = 185.0
        ws.values["number.discharge_current"] = 0.0
        ws.values["number.grid_charge_current"] = 0.0          # the stuck state
        ws.state_cache["switch.grid_charge"] = {"state": "on"}
        with aioresponses() as m:
            m.post(f"{HA_SERVICES_URL}/number/set_value", status=200, repeat=True)
            await ctrl.apply_mode(EMSMode.GRID_CHARGING)
            sent = [kw["json"] for (method, url), calls in m.requests.items()
                    for kw in [c.kwargs for c in calls]]
        assert {"entity_id": "number.grid_charge_current", "value": 185} in sent
        assert ctrl.grid_charge_current_target_a == 185
        assert ctrl.grid_charge_current_limit_a is None        # not confirmed yet
        assert "Grid charge current (number.grid_charge_current)" in ctrl.stuck_channel_labels

    @pytest.mark.asyncio
    async def test_small_changes_stay_inside_the_deadband(self, make_config, ws):
        """v4.2.4: 82 A, then 84 A, 86 A … – nothing new is written below the
        deadband (max 3 A, 5 %); a real change still is."""
        ctrl = make_ctrl(make_config, ws, simulation=True)
        ws.values["sensor.deye8k_battery_voltage"] = 50.0
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=4100)   # 82 A
        assert ctrl.charge_current_target_a == 82
        for watts in (4200, 4250, 4300):                                          # 84–86 A
            await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=watts)
            assert ctrl.charge_current_target_a == 82
            assert ctrl.grid_charge_current_target_a == 82
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=5000)   # 100 A
        assert ctrl.charge_current_target_a == 100

    @pytest.mark.asyncio
    async def test_stretched_power_goes_to_both_limits(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        ws.values["sensor.deye8k_battery_voltage"] = 50.0
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=2500)
        assert ctrl.charge_current_target_a == 50
        assert ctrl.grid_charge_current_target_a == 50

    @pytest.mark.asyncio
    @pytest.mark.parametrize("mode", [EMSMode.IDLE, EMSMode.PV_CHARGING, EMSMode.EXPORT_SURPLUS,
                                      EMSMode.HOLD_FOR_PEAK, EMSMode.PROTECT_BATTERY])
    async def test_other_modes_set_the_resting_value(self, make_config, ws, mode):
        ctrl = make_ctrl(make_config, ws, simulation=True)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1000)
        await ctrl.apply_mode(mode)
        assert ctrl.grid_charge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_grid_charging_ceiling(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True, battery_max_grid_charge_current_a=100)
        await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.charge_current_target_a == 185
        assert ctrl.grid_charge_current_target_a == 100

    @pytest.mark.asyncio
    async def test_restore_and_failsafe_set_185(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=False)
        actions = ctrl.failsafe_actions()
        assert {"service": "number.set_value",
                "data": {"entity_id": "number.grid_charge_current", "value": 185}} in actions
        sim = make_ctrl(make_config, ws, simulation=True)
        await sim.apply_mode(EMSMode.GRID_CHARGING, grid_charge_power_w=1000)
        await sim.restore_safe_defaults()
        assert sim.grid_charge_current_target_a == 185

    @pytest.mark.asyncio
    async def test_empty_entity_writes_nothing(self, make_config, ws):
        ctrl = make_ctrl(make_config, ws, simulation=True, inverter_grid_charge_current_entity="")
        await ctrl.apply_mode(EMSMode.GRID_CHARGING)
        assert ctrl.grid_charge_current_target_a is None

    def test_default_points_at_the_deye_entity(self, make_config):
        cfg = make_config()
        assert cfg.inverter_grid_charge_current_entity == "number.deye8k_battery_grid_charging_current"
        assert cfg.battery_max_grid_charge_current_a == 185
        assert cfg.inverter_grid_charge_current_entity in cfg.monitored_entities
