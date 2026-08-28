"""EMSController.update(): the full per-tick orchestration."""
from datetime import datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

from const import EMSMode
from consumption_model import Prediction
from ems_controller import EMSController
from solcast_client import SolcastClient

_NIEDRIG_0206_TIMESLOTS = {
    "timeslots": [
        {"name": "NIEDRIG", "rate": "27.4414",
         "activation_rules": [{"from_time": "02:00:00", "to_time": "06:00:00"}]},
        {"name": "STANDARD", "rate": "34.4400",
         "activation_rules": [{"from_time": "06:00:00", "to_time": "02:00:00"}]},
    ],
}


class FakeOptimizer:
    def __init__(self) -> None:
        self.startup_warnings: list[str] = []
        self.recorded_ticks: list[dict] = []
        self.flushed = 0

    def is_cheap_rate(self, price):
        return price is not None and price < 0.10

    def price_tier(self, price):
        return "low" if price is not None and price < 0.10 else "high"

    def today_efficiency(self):
        return 0.9

    async def avg_discharge_tariff_eur_kwh(self):
        return 0.35

    def today_load_total_kwh(self):
        return 5.0

    def record_tick(self, **kwargs):
        self.recorded_ticks.append(kwargs)

    async def flush_to_db(self):
        self.flushed += 1

    def get_startup_warnings(self):
        return list(self.startup_warnings)

    async def summary_with_db(self):
        return {"today_grid_cost_eur": 1.23, "today_grid_charge_cost_eur": 0.5,
                "today_load_total_kwh": 10.0}


class FakeInverter:
    def __init__(self) -> None:
        self.applied_modes: list[EMSMode] = []
        self.last_grid_charge_power_w = None
        self.simulation = True
        self.write_errors = 0
        self.write_unconfirmed = 0
        self.longest_pending_sec = 0.0
        self.stuck_channel_labels: list[str] = []
        self.charge_current_limit_a = 100
        self.discharge_current_limit_a = 100
        self.charge_current_target_a = 100
        self.discharge_current_target_a = 100
        self._events: list[dict] = [
            {"channel": "test", "target": 1, "outcome": "confirmed", "latency_sec": 1.0},
        ]

    async def apply_mode(self, mode, grid_charge_power_w=None):
        self.applied_modes.append(mode)
        self.last_grid_charge_power_w = grid_charge_power_w

    def pop_write_events(self):
        events, self._events = self._events, []
        return events


class FakeModel:
    def __init__(self, prediction=None, remaining=3.0) -> None:
        self._prediction = prediction or Prediction(
            predicted_load_kwh=10.0, predicted_pv_kwh=8.0, confidence="high", source="historical",
        )
        self._remaining = remaining

    async def predict(self, bat_soc):
        return self._prediction

    async def remaining_load_kwh(self, load_so_far_kwh):
        return self._remaining


def build_controller(make_config, fake_ws, with_inverter=True, with_model=True, with_solcast=True, **overrides):
    defaults = dict(
        pv_power_entity="sensor.pv", load_power_entity="sensor.load",
        grid_power_entity="sensor.grid", battery_soc_entity="sensor.soc",
        battery_power_entity="sensor.batp", electricity_price_entity="sensor.price",
        battery_capacity_entity="", battery_state_entity="",
        feed_in_energy_entity="", grid_import_energy_entity="", load_consumption_entity="",
        grid_import_total_entity="", feed_in_total_entity="", load_consumption_total_entity="",
        battery_charge_entity="", battery_discharge_entity="",
        today_production_entity="", today_losses_entity="",
    )
    defaults.update(overrides)
    cfg = make_config(**defaults)
    optimizer = FakeOptimizer()
    inverter = FakeInverter() if with_inverter else None
    model = FakeModel() if with_model else None
    ctrl = EMSController(cfg, fake_ws, optimizer, inverter=inverter, consumption_model=model)
    if with_solcast:
        ctrl._solcast = SolcastClient(cfg, fake_ws)
    fake_ws.stale[cfg.battery_power_entity] = False
    fake_ws.stale[cfg.pv_power_entity] = False
    fake_ws.stale[cfg.load_power_entity] = False
    return ctrl, optimizer, inverter, model


