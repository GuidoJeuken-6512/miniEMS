"""energy_plan.py: the proactive tomorrow-deficit calculation and window fill."""
from datetime import datetime, timedelta

import pytest

from energy_plan import PlannedWindow, compute_energy_plan
from price_curve import PriceCurve


class _AttrWS:
    def __init__(self, attributes: dict) -> None:
        self._attrs = attributes

    def get_state_attribute(self, entity_id: str, attribute: str):
        return self._attrs.get(attribute)


_OCTOPUS_TIMESLOTS = {
    "timeslots": [
        {
            "name": "NIEDRIG", "rate": "27.4414",
            "activation_rules": [
                {"from_time": "02:00:00", "to_time": "06:00:00"},
                {"from_time": "12:00:00", "to_time": "16:00:00"},
            ],
        },
        {
            "name": "STANDARD", "rate": "34.4400",
            "activation_rules": [
                {"from_time": "06:00:00", "to_time": "12:00:00"},
                {"from_time": "16:00:00", "to_time": "18:00:00"},
                {"from_time": "21:00:00", "to_time": "02:00:00"},
            ],
        },
        {
            "name": "HOCH", "rate": "39.4400",
            "activation_rules": [{"from_time": "18:00:00", "to_time": "21:00:00"}],
        },
    ],
}


def _curve() -> PriceCurve:
    return PriceCurve.from_entity(_AttrWS(_OCTOPUS_TIMESLOTS), "sensor.price")


def _dt(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 8, day, hour, minute)


class TestPlannedWindow:
    def test_cost_is_energy_times_rate(self):
        w = PlannedWindow(_dt(2), _dt(4), rate_eur_kwh=0.30, energy_kwh=2.0)
        assert w.cost_eur == pytest.approx(0.6)


class TestComputeEnergyPlanGuardClauses:
    def test_none_soc_is_unfeasible(self):
        plan = compute_energy_plan(_dt(20), _dt(20, day=16), None, 5.0, 1.0, 1.0, _curve())
        assert plan.deficit_kwh is None
        assert plan.feasible is False
        assert "SoC" in plan.reason

    def test_none_forecast_is_unfeasible(self):
        plan = compute_energy_plan(_dt(20), _dt(20, day=16), 5.0, None, 1.0, 1.0, _curve())
        assert plan.deficit_kwh is None
        assert plan.feasible is False
        assert "forecast" in plan.reason

    def test_zero_deficit_when_forecast_covers_need(self):
        plan = compute_energy_plan(_dt(20), _dt(20, day=16), 2.0, 10.0, 1.0, 1.0, _curve())
        assert plan.deficit_kwh == 0.0
        assert plan.feasible is True
        assert plan.windows == []

    def test_no_deadline_is_unfeasible_but_deficit_known(self):
        plan = compute_energy_plan(_dt(20), None, 5.0, 0.0, 1.0, 1.0, _curve())
        assert plan.deficit_kwh == pytest.approx(5.0)
        assert plan.feasible is False
        assert "deadline" in plan.reason

    def test_no_curve_is_unfeasible_but_deficit_known(self):
        plan = compute_energy_plan(_dt(20), _dt(20, day=16), 5.0, 0.0, 1.0, 1.0, None)
        assert plan.deficit_kwh == pytest.approx(5.0)
        assert plan.feasible is False
        assert "tariff" in plan.reason

    def test_no_charge_power_is_unfeasible_but_deficit_known(self):
        plan = compute_energy_plan(_dt(20), _dt(20, day=16), 5.0, 0.0, 1.0, None, _curve())
        assert plan.deficit_kwh == pytest.approx(5.0)
        assert plan.feasible is False
        assert "charge power" in plan.reason


