"""PriceCurve: tariff preview built from a price entity's timeslots attribute."""
from datetime import datetime, time, timedelta

import pytest

from price_curve import PriceCurve


class _AttrWS:
    """Minimal ws double exposing only get_state_attribute, as PriceCurve needs."""

    def __init__(self, attributes: dict) -> None:
        self._attrs = attributes

    def get_state_attribute(self, entity_id: str, attribute: str):
        return self._attrs.get(attribute)


def _octopus_timeslots() -> dict:
    """Real-shape fixture, matching the production activation_rules layout."""
    return {
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
                    {"from_time": "21:00:00", "to_time": "02:00:00"},   # wraps midnight
                ],
            },
            {
                "name": "HOCH", "rate": "39.4400",
                "activation_rules": [{"from_time": "18:00:00", "to_time": "21:00:00"}],
            },
        ],
    }


def _dt(hour: int, minute: int = 0, day: int = 15) -> datetime:
    return datetime(2026, 8, day, hour, minute)


class TestFromEntity:
    def test_no_entity_returns_none(self):
        ws = _AttrWS({})
        assert PriceCurve.from_entity(ws, "") is None

    def test_no_timeslots_attribute_returns_none(self):
        ws = _AttrWS({"rates": [], "unit_rate_forecast": []})
        assert PriceCurve.from_entity(ws, "sensor.price") is None

    def test_empty_timeslots_list_returns_none(self):
        ws = _AttrWS({"timeslots": []})
        assert PriceCurve.from_entity(ws, "sensor.price") is None

    def test_builds_curve_from_octopus_shape(self):
        ws = _AttrWS(_octopus_timeslots())
        curve = PriceCurve.from_entity(ws, "sensor.price")
        assert curve is not None
        assert "sensor.price" in curve.source

    def test_skips_timeslot_with_unparsable_rate(self):
        data = {"timeslots": [
            {"name": "BROKEN", "rate": "not-a-number",
             "activation_rules": [{"from_time": "00:00", "to_time": "01:00"}]},
        ]}
        ws = _AttrWS(data)
        # No usable window survives -> None, not a crash
        assert PriceCurve.from_entity(ws, "sensor.price") is None

    def test_skips_rule_with_unparsable_time(self):
        data = {"timeslots": [
            {"name": "PARTIAL", "rate": "10.0",
             "activation_rules": [
                 {"from_time": "not-a-time", "to_time": "06:00"},
                 {"from_time": "10:00", "to_time": "11:00"},
             ]},
        ]}
        ws = _AttrWS(data)
        curve = PriceCurve.from_entity(ws, "sensor.price")
        assert curve is not None
        w = curve.window_at(_dt(10, 30))
        assert w is not None and w.rate_eur_kwh == 0.10


class TestWindowAt:
    @pytest.fixture
    def curve(self):
        return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")

    def test_niedrig_morning_window(self, curve):
        w = curve.window_at(_dt(3, 0))
        assert w.name == "NIEDRIG"
        assert w.rate_eur_kwh == pytest.approx(0.274414)

    def test_niedrig_afternoon_window(self, curve):
        w = curve.window_at(_dt(13, 0))
        assert w.name == "NIEDRIG"

    def test_hoch_window(self, curve):
        w = curve.window_at(_dt(19, 0))
        assert w.name == "HOCH"

    def test_standard_wraps_midnight_late_evening(self, curve):
        w = curve.window_at(_dt(23, 0))
        assert w.name == "STANDARD"

    def test_standard_wraps_midnight_after_midnight(self, curve):
        w = curve.window_at(_dt(1, 0))
        assert w.name == "STANDARD"

    def test_gap_returns_none(self):
        # A calendar with a hole between 00:00 and 01:00
        data = {"timeslots": [
            {"name": "X", "rate": "10.0",
             "activation_rules": [{"from_time": "01:00", "to_time": "02:00"}]},
        ]}
        curve = PriceCurve.from_entity(_AttrWS(data), "sensor.price")
        assert curve.window_at(_dt(0, 30)) is None


class TestWindowEnd:
    @pytest.fixture
    def curve(self):
        return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")

    def test_end_same_day(self, curve):
        end = curve.window_end(_dt(3, 0))
        assert end == _dt(6, 0)

    def test_end_wraps_to_next_day(self, curve):
        # STANDARD 21:00-02:00: at 23:00 the end (02:00) is on the next day
        end = curve.window_end(_dt(23, 0, day=15))
        assert end == _dt(2, 0, day=16)

    def test_end_none_in_a_gap(self):
        data = {"timeslots": [
            {"name": "X", "rate": "10.0",
             "activation_rules": [{"from_time": "01:00", "to_time": "02:00"}]},
        ]}
        curve = PriceCurve.from_entity(_AttrWS(data), "sensor.price")
        assert curve.window_end(_dt(10, 0)) is None