@pytest.mark.asyncio
async def test_update_returns_core_dashboard_fields(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 1000.0, "sensor.load": 500.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": -500.0, "sensor.price": 0.30,
    })
    result = await ctrl.update()
    assert result["pv_power_w"] == 1000.0
    assert result["battery_soc_pct"] == 50.0
    assert result["electricity_price_eur"] == 0.30
    assert "mode" in result
    assert result["battery_kwh_freetochange"] is not None
    assert result["remaining_load_kwh"] == 3.0


@pytest.mark.asyncio
async def test_update_applies_inverter_mode(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    await ctrl.update()
    assert len(inverter.applied_modes) == 1


@pytest.mark.asyncio
async def test_update_wires_stretched_grid_charge_power_to_inverter(make_config, fake_ws):
    """V1 (docs/roadmap/energiefahrplan.md): end-to-end, update() must pass a
    stretched (not full-blast) Watts target to the inverter when a tariff
    calendar is available. Since v2.4.0 this is Watts, computed without
    reading battery_voltage at all (that conversion now happens inside
    InverterController, see device_control.py)."""
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws, mode_dwell_sec=0,
        cheap_rate_threshold_eur=0.5, grid_charge_dark_start_hour=21,
        grid_charge_dark_end_hour=6, battery_max_charge_current_a=100,
        battery_max_soc=90,
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.05,
    })
    fake_ws.attributes["sensor.price"] = _NIEDRIG_0206_TIMESLOTS
    # A naive freeze: datetime.now().astimezone() attaches the local tzinfo
    # to this exact wall-clock time without any UTC conversion, so the
    # resulting hour (3) is deterministic regardless of the host's timezone.
    with freeze_time("2026-08-15 03:00:00"):
        result = await ctrl.update()
    assert result["mode"] == EMSMode.GRID_CHARGING.value
    # bat_kwh_free = (90-50)/100*10 = 4.0 kWh; window 03:00-06:00, 80% margin
    # -> 2.4h; P_soll = 4.0/2.4 = 1.6667 kW = 1666.67 W.
    assert inverter.last_grid_charge_power_w == pytest.approx(1666.67, abs=0.5)


@pytest.mark.asyncio
async def test_update_persists_write_confirm_events_to_log(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    await ctrl.update()
    write_entries = [e for e in ctrl._event_log.to_list(include_write_confirm=True)
                      if e["entry_type"] == "write_confirm"]
    assert len(write_entries) == 1


@pytest.mark.asyncio
async def test_update_logs_mode_change(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws, mode_dwell_sec=0)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 5.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    }, )
    ctrl._cfg.battery_min_soc = 15   # forces PROTECT_BATTERY, a change from initial IDLE
    await ctrl.update()
    entries = ctrl._event_log.to_list()
    assert any(e["mode"] == EMSMode.PROTECT_BATTERY.value for e in entries)


