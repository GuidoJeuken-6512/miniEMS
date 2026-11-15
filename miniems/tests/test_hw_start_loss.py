"""A8 – hot water: learn the heat lost per start and charge it to every start."""
from datetime import datetime, timedelta

import pytest

from hot_water_planner import plan_hot_water
from hw_start_loss import learn_start_losses

from test_hot_water_min_charge import LOSS, model, slots


def _row(start_c, end_c, heat_lambda, from_running=0, valid=1):
    heat_boiler = (end_c - start_c) * 1.163 * 300 / 1000.0
    return {"start_c": start_c, "end_c": end_c, "heat_lambda_kwh": heat_lambda,
            "heat_boiler_kwh": heat_boiler, "from_running": from_running, "valid": valid}


class TestLearning:
    def test_fixed_loss_per_kind(self):
        # standby: 0.8 kWh fixed + 2 % of the charged heat; running: 0.2 kWh fixed
        rows = []
        for dt in (1, 3, 5, 7, 9):
            boiler = dt * 1.163 * 300 / 1000.0
            rows.append(_row(45, 45 + dt, boiler * 1.02 + 0.8))
            rows.append(_row(45, 45 + dt, boiler + 0.2, from_running=1))
        losses = learn_start_losses(rows)
        assert losses.standby_kwh_th == pytest.approx(0.8, abs=0.05)
        assert losses.running_kwh_th == pytest.approx(0.2, abs=0.05)

    def test_needs_five_valid_charges(self):
        rows = [_row(45, 50, 2.5) for _ in range(4)] + [_row(45, 50, 9.9, valid=0)] * 3
        assert learn_start_losses(rows).standby_kwh_th is None

    def test_clamped(self):
        rows = [_row(45, 46 + i, 20.0) for i in range(5)]
        assert learn_start_losses(rows).standby_kwh_th == 3.0


class TestPlanner:
    def test_loss_alone_removes_the_small_top_up(self):
        """2026-11-14 without the fixed minimum charge: a 0.7 kWh top-up at 15:30.

        With 0.5 kWh lost per start that top-up no longer pays – the learned
        loss does what the fixed minimum charge had to do by rule.
        """
        now = datetime(2026, 11, 14, 12, 40)
        kw = dict(floor_c=45.0, shutoff_c=50.0, volume_l=300.0, feed_in_eur_kwh=0.079,
                  requirements=[], tolerance_pct=5.0)
        m = model(loss=LOSS)
        free = plan_hot_water(now, slots(now), 49.3, m, **kw)
        lossy = plan_hot_water(now, slots(now), 49.3, m, start_loss_kwh_th=0.5, **kw)
        assert any(w.heat_kwh_th < 1.0 for w in free.windows)
        assert lossy.windows and all(w.heat_kwh_th >= 1.0 for w in lossy.windows)
        # no start loss given → the same plan as before
        same = plan_hot_water(now, slots(now), 49.3, m, start_loss_kwh_th=0.0, **kw)
        assert [w.start for w in same.windows] == [w.start for w in free.windows]

    def test_running_loss_used_in_slot_zero(self):
        now = datetime(2026, 11, 14, 12, 40)
        kw = dict(floor_c=45.0, shutoff_c=50.0, volume_l=300.0, feed_in_eur_kwh=0.079,
                  requirements=[], min_charge_k=3.0, min_charge_running_k=1.5,
                  compressor_running=True, cheap_rate_eur=0.30)
        m = model(loss=LOSS)
        p = plan_hot_water(now, slots(now), 46.0, m, start_loss_kwh_th=2.0,
                           start_loss_running_kwh_th=0.0, **kw)
        assert p.windows


class TestRecording:
    @pytest.mark.asyncio
    async def test_charge_booked_after_trailing_run(self, make_config, fake_ws):
        from hot_water_controller import HotWaterController
        from const import HWMode

        class Store:
            rows = []

            async def insert_hw_charge(self, fields):
                self.rows.append(fields)

            async def query_hw_charges(self, limit=200):
                return list(self.rows)

        cfg = make_config(boil_temp_entity="sensor.boil", hw_thermal_energy_entity="sensor.th",
                          hw_volume_l=200.0)
        store = Store()
        hw = HotWaterController(cfg, fake_ws, store, None)
        t0 = datetime(2026, 11, 16, 13, 0)
        fake_ws.values["sensor.th"] = 100.0
        hw._start(t0, HWMode.CHARGING, 50.0, 45.0, "test")
        hw._boil_c = 50.0
        hw._stop_charge(t0 + timedelta(minutes=30), "goal reached")
        fake_ws.values["sensor.th"] = 101.8
        await hw._finish_charges(t0 + timedelta(minutes=35))
        assert store.rows == []                      # trailing run not over yet
        await hw._finish_charges(t0 + timedelta(minutes=41))
        row = store.rows[0]
        assert row["heat_lambda_kwh"] == pytest.approx(1.8)
        assert row["heat_boiler_kwh"] == pytest.approx(5 * 1.163 * 0.2, abs=1e-3)
        assert row["valid"] == 1

    def test_draw_voids_the_charge(self, make_config, fake_ws):
        from hot_water_controller import HotWaterController
        from const import HWMode
        cfg = make_config(boil_temp_entity="sensor.boil", hw_thermal_energy_entity="sensor.th")
        hw = HotWaterController(cfg, fake_ws, None, None)
        fake_ws.values["sensor.th"] = 10.0
        hw._start(datetime(2026, 11, 16, 13), HWMode.CHARGING, 50.0, 45.0, "test")
        hw.note_draw()
        hw._boil_c = 49.0
        hw._stop_charge(datetime(2026, 11, 16, 13, 30), "goal")
        assert hw._pending_charges[0]["valid"] == 0

    def test_losses_only_with_switch(self, make_config, fake_ws):
        from hot_water_controller import HotWaterController
        from hw_start_loss import StartLosses
        off = HotWaterController(make_config(boil_temp_entity="s"), fake_ws, None, None)
        off._start_losses = StartLosses(0.8, 0.2, 5, 5)
        assert off._start_loss_args() == {}
        on = HotWaterController(make_config(boil_temp_entity="s", hw_start_cost_enabled=True),
                                fake_ws, None, None)
        on._start_losses = StartLosses(0.8, 0.2, 5, 5)
        assert on._start_loss_args() == {"start_loss_kwh_th": 0.8, "start_loss_running_kwh_th": 0.2}
