"""A7 – learned battery values (A7a) and forecast corrections (A7b/c)."""
from datetime import date, datetime, timedelta, timezone

import pytest

import store as store_module
from battery_learning import learn_battery
from cost_optimizer import CostOptimizer
from forecast_learning import ForecastCorrections, learn_corrections
from load_forecast import HourlyLoadRecorder
from store import EnergyStore

from test_ems_controller import make_controller

TZ = timezone(timedelta(hours=1))


def _day(i, bat_in=10.0, bat_out=9.0, first=20.0, last=20.0, load=4.5, bat=5.0):
    return {"date": (date(2026, 11, 1) + timedelta(days=i)).isoformat(),
            "bat_in_kwh": bat_in, "bat_out_kwh": bat_out, "soc_first_pct": first,
            "soc_last_pct": last, "dis_eff_load_kwh": load, "dis_eff_bat_kwh": bat}


class TestBatteryLearning:
    def test_learns_within_bounds(self):
        lb = learn_battery([_day(i) for i in range(7)], capacity_kwh=10.0)
        assert lb.eta_dc == pytest.approx(0.9)
        assert lb.eta_discharge == pytest.approx(0.9)
        assert lb.round_trip == pytest.approx(0.9 * 0.81)

    def test_soc_change_enters_dc_efficiency(self):
        rows = [_day(0, first=20.0, last=20.0)] + [_day(i) for i in range(1, 5)]
        rows[-1]["soc_last_pct"] = 40.0          # 2 kWh more stored at the end
        lb = learn_battery(rows, capacity_kwh=10.0)
        assert lb.eta_dc == pytest.approx((45.0 + 2.0) / 50.0)

    def test_too_few_days_or_implausible(self):
        assert learn_battery([_day(0), _day(1)], 10.0).round_trip is None
        lb = learn_battery([_day(i, bat_out=10.5) for i in range(5)], 10.0)
        assert lb.eta_dc is None                  # > 99 %: not physical

    def test_capacity_from_soc_swing(self):
        rows = [_day(i, bat_in=9.0 / 0.9 ** 0.5, bat_out=0.0, first=10.0, last=100.0)
                for i in range(4)]
        rows += [_day(4 + i, bat_in=0.0, bat_out=8.0, first=100.0, last=10.0) for i in range(4)]
        lb = learn_battery(rows, capacity_kwh=10.0)
        assert lb.capacity_kwh is not None and 8.0 < lb.capacity_kwh < 12.0


def _hour(day, hour, pv=None, fc=None, load=None, pred=None):
    return {"hour_start": datetime(2026, 11, day, hour, tzinfo=TZ).isoformat(), "hour": hour,
            "pv_kwh": pv, "pv_fc_kwh": fc, "load_kwh": load, "load_pred_kwh": pred}