@pytest.mark.asyncio
async def test_update_logs_price_change_after_first_known_price(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    await ctrl.update()   # establishes _last_price = 0.30, no prior price to compare
    fake_ws.values["sensor.price"] = 0.40
    await ctrl.update()
    price_entries = [e for e in ctrl._event_log.to_list() if e["entry_type"] == "price_change"]
    assert len(price_entries) == 1
    assert price_entries[0]["price_eur_kwh"] == 0.40


@pytest.mark.asyncio
async def test_update_no_price_change_entry_when_price_unchanged(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    await ctrl.update()
    await ctrl.update()
    price_entries = [e for e in ctrl._event_log.to_list() if e["entry_type"] == "price_change"]
    assert price_entries == []


@pytest.mark.asyncio
async def test_update_includes_prediction_fields(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    result = await ctrl.update()
    assert result["predicted_load_kwh"] == 10.0
    assert result["predicted_pv_kwh"] == 8.0
    assert result["prediction_confidence"] == "high"


@pytest.mark.asyncio
async def test_update_includes_solcast_fields_when_configured(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws,
        solcast_remaining_today_entity="sensor.remaining",
        solcast_today_entity="sensor.today", solcast_tomorrow_entity="sensor.tomorrow",
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        "sensor.remaining": 2.0, "sensor.today": 5.0, "sensor.tomorrow": 6.0,
    })
    result = await ctrl.update()
    assert result["solcast_remaining_today_kwh"] == 2.0
    assert result["solcast_today_kwh"] == 5.0
    assert result["solcast_tomorrow_kwh"] == 6.0


@pytest.mark.asyncio
async def test_update_includes_inverter_write_status_fields(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    result = await ctrl.update()
    assert result["inverter_write_status"] == "ok"
    assert result["charge_current_limit_a"] == 100


@pytest.mark.asyncio
async def test_update_works_without_inverter_model_or_solcast(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws, with_inverter=False, with_model=False, with_solcast=False,
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    result = await ctrl.update()
    assert "inverter_write_status" not in result
    assert "predicted_load_kwh" not in result
    assert result["remaining_load_kwh"] is None


@pytest.mark.asyncio
async def test_update_propagates_startup_warnings(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    optimizer.startup_warnings.append("Data gap detected: 999s")
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    result = await ctrl.update()
    assert any("Data gap" in w for w in result["warnings"])


@pytest.mark.asyncio
async def test_update_uses_battery_capacity_sensor_when_plausible(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws, battery_capacity_entity="sensor.cap", battery_capacity_kwh=10.0,
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        "sensor.cap": 12.0,
    })
    result = await ctrl.update()
    assert result["battery_capacity_kwh"] == 12.0


@pytest.mark.asyncio
async def test_update_rejects_implausible_capacity_for_control_math(make_config, fake_ws):
    """The plausibility band only guards the *internal* control-math capacity
    (BatteryModel.capacity_kwh, which feeds free/useable kWh) – the
    dashboard's raw `battery_capacity_kwh` result field mirrors the sensor
    unfiltered, so it is asserted separately below."""
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws, battery_capacity_entity="sensor.cap", battery_capacity_kwh=10.0,
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        "sensor.cap": 999.0,   # e.g. an Ah reading, way outside plausibility band
    })
    await ctrl.update()
    assert ctrl._battery_model.capacity_kwh == 10.0   # fell back to config, not the sensor


@pytest.mark.asyncio
async def test_update_capacity_result_field_mirrors_sensor_unfiltered(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(
        make_config, fake_ws, battery_capacity_entity="sensor.cap", battery_capacity_kwh=10.0,
    )
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        "sensor.cap": 999.0,
    })
    result = await ctrl.update()
    assert result["battery_capacity_kwh"] == 999.0


class TestUpdateEnergyPlanWiring:
    """The Energiefahrplan section of update() – see energy_plan.py."""

    def _build(self, make_config, fake_ws, **overrides):
        return build_controller(make_config, fake_ws, **overrides)

    @pytest.mark.asyncio
    async def test_result_includes_energy_plan_after_first_tick(self, make_config, fake_ws):
        ctrl, optimizer, inverter, model = self._build(make_config, fake_ws)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        result = await ctrl.update()
        assert "energy_plan" in result
        assert "deficit_kwh" in result["energy_plan"]
        assert "windows" in result["energy_plan"]

    @pytest.mark.asyncio
    async def test_plan_not_recomputed_within_the_hour(self, make_config, fake_ws):
        ctrl, optimizer, inverter, model = self._build(make_config, fake_ws)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        await ctrl.update()
        first = ctrl._energy_plan
        fake_ws.values["sensor.soc"] = 10.0   # would change the deficit if recomputed
        await ctrl.update()
        assert ctrl._energy_plan is first   # same object -> not recomputed

    @pytest.mark.asyncio
    async def test_plan_recomputes_after_the_interval_elapses(self, make_config, fake_ws):
        ctrl, optimizer, inverter, model = self._build(make_config, fake_ws)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        await ctrl.update()
        first = ctrl._energy_plan
        ctrl._last_energy_plan_at -= timedelta(hours=2)   # simulate elapsed time
        await ctrl.update()
        assert ctrl._energy_plan is not first


@pytest.mark.asyncio
async def test_update_flushes_optimizer_to_db(make_config, fake_ws):
    ctrl, optimizer, inverter, model = build_controller(make_config, fake_ws)
    fake_ws.values.update({
        "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
        "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
    })
    await ctrl.update()
    assert optimizer.flushed == 1
    assert len(optimizer.recorded_ticks) == 1


class FakeCapability:
    """Double for BatteryCapabilityTracker – records what update() sends it."""

    def __init__(self, learned_kw: float | None = None) -> None:
        self.learned_kw = learned_kw
        self.recorded: list[tuple] = []
        self.flushed = 0
        self.rolled_over: list = []

    def record_tick(self, mode, soc, battery_power_w):
        self.recorded.append((mode, soc, battery_power_w))

    async def flush_to_db(self):
        self.flushed += 1

    async def rollover_day(self, ended_day):
        self.rolled_over.append(ended_day)

    async def charge_power_kw(self, soc, fallback_kw):
        return self.learned_kw if self.learned_kw is not None else fallback_kw


class TestUpdateBatteryCapabilityWiring:
    """update() feeds the SoC-bucketed learned-charge-power tracker every
    tick, against the *committed* mode – see battery_capability.py."""

    def _build(self, make_config, fake_ws, capability, **overrides):
        defaults = dict(
            pv_power_entity="sensor.pv", load_power_entity="sensor.load",
            grid_power_entity="sensor.grid", battery_soc_entity="sensor.soc",
            battery_power_entity="sensor.batp", electricity_price_entity="sensor.price",
            battery_capacity_entity="", battery_state_entity="",
            feed_in_energy_entity="", grid_import_energy_entity="", load_consumption_entity="",
            grid_import_total_entity="", feed_in_total_entity="", load_consumption_total_entity="",
            battery_charge_entity="", battery_discharge_entity="",
            today_production_entity="", today_losses_entity="",
        )
        defaults.update(overrides)
        cfg = make_config(**defaults)
        optimizer = FakeOptimizer()
        ctrl = EMSController(cfg, fake_ws, optimizer, capability=capability)
        ctrl._solcast = SolcastClient(cfg, fake_ws)
        fake_ws.stale[cfg.battery_power_entity] = False
        fake_ws.stale[cfg.pv_power_entity] = False
        fake_ws.stale[cfg.load_power_entity] = False
        return ctrl

    @pytest.mark.asyncio
    async def test_records_tick_with_committed_mode_and_soc(self, make_config, fake_ws):
        capability = FakeCapability()
        ctrl = self._build(make_config, fake_ws, capability)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": -3000.0, "sensor.price": 0.30,
        })
        result = await ctrl.update()
        assert len(capability.recorded) == 1
        mode, soc, power_w = capability.recorded[0]
        assert mode.value == result["mode"]
        assert soc == 50.0
        assert power_w == -3000.0

    @pytest.mark.asyncio
    async def test_flushes_capability_every_tick(self, make_config, fake_ws):
        capability = FakeCapability()
        ctrl = self._build(make_config, fake_ws, capability)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        await ctrl.update()
        assert capability.flushed == 1

    @pytest.mark.asyncio
    async def test_no_rollover_on_first_tick(self, make_config, fake_ws):
        """No previous day to roll over yet on the very first tick after startup."""
        capability = FakeCapability()
        ctrl = self._build(make_config, fake_ws, capability)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        await ctrl.update()
        assert capability.rolled_over == []

    @pytest.mark.asyncio
    async def test_rolls_over_once_the_date_changes(self, make_config, fake_ws, monkeypatch):
        import ems_controller as ems_controller_module
        from datetime import date, timedelta

        capability = FakeCapability()
        ctrl = self._build(make_config, fake_ws, capability)
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
        })
        yesterday = date.today() - timedelta(days=1)

        class _FakeDate(date):
            @classmethod
            def today(cls):
                return yesterday

        monkeypatch.setattr(ems_controller_module, "date", _FakeDate)
        await ctrl.update()   # runs "yesterday" -> just records _last_capability_rollover
        assert capability.rolled_over == []

        monkeypatch.setattr(ems_controller_module, "date", date)   # back to the real today
        await ctrl.update()
        assert capability.rolled_over == [yesterday]

    @pytest.mark.asyncio
    async def test_learned_charge_kw_is_refreshed_from_capability(self, make_config, fake_ws):
        capability = FakeCapability(learned_kw=3.3)
        ctrl = self._build(make_config, fake_ws, capability, battery_voltage_entity="sensor.voltage")
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30,
            "sensor.voltage": 50.0,
        })
        await ctrl.update()
        assert ctrl._learned_charge_kw == 3.3
        assert ctrl._charge_power_kw() == 3.3

    @pytest.mark.asyncio
    async def test_learned_charge_kw_none_without_soc(self, make_config, fake_ws):
        capability = FakeCapability(learned_kw=3.3)
        ctrl = self._build(make_config, fake_ws, capability, battery_voltage_entity="sensor.voltage")
        fake_ws.values.update({
            "sensor.pv": 0.0, "sensor.load": 0.0, "sensor.grid": 0.0,
            "sensor.soc": None, "sensor.batp": 0.0, "sensor.price": 0.30,
            "sensor.voltage": 50.0,
        })
        await ctrl.update()
        assert ctrl._learned_charge_kw is None
