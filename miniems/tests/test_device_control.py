"""device_control.py: the Watt <-> actuator-native-unit boundary."""
import pytest

from device_control import (
    ActuatorLimits,
    LiveLimitsCache,
    clamp_to_limits,
    from_actuator_value,
    resolve_limits,
    to_actuator_value,
)
from device_profile import ActuatorControl


def _dc_control(**overrides) -> ActuatorControl:
    defaults = dict(quantity="power", domain="number", actuator_unit="A", via="dc_current")
    defaults.update(overrides)
    return ActuatorControl(**defaults)


class TestToActuatorValueDcCurrent:
    def test_uses_live_voltage(self):
        control = _dc_control(voltage_fallback_v=48.0)
        assert to_actuator_value(1000.0, control, voltage_v=50.0) == pytest.approx(20.0)

    def test_falls_back_to_profile_voltage_when_live_missing(self):
        control = _dc_control(voltage_fallback_v=50.0)
        assert to_actuator_value(1000.0, control, voltage_v=None) == pytest.approx(20.0)

    def test_falls_back_when_live_voltage_is_zero_or_negative(self):
        control = _dc_control(voltage_fallback_v=50.0)
        assert to_actuator_value(1000.0, control, voltage_v=0.0) == pytest.approx(20.0)
        assert to_actuator_value(1000.0, control, voltage_v=-5.0) == pytest.approx(20.0)

    def test_zero_when_no_voltage_available_at_all(self):
        control = _dc_control(voltage_fallback_v=0.0)
        assert to_actuator_value(1000.0, control, voltage_v=None) == 0.0


class TestToActuatorValueAcCurrent:
    def test_single_phase_default_230v(self):
        control = ActuatorControl(via="ac_current")
        assert to_actuator_value(2300.0, control) == pytest.approx(10.0)

    def test_three_phase(self):
        control = ActuatorControl(via="ac_current", voltage_fixed_v=230.0)
        assert to_actuator_value(6900.0, control, phases=3) == pytest.approx(10.0)

    def test_custom_fixed_voltage(self):
        control = ActuatorControl(via="ac_current", voltage_fixed_v=400.0)
        assert to_actuator_value(4000.0, control) == pytest.approx(10.0)


class TestToActuatorValuePercentOfRated:
    def test_half_of_rated_power(self):
        control = ActuatorControl(via="percent_of_rated", rated_power_w=5000.0)
        assert to_actuator_value(2500.0, control) == pytest.approx(50.0)

    def test_zero_when_rated_power_missing(self):
        control = ActuatorControl(via="percent_of_rated", rated_power_w=0.0)
        assert to_actuator_value(2500.0, control) == 0.0


class TestToActuatorValueNativePower:
    def test_passthrough_unchanged(self):
        control = ActuatorControl(via="native_power")
        assert to_actuator_value(1234.5, control) == 1234.5

    def test_unrecognised_via_passes_through(self):
        control = ActuatorControl(via="something_unknown")
        assert to_actuator_value(1234.5, control) == 1234.5


class TestFromActuatorValue:
    def test_dc_current_inverse_of_to_actuator_value(self):
        control = _dc_control(voltage_fallback_v=50.0)
        amps = to_actuator_value(1000.0, control, voltage_v=50.0)
        assert from_actuator_value(amps, control, voltage_v=50.0) == pytest.approx(1000.0)

    def test_dc_current_zero_when_no_voltage(self):
        control = _dc_control(voltage_fallback_v=0.0)
        assert from_actuator_value(20.0, control, voltage_v=None) == 0.0

    def test_ac_current_inverse(self):
        control = ActuatorControl(via="ac_current", voltage_fixed_v=230.0)
        amps = to_actuator_value(2300.0, control)
        assert from_actuator_value(amps, control) == pytest.approx(2300.0)

    def test_percent_of_rated_inverse(self):
        control = ActuatorControl(via="percent_of_rated", rated_power_w=5000.0)
        pct = to_actuator_value(2500.0, control)
        assert from_actuator_value(pct, control) == pytest.approx(2500.0)

    def test_native_power_passthrough(self):
        control = ActuatorControl(via="native_power")
        assert from_actuator_value(500.0, control) == 500.0