class TestComputeEnergyPlanFill:
    def test_deficit_formula_matches_the_tomorrow_fallback(self):
        # Same formula as _should_grid_charge()'s reactive branch:
        # max(0, bat_kwh_free - predicted_pv_tomorrow_kwh * margin_factor)
        plan = compute_energy_plan(
            _dt(20), _dt(13, day=16), bat_kwh_free=6.0,
            predicted_pv_tomorrow_kwh=4.0, margin_factor=1.2,
            charge_kw=1.0, curve=_curve(),
        )
        assert plan.deficit_kwh == pytest.approx(6.0 - 4.0 * 1.2)   # 1.2

    def test_fills_cheapest_window_first(self):
        # 20:00 -> tomorrow 13:00: candidates before the deadline include
        # STANDARD (16-18/21-02 today+tonight), HOCH (18-21), and tomorrow's
        # NIEDRIG 02-06 and 12-13 (clipped). NIEDRIG is cheapest -> fills first.
        plan = compute_energy_plan(
            _dt(20), _dt(13, day=16), bat_kwh_free=1.0,
            predicted_pv_tomorrow_kwh=0.0, margin_factor=1.0,
            charge_kw=1.0, curve=_curve(),
        )
        assert plan.feasible is True
        assert len(plan.windows) == 1
        assert plan.windows[0].rate_eur_kwh == pytest.approx(0.274414)
        assert plan.windows[0].energy_kwh == pytest.approx(1.0)

    def test_spills_into_next_cheapest_window_when_one_is_not_enough(self):
        # deficit needs more than one NIEDRIG window (02-06, 4h @ 1kW = 4kWh)
        # can supply alone -> spills into the 12-13 (clipped) NIEDRIG slice too.
        plan = compute_energy_plan(
            _dt(20), _dt(13, day=16), bat_kwh_free=4.5,
            predicted_pv_tomorrow_kwh=0.0, margin_factor=1.0,
            charge_kw=1.0, curve=_curve(),
        )
        assert plan.feasible is True
        assert plan.planned_kwh == pytest.approx(4.5)
        assert len(plan.windows) == 2   # both NIEDRIG slices, still cheaper than STANDARD/HOCH
        assert all(w.rate_eur_kwh == pytest.approx(0.274414) for w in plan.windows)

    def test_windows_are_chronological_even_though_filled_by_price(self):
        plan = compute_energy_plan(
            _dt(20), _dt(13, day=16), bat_kwh_free=4.5,
            predicted_pv_tomorrow_kwh=0.0, margin_factor=1.0,
            charge_kw=1.0, curve=_curve(),
        )
        starts = [w.start for w in plan.windows]
        assert starts == sorted(starts)

    def test_infeasible_when_candidates_cannot_cover_the_deficit(self):
        # Deadline right after "now" -> almost nothing to fill from.
        plan = compute_energy_plan(
            _dt(20), _dt(20, minute=5), bat_kwh_free=10.0,
            predicted_pv_tomorrow_kwh=0.0, margin_factor=1.0,
            charge_kw=1.0, curve=_curve(),
        )
        assert plan.feasible is False
        assert plan.planned_kwh < 10.0
        assert "cannot cover" in plan.reason

    def test_estimated_cost_is_sum_of_window_costs(self):
        plan = compute_energy_plan(
            _dt(20), _dt(13, day=16), bat_kwh_free=1.0,
            predicted_pv_tomorrow_kwh=0.0, margin_factor=1.0,
            charge_kw=1.0, curve=_curve(),
        )
        assert plan.estimated_cost_eur == pytest.approx(sum(w.cost_eur for w in plan.windows))


class TestWindowsBefore:
    def test_empty_when_deadline_not_after_start(self):
        assert _curve().windows_before(_dt(10), _dt(10)) == []
        assert _curve().windows_before(_dt(10), _dt(9)) == []

    def test_clips_the_first_window_to_start(self):
        result = _curve().windows_before(_dt(3), _dt(7))
        assert result[0] == (_dt(3), _dt(6), pytest.approx(0.274414))

    def test_clips_the_last_window_to_deadline(self):
        result = _curve().windows_before(_dt(3), _dt(5))
        assert result == [(_dt(3), _dt(5), pytest.approx(0.274414))]

    def test_spans_multiple_windows_in_order(self):
        result = _curve().windows_before(_dt(1), _dt(7))
        # STANDARD (21-02, clipped to 01-02), NIEDRIG (02-06), STANDARD (06-07)
        assert [r[2] for r in result] == pytest.approx([0.3444, 0.274414, 0.3444])
        assert result[0][0] == _dt(1)
        assert result[-1][1] == _dt(7)

    def test_skips_over_a_calendar_gap(self):
        data = {"timeslots": [
            {"name": "X", "rate": "10.0",
             "activation_rules": [{"from_time": "05:00", "to_time": "06:00"}]},
        ]}
        curve = PriceCurve.from_entity(_AttrWS(data), "sensor.price")
        result = curve.windows_before(_dt(0), _dt(7))
        assert result == [(_dt(5), _dt(6), pytest.approx(0.10))]