class TestCheapestRateBetween:
    @pytest.fixture
    def curve(self):
        return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")

    def test_finds_minimum_across_window(self, curve):
        # 00:00 -> 20:00 spans NIEDRIG (0.2744), STANDARD (0.344), HOCH (0.3944)
        rate = curve.cheapest_rate_between(_dt(0, 0), _dt(20, 0))
        assert rate == pytest.approx(0.274414)

    def test_empty_span_returns_none(self, curve):
        assert curve.cheapest_rate_between(_dt(10, 0), _dt(10, 0)) is None

    def test_deadline_before_start_returns_none(self, curve):
        assert curve.cheapest_rate_between(_dt(10, 0), _dt(9, 0)) is None


class TestIsCheapestNow:
    @pytest.fixture
    def curve(self):
        return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")

    def test_true_when_current_window_is_the_cheapest(self, curve):
        # At 03:00 (NIEDRIG) with a deadline later that still includes NIEDRIG again
        assert curve.is_cheapest_now(_dt(3, 0), _dt(20, 0)) is True

    def test_false_when_a_cheaper_window_is_still_ahead(self, curve):
        # At 08:00 (STANDARD, 0.344) with deadline 15:00 – NIEDRIG at 12-16 is cheaper
        assert curve.is_cheapest_now(_dt(8, 0), _dt(15, 0)) is False

    def test_02_and_12_niedrig_windows_are_equally_cheapest(self, curve):
        # This is exactly the open problem V2 documents: both NIEDRIG windows
        # carry the same rate, so "cheapest now" is True for either.
        assert curve.is_cheapest_now(_dt(3, 0), _dt(20, 0)) is True
        assert curve.is_cheapest_now(_dt(13, 0), _dt(20, 0)) is True

    def test_none_when_calendar_has_a_gap_at_current_moment(self):
        data = {"timeslots": [
            {"name": "X", "rate": "10.0",
             "activation_rules": [{"from_time": "01:00", "to_time": "02:00"}]},
        ]}
        curve = PriceCurve.from_entity(_AttrWS(data), "sensor.price")
        assert curve.is_cheapest_now(_dt(10, 0), _dt(20, 0)) is None


class TestLaterWindowAsCheap:
    """V2's actual fix for the 12-16 Uhr problem is_cheapest_now() can't
    solve: two equally-cheap windows, one of which overlaps PV production."""

    @pytest.fixture
    def curve(self):
        return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")

    def test_afternoon_niedrig_defers_to_tonights_niedrig(self, curve):
        # 15:30, still in the 12-16 NIEDRIG window; deadline is tomorrow's PV
        # peak (~13:00, well over a day out) -> tonight's 02-06 NIEDRIG window
        # fits comfortably and is equally cheap -> defer.
        now = _dt(15, 30, day=15)
        deadline = _dt(13, 0, day=16)
        assert curve.later_window_as_cheap(now, deadline, timedelta(hours=2)) is True

    def test_night_niedrig_does_not_defer_to_a_pricier_window(self, curve):
        # 02:30, in tonight's NIEDRIG window; deadline is *today's* own peak
        # (13:00) -> the only window before the deadline is 06-12 STANDARD,
        # pricier than NIEDRIG -> do not defer, charge now.
        now = _dt(2, 30, day=15)
        deadline = _dt(13, 0, day=15)
        assert curve.later_window_as_cheap(now, deadline, timedelta(hours=2)) is False

    def test_no_deferral_when_deadline_leaves_no_slack(self, curve):
        # Deadline only 30 minutes out, but 2h still needed to charge ->
        # latest_feasible_start is already in the past relative to now.
        now = _dt(12, 30, day=15)
        deadline = _dt(13, 0, day=15)
        assert curve.later_window_as_cheap(now, deadline, timedelta(hours=2)) is False

    def test_false_outside_any_window(self):
        data = {"timeslots": [
            {"name": "X", "rate": "10.0",
             "activation_rules": [{"from_time": "01:00", "to_time": "02:00"}]},
        ]}
        curve = PriceCurve.from_entity(_AttrWS(data), "sensor.price")
        assert curve.later_window_as_cheap(_dt(10, 0), _dt(20, 0), timedelta(hours=1)) is False

    def test_false_when_calendar_has_a_hole_before_the_deadline(self, curve):
        # Deadline right at the end of the current window, no later window at
        # all is reachable before it.
        now = _dt(13, 0, day=15)   # NIEDRIG 12-16
        deadline = _dt(16, 0, day=15)   # window_end(now) itself
        assert curve.later_window_as_cheap(now, deadline, timedelta(hours=0)) is False
