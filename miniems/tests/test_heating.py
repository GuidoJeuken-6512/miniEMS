"""B6/B7 – heating via the flow-line offset, room model, pre-heat planner; D1."""
import math
from datetime import datetime, timedelta, timezone

import pytest

import store as store_module
from heat_pump_device import HeatPumpDevice
from heating_controller import (
    LOCK_DOWN_MIN, LOCK_UP_MIN, HeatingController, HeatingInputs, PriceSlot,
)
from heating_model import (
    MIN_COOL_HOURS, HeatSlot, HourAccumulator, RoomModel, fit_room_model, plan_heating,
)
from store import EnergyStore

TZ = timezone(timedelta(hours=1))
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)
OFFSET = "number.hc1_offset"


class FakeCaller:
    def __init__(self):
        self.calls = []
        self.active_token = "t"

    async def call(self, domain, service, data):
        self.calls.append((domain, service, data))
        return True


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


def heating(make_config, fake_ws, store=None, caller=None, **kw):
    opts = dict(heating_offset_entity=OFFSET, heating_control_enabled=True,
                heating_control_simulation=False, heating_room_temp_entity="")
    opts.update(kw)
    cfg = make_config(**opts)
    return HeatingController(cfg, fake_ws, store, caller or FakeCaller())


def inputs(now, tier, price=0.30, slots=(), outdoor=5.0, **kw):
    return HeatingInputs(now=now, price_eur_kwh=price, tier=tier, slots=list(slots),
                         outdoor_c=outdoor, **kw)


async def started(ctrl, now=T0):
    await ctrl.update(inputs(now - timedelta(minutes=1), "medium"))   # startup tick: 0 K
    return ctrl