class TestForecastLearning:
    def test_pv_factor_per_hour(self):
        rows = [_hour(d, 9, pv=0.5, fc=1.0) for d in range(1, 8)]
        rows += [_hour(d, 13, pv=2.4, fc=2.0) for d in range(1, 8)]
        c = learn_corrections(rows)
        assert c.pv_by_hour[9] == pytest.approx(0.5)
        assert c.pv_by_hour[13] == pytest.approx(1.2)
        assert c.pv_factor(9, per_hour=True, total=False) == pytest.approx(0.5)
        assert c.pv_factor(9, per_hour=False, total=False) == 1.0

    def test_needs_enough_days(self):
        c = learn_corrections([_hour(d, 9, pv=0.5, fc=1.0) for d in range(1, 4)])
        assert 9 not in c.pv_by_hour

    def test_clamped(self):
        rows = [_hour(d, 9, pv=0.05, fc=1.0) for d in range(1, 8)]
        assert learn_corrections(rows).pv_by_hour[9] == 0.3

    def test_load_factor_needs_72_hours(self):
        rows = [_hour(1 + h // 24, h % 24, load=1.3, pred=1.0) for h in range(100)]
        assert learn_corrections(rows).load_total == pytest.approx(1.25)   # clamped from 1.3
        assert learn_corrections(rows[:50]).load_total == 1.0


class TestRecording:
    @pytest.fixture
    async def db(self, tmp_path, monkeypatch):
        monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "t.db"))
        s = EnergyStore()
        await s.open()
        yield s
        await s.close()

    @pytest.mark.asyncio
    async def test_recorder_stores_pv_and_forecasts(self, db):
        rec = HourlyLoadRecorder(db, interval_sec=1800)
        t = datetime(2026, 11, 16, 10, 0, tzinfo=TZ)
        await rec.record_tick(t, 1000.0, None, None, pv_w=2000.0)
        rec.set_forecasts(1.5, 0.9)
        await rec.record_tick(t + timedelta(minutes=30), 1000.0, None, None, pv_w=4000.0)
        rec.set_forecasts(9.9, 9.9)                # later values do not overwrite
        rows = await db.query_hours_since(t.date())
        assert rows[0]["pv_kwh"] == pytest.approx(3.0)
        assert rows[0]["pv_fc_kwh"] == pytest.approx(1.5)
        assert rows[0]["load_pred_kwh"] == pytest.approx(0.9)

    @pytest.mark.asyncio
    async def test_optimizer_battery_energy(self, db, make_config):
        opt = CostOptimizer(make_config(), db)
        opt.note_maxima(50.0, None)
        opt.record_tick(grid_power_w=0, pv_power_w=0, load_power_w=900, battery_power_w=1000,
                        price_eur_kwh=0.3, interval_sec=3600)
        # the spike filter adopts a jump only once a second tick confirms it
        for _ in range(3):
            opt.record_tick(grid_power_w=2000, pv_power_w=0, load_power_w=0, battery_power_w=-2000,
                            price_eur_kwh=0.3, interval_sec=1800)
        opt.note_maxima(60.0, None)
        await opt.flush_to_db()
        row = await db.load_day(date.today())
        assert row["bat_out_kwh"] == pytest.approx(1.0)
        assert row["bat_in_kwh"] == pytest.approx(2.0)      # 2 confirmed ticks × 1 kWh
        assert row["dis_eff_load_kwh"] == pytest.approx(0.9)
        assert (row["soc_first_pct"], row["soc_last_pct"]) == (50.0, 60.0)


class TestUse:
    def test_corrections_only_with_switch(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._corrections = ForecastCorrections(pv_by_hour={9: 0.5}, pv_total=0.8, load_total=1.2)
        t = datetime(2026, 11, 16, 9, tzinfo=TZ)
        assert ctrl._pv_factor(t) == 1.0 and ctrl._load_factor() == 1.0
        on = make_controller(make_config, fake_ws, pv_correction_enabled=True,
                             plan_feedback_enabled=True)
        on._corrections = ctrl._corrections
        assert on._pv_factor(t) == 0.5
        assert on._pv_factor(t.replace(hour=13)) == 0.8
        assert on._load_factor() == 1.2

    def test_learned_eta_only_with_switch(self, make_config, fake_ws):
        lb = learn_battery([_day(i) for i in range(7)], 10.0)
        ctrl = make_controller(make_config, fake_ws)
        ctrl._learned_battery = lb
        assert ctrl._round_trip_eta() == pytest.approx(0.9)       # FakeOptimizer's value
        on = make_controller(make_config, fake_ws, battery_learned_values_enabled=True)
        on._learned_battery = lb
        assert on._round_trip_eta() == pytest.approx(0.729)
        assert on._discharge_eta() == pytest.approx(0.9)

    @pytest.mark.parametrize("measured", [-10.0, -0.5, 0.0, 0.11, 0.44, 1.2, None])
    def test_implausible_measured_eta_falls_back_to_default(self, make_config, fake_ws, measured):
        """17.11.2026: today's running inverter efficiency read 11 % at 13:49 –
        the day plan stopped grid charging in the low-price window."""
        ctrl = make_controller(make_config, fake_ws)
        ctrl._optimizer._efficiency = measured
        assert ctrl._measured_eta() is None
        assert ctrl._round_trip_eta() == pytest.approx(0.9)       # _DEFAULT_ETA
        assert ctrl._discharge_eta() == pytest.approx(0.9 ** 0.5)

    def test_plausible_measured_eta_is_used(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._optimizer._efficiency = 0.77
        assert ctrl._round_trip_eta() == pytest.approx(0.77)

    def test_implausible_eta_does_not_block_legacy_gate(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        ctrl._discharge_tariff = 0.3944
        ctrl._optimizer._efficiency = 0.11
        assert ctrl._grid_charge_pays_legacy(0.2744) is True
