"""hot_water_model.py: COP fit with prior, loss, heat power, draw profile."""
from datetime import date, datetime, timedelta

import pytest

from hot_water_model import HotWaterModel, fit_hot_water_model, is_workday

TODAY = date(2026, 10, 29)   # Thursday
INTERVAL = 30


def hp_row(temp, cop, mode=2, cop_ticks=120, ticks=120, kwh=None, hour="2026-10-28T10:00:00+01:00"):
    return {"hour_start": hour, "mode": mode, "outdoor_temp_c": temp, "cop_avg": cop,
            "cop_ticks": cop_ticks, "ticks": ticks, "heat_pump_kwh": kwh}


def fit(make_config, rows=(), segments=(), draws=(), **cfg):
    return fit_hot_water_model(list(rows), list(segments), list(draws),
                               make_config(**cfg), TODAY, INTERVAL)


class TestCop:
    def test_prior_only(self, make_config):
        m = fit(make_config)
        assert m.cop_source == "prior"
        assert m.cop_at(8.5) == pytest.approx(4.77)
        assert m.cop_at(18.5) == pytest.approx(4.77 + 10 * 0.08)
        assert m.cop_at(None) == pytest.approx(4.77)   # reference temperature

    def test_real_data_outweighs_prior(self, make_config):
        rows = [hp_row(t, 2.0 + 0.1 * t, hour=f"2026-10-2{i % 9}T{i % 24:02d}:00:00+01:00")
                for i, t in enumerate([0, 5, 10, 15, 20] * 6)]
        m = fit(make_config, rows)
        assert m.cop_source == "learned"
        assert m.cop_hours == pytest.approx(30.0)
        # 30 h real data at COP 3.0 vs 3 h prior at ~4.9: close to the data
        assert 3.0 < m.cop_at(10.0) < 3.25

    def test_only_hot_water_mode_and_enough_ticks_count(self, make_config):
        rows = [hp_row(5, 9.0, mode=1), hp_row(5, 9.0, cop_ticks=3)]
        assert fit(make_config, rows).cop_source == "prior"

    def test_clamped(self, make_config):
        m = fit(make_config, hw_cop_prior=7.9)
        assert m.cop_at(40.0) == 8.0


class TestLossAndPower:
    def test_loss_prior_and_learned(self, make_config):
        assert fit(make_config).loss_source == "prior"
        segs = [{"k_per_h": 0.6, "avg_boil_temp_c": 50.0}] * 3
        m = fit(make_config, segments=segs)
        assert m.loss_source == "learned"
        assert m.loss_k_per_h(50.0) == pytest.approx(0.6)
        assert m.loss_k_per_h(35.0) == pytest.approx(0.3)

    def test_heat_power_from_history(self, make_config):
        # 1 h at 1.0 kWh electric and COP 4 → 4 kW thermal
        rows = [hp_row(8, 4.0, kwh=1.0, hour=f"2026-10-28T{h:02d}:00:00+01:00") for h in range(3)]
        m = fit(make_config, rows)
        assert m.heat_source == "learned"
        assert m.heat_kw_th == pytest.approx(4.0)
        assert fit(make_config, hw_heat_power_kw=3.0).heat_kw_th == 3.0

    def test_ww_share_per_hour(self, make_config):
        rows = [hp_row(8, 4.0, kwh=1.0, hour="2026-10-28T06:00:00+01:00"),
                hp_row(8, None, mode=0, hour="2026-10-28T07:00:00+01:00")]
        assert fit(make_config, rows).ww_elec_kwh_per_h == pytest.approx(0.5)


class TestDraws:
    def test_needs_enough_days(self, make_config):
        draws = [{"ts": "2026-10-27T07:10:00+01:00", "drop_k": 5.0}]
        m = fit(make_config, draws=draws)
        assert m.draw_profile == {}

    def test_profile_per_workday_hour(self, make_config):
        draws = []
        for back in range(1, 15):
            day = TODAY - timedelta(days=back)
            if is_workday(day):
                draws.append({"ts": f"{day}T07:15:00+01:00", "drop_k": 6.0})
        m = fit(make_config, draws=draws)
        assert m.draw_days == 14
        assert m.draw_profile[(True, 7)] == pytest.approx(6.0)
        start = datetime(2026, 10, 30, 7, 0)   # Friday
        assert m.draw_k(start, start + timedelta(minutes=30)) == pytest.approx(3.0)
        sat = datetime(2026, 10, 31, 7, 0)
        assert m.draw_k(sat, sat + timedelta(hours=1)) == 0.0


def test_as_dict_marks_prior(make_config):
    d = fit(make_config).as_dict(8.5)
    assert d["cop_source"] == "prior"
    assert d["cop_now"] == pytest.approx(4.77)
    assert isinstance(fit(make_config), HotWaterModel)