class TestOffsetRules:
    @pytest.mark.asyncio
    async def test_startup_writes_zero(self, make_config, fake_ws):
        caller = FakeCaller()
        ctrl = heating(make_config, fake_ws, caller=caller)
        await ctrl.update(inputs(T0, "low", price=0.10))
        assert ctrl.offset_k == 0.0
        assert ("number", "set_value", {"entity_id": OFFSET, "value": 0.0}) in caller.calls

    @pytest.mark.asyncio
    async def test_high_price_lowers(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        await ctrl.update(inputs(T0, "high", price=0.40))
        assert ctrl.offset_k == -2.0

    @pytest.mark.asyncio
    async def test_low_price_raises_only_when_it_pays(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        peak = PriceSlot(T0 + timedelta(hours=3), T0 + timedelta(hours=4), 0.40, "high")
        await ctrl.update(inputs(T0, "low", price=0.27, slots=[peak]))
        assert ctrl.offset_k == 2.0
        ctrl2 = await started(heating(make_config, fake_ws))
        flat = PriceSlot(T0 + timedelta(hours=3), T0 + timedelta(hours=4), 0.275, "medium")
        await ctrl2.update(inputs(T0, "low", price=0.27, slots=[flat]))
        assert ctrl2.offset_k == 0.0
        assert "not worth it" in ctrl2.status(T0)["reason"]

    @pytest.mark.asyncio
    async def test_preload_before_high(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        peak = PriceSlot(T0 + timedelta(hours=5), T0 + timedelta(hours=6), 0.45, "high")
        await ctrl.update(inputs(T0, "medium", price=0.34, slots=[peak]))
        assert ctrl.offset_k == 1.0

    @pytest.mark.asyncio
    async def test_no_heating_above_the_limit(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        await ctrl.update(inputs(T0, "high", price=0.40, outdoor=18.0))
        assert ctrl.offset_k == 0.0

    @pytest.mark.asyncio
    @pytest.mark.parametrize("outdoor,expected", [(-8.0, -1.0), (-16.0, 0.0)])
    async def test_cold_limits_the_lowering(self, make_config, fake_ws, outdoor, expected):
        ctrl = await started(heating(make_config, fake_ws))
        await ctrl.update(inputs(T0, "high", price=0.40, outdoor=outdoor))
        assert ctrl.offset_k == expected

    @pytest.mark.asyncio
    async def test_at_most_three_hours_lowered(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        for minutes in range(0, 181, 30):
            await ctrl.update(inputs(T0 + timedelta(minutes=minutes), "high", price=0.40))
        assert ctrl.status(T0)["wanted_k"] == 0.0
        # Back to 0 is a raise: held by the lowering's lock until it ends.

    @pytest.mark.asyncio
    async def test_compressor_lock(self, make_config, fake_ws):
        ctrl = await started(heating(make_config, fake_ws))
        await ctrl.update(inputs(T0, "high", price=0.40))
        assert ctrl.offset_k == -2.0
        await ctrl.update(inputs(T0 + timedelta(minutes=10), "medium", price=0.34))
        assert ctrl.offset_k == -2.0                       # locked
        assert "compressor lock" in ctrl.status(T0)["reason"]
        await ctrl.update(inputs(T0 + timedelta(minutes=LOCK_DOWN_MIN + 1), "medium", price=0.34))
        assert ctrl.offset_k == 0.0

    @pytest.mark.asyncio
    async def test_lock_survives_a_restart(self, make_config, fake_ws, db):
        ctrl = await started(heating(make_config, fake_ws, store=db))
        await ctrl.update(inputs(T0, "high", price=0.40))
        fresh = heating(make_config, fake_ws, store=db)
        await fresh.update(inputs(T0 + timedelta(minutes=5), "medium"))   # startup: 0 K
        await fresh.update(inputs(T0 + timedelta(minutes=10), "high", price=0.40))
        # The raise to 0 at startup does not count; the lock from before still holds.
        assert fresh.offset_k == 0.0
        assert fresh.status(T0 + timedelta(minutes=10))["locked_until"] is not None
        later = T0 + timedelta(minutes=LOCK_DOWN_MIN + 1)
        await fresh.update(inputs(later, "high", price=0.40))
        assert fresh.offset_k == -2.0

    @pytest.mark.asyncio
    async def test_room_near_comfort_stops_lowering(self, make_config, fake_ws):
        ctrl = heating(make_config, fake_ws, heating_comfort_min_c=20.5)
        ctrl._cfg.heating_room_temp_entity = "sensor.room"
        fake_ws.values["sensor.room"] = 20.6
        await started(ctrl)
        await ctrl.update(inputs(T0, "high", price=0.40))
        assert ctrl.offset_k == 0.0

    @pytest.mark.asyncio
    async def test_cold_alarm_after_30_minutes(self, make_config, fake_ws):
        ctrl = heating(make_config, fake_ws, heating_comfort_min_c=20.5)
        ctrl._cfg.heating_room_temp_entity = "sensor.room"
        fake_ws.values["sensor.room"] = 19.8
        await started(ctrl)
        await ctrl.update(inputs(T0, "medium", price=0.34))
        assert ctrl.offset_k == 0.0                        # a window open briefly
        await ctrl.update(inputs(T0 + timedelta(minutes=31), "medium", price=0.34))
        assert ctrl.offset_k == 2.0

    @pytest.mark.asyncio
    async def test_heating_rod_halves_the_lowering(self, make_config, fake_ws):
        ctrl = heating(make_config, fake_ws, heat_pump_power_entity="sensor.hp_w",
                       heating_compressor_max_w=2000)
        fake_ws.values["sensor.hp_w"] = 5000
        await started(ctrl, T0 - timedelta(minutes=20))
        await ctrl.update(inputs(T0 - timedelta(minutes=15), "medium", price=0.34))
        await ctrl.update(inputs(T0, "medium", price=0.34))
        assert ctrl.status(T0)["heating_rod_today"]
        await ctrl.update(inputs(T0 + timedelta(minutes=1), "high", price=0.40))
        assert ctrl.offset_k == -1.0

    @pytest.mark.asyncio
    async def test_failsafe_and_simulation(self, make_config, fake_ws):
        ctrl = heating(make_config, fake_ws)
        assert ctrl.failsafe_actions() == [
            {"service": "number.set_value", "data": {"entity_id": OFFSET, "value": 0.0}}]
        caller = FakeCaller()
        sim = heating(make_config, fake_ws, caller=caller, heating_control_simulation=True)
        await sim.update(inputs(T0, "high"))
        assert caller.calls == [] and sim.failsafe_actions() == []


class TestDevice:
    def test_service_template_unchanged(self, make_config, fake_ws):
        """D1 regression: the Lambda write is byte-identical to v4.0.0."""
        dev = HeatPumpDevice(make_config(), fake_ws)
        call = dev.hw_setpoint_call(50.0)
        assert call.name == "lambda_heat_pumps.write_modbus_register"
        assert call.data == {"register_address": 2050, "value": 500}

    def test_number_entity_path(self, make_config, fake_ws):
        dev = HeatPumpDevice(make_config(hw_setpoint_entity="number.boiler"), fake_ws)
        call = dev.hw_setpoint_call(48.04)
        assert (call.name, call.data) == ("number.set_value",
                                          {"entity_id": "number.boiler", "value": 48.0})

    def test_unset_service(self, make_config, fake_ws):
        dev = HeatPumpDevice(make_config(hw_write_service=""), fake_ws)
        assert dev.hw_setpoint_call(50.0) is None
        assert dev.hw_setpoint_call(50.0, simulation=True).domain == "<hw_write_service unset>"

    def test_compressor_states(self, make_config, fake_ws):
        dev = HeatPumpDevice(make_config(hw_compressor_entity="sensor.c"), fake_ws)
        fake_ws.values["sensor.c"] = 40
        assert dev.compressor_running() is True
        fake_ws.values["sensor.c"] = None
        fake_ws.state_cache["sensor.c"] = {"state": "DHW"}
        assert dev.compressor_running() is True
        fake_ws.state_cache["sensor.c"] = {"state": "STBY"}
        assert dev.compressor_running() is False

    def test_missing_capabilities(self, make_config, fake_ws):
        dev = HeatPumpDevice(make_config(heating_offset_entity="", heating_room_temp_entity=""),
                             fake_ws)
        assert dev.missing("flow_offset", "room_temp") == ["flow_offset", "room_temp"]


# ----------------------------------------------------------------------
# B7
# ----------------------------------------------------------------------

def synthetic_history(hours=24 * 8, a=0.05, c=0.9, d=0.02, noise=0.0):
    """Hourly rows of a house that cools with a and heats with c + d·T_out."""
    rows = []
    t_in = 21.0
    start = datetime(2026, 11, 1, 0, 0, tzinfo=TZ)
    for h in range(hours):
        moment = start + timedelta(hours=h)
        t_out = 5 + 4 * math.sin(h / 24 * 2 * math.pi)
        heating = 1.0 if (h // 3) % 2 == 0 else 0.0
        rows.append({"hour_start": moment.isoformat(), "room_temp_c": round(t_in, 3),
                     "outdoor_temp_c": t_out, "heating_frac": heating, "pv_kwh": 0.0})
        loss = a * (t_in - t_out)
        gain = (c + d * t_out) if heating else 0.0
        t_in += gain - loss + (noise if h % 2 else -noise)
    return rows


class TestRoomModel:
    def test_learning_needs_data(self):
        model = fit_room_model(synthetic_history(hours=40))
        assert not model.ready
        assert "learning" in model.reason

    def test_fit_recovers_the_rates(self):
        model = fit_room_model(synthetic_history())
        assert model.a == pytest.approx(0.05, rel=0.05)
        assert model.c == pytest.approx(0.9, rel=0.1)
        assert model.cool_hours >= MIN_COOL_HOURS
        assert model.ready
        assert model.backtest_mae_k_h < 0.05

    def test_noisy_model_is_not_used(self):
        model = fit_room_model(synthetic_history(noise=0.8))
        assert not model.ready
        assert "backtest" in model.reason

    def test_hour_accumulator(self):
        acc = HourAccumulator()
        assert acc.add(T0, 21.0, True, -2.0) is None
        assert acc.add(T0 + timedelta(minutes=30), 20.0, False, 0.0) is None
        row = acc.add(T0 + timedelta(hours=1), 20.0, False, 0.0)
        assert row["room_temp_c"] == 20.5 and row["heating_frac"] == 0.5
        assert row["flow_offset_k"] == -1.0


class TestPlanner:
    def model(self):
        m = RoomModel(a=0.05, b=0.0, c=1.0, d=0.0, cool_hours=99, heat_hours=99,
                      backtest_mae_k_h=0.05)
        assert m.ready
        return m

    def slots(self, tiers, outdoor=0.0):
        out = []
        for i, tier in enumerate(tiers):
            s = T0 + timedelta(minutes=30 * i)
            price = {"low": 0.27, "medium": 0.34, "high": 0.45}[tier]
            out.append(HeatSlot(s, s + timedelta(minutes=30), price, tier, outdoor))
        return out

    def test_lowers_in_high_slots_when_the_room_allows(self):
        sl = self.slots(["medium"] * 4 + ["high"] * 2 + ["medium"] * 2)
        plan = plan_heating(self.model(), sl, t_now=21.0, comfort_min_c=19.5,
                            setpoint_c=21.0, up_k=2, down_k=2)
        assert plan.actions[4:6] == [-1, -1]
        assert plan.offset_at(sl[4].start) == -2

    def test_preheats_before_a_violation(self):
        sl = self.slots(["low"] * 2 + ["medium"] * 2 + ["high"] * 6)
        plan = plan_heating(self.model(), sl, t_now=20.6, comfort_min_c=20.0,
                            setpoint_c=20.6, up_k=2, down_k=2)
        assert 1 in plan.actions[:2]                       # pre-heat in a cheap slot
        assert min(plan.temps) >= 20.0 - 1e-6

    def test_not_ready_plans_nothing(self):
        plan = plan_heating(RoomModel(), self.slots(["high"] * 4), t_now=21, comfort_min_c=20,
                            setpoint_c=21, up_k=2, down_k=2)
        assert not plan.ready and set(plan.actions) == {0}