class TestClampToLimits:
    def test_within_range_unchanged_by_clamp_but_quantised(self):
        limits = ActuatorLimits(min=0, max=350, step=1)
        assert clamp_to_limits(42.4, limits) == 42.0

    def test_below_min_clamped_up(self):
        limits = ActuatorLimits(min=10, max=350, step=1)
        assert clamp_to_limits(-5, limits) == 10.0

    def test_above_max_clamped_down(self):
        limits = ActuatorLimits(min=0, max=185, step=1)
        assert clamp_to_limits(300, limits) == 185.0

    def test_quantised_to_step(self):
        limits = ActuatorLimits(min=0, max=350, step=5)
        assert clamp_to_limits(23, limits) == 25.0

    def test_zero_step_skips_quantisation(self):
        limits = ActuatorLimits(min=0, max=350, step=0)
        assert clamp_to_limits(23.7, limits) == pytest.approx(23.7)


class TestResolveLimits:
    def test_all_live_values_used_when_present(self):
        control = ActuatorControl(fallback_limits={"min": 0, "max": 999, "step": 99})
        limits = resolve_limits(1.0, 2.0, 3.0, control)
        assert limits == ActuatorLimits(min=1.0, max=2.0, step=3.0)

    def test_falls_back_per_field_not_all_or_nothing(self):
        control = ActuatorControl(fallback_limits={"min": 5, "max": 350, "step": 1})
        limits = resolve_limits(None, 200.0, None, control)
        assert limits == ActuatorLimits(min=5.0, max=200.0, step=1.0)

    def test_no_fallback_limits_defaults_to_zero_min_max_step_one(self):
        control = ActuatorControl()
        limits = resolve_limits(None, None, None, control)
        assert limits == ActuatorLimits(min=0.0, max=0.0, step=1.0)


class TestLiveLimitsCache:
    def test_first_resolution_uses_live_max(self):
        control = ActuatorControl(fallback_limits={"min": 0, "max": 350, "step": 1})
        cache = LiveLimitsCache()
        limits = cache.resolve("number.x", 0.0, 200.0, 1.0, control)
        assert limits.max == 200.0

    def test_unavailable_max_falls_back_to_last_good_not_the_profile_default(self):
        """The critical safety guarantee: a momentarily missing live max must
        never silently drop to the (possibly much lower or zero) profile
        fallback - it must hold the last real reading."""
        control = ActuatorControl(fallback_limits={"min": 0, "max": 350, "step": 1})
        cache = LiveLimitsCache()
        cache.resolve("number.x", 0.0, 200.0, 1.0, control)   # first: live max = 200
        limits = cache.resolve("number.x", 0.0, None, 1.0, control)   # now unavailable
        assert limits.max == 200.0   # NOT the profile's fallback of 350

    def test_no_prior_reading_falls_back_to_profile_default(self):
        control = ActuatorControl(fallback_limits={"min": 0, "max": 350, "step": 1})
        cache = LiveLimitsCache()
        limits = cache.resolve("number.x", 0.0, None, 1.0, control)
        assert limits.max == 350.0

    def test_max_can_still_rise_on_a_later_good_reading(self):
        control = ActuatorControl(fallback_limits={"min": 0, "max": 350, "step": 1})
        cache = LiveLimitsCache()
        cache.resolve("number.x", 0.0, 100.0, 1.0, control)
        limits = cache.resolve("number.x", 0.0, 250.0, 1.0, control)
        assert limits.max == 250.0

    def test_separate_entities_do_not_share_cache_state(self):
        control = ActuatorControl(fallback_limits={"min": 0, "max": 350, "step": 1})
        cache = LiveLimitsCache()
        cache.resolve("number.a", 0.0, 100.0, 1.0, control)
        limits_b = cache.resolve("number.b", 0.0, None, 1.0, control)
        assert limits_b.max == 350.0   # b's own fallback, unaffected by a's cached 100
