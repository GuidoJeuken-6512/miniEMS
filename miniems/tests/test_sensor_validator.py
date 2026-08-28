"""SensorValidator: spike detection with two-tick confirmation."""
from sensor_validator import SensorValidator


def test_first_reading_always_accepted():
    v = SensorValidator()
    assert v.validate("sensor.x", 5000.0) == 5000.0


def test_small_change_accepted():
    v = SensorValidator()
    v.validate("sensor.x", 1000.0)
    # 100 W / 10% change – below both thresholds
    assert v.validate("sensor.x", 1100.0) == 1100.0


def test_large_absolute_and_relative_change_rejected():
    v = SensorValidator()
    v.validate("sensor.x", 1000.0)
    # +900 W, +90% – spike
    assert v.validate("sensor.x", 1900.0) is None


def test_large_absolute_but_small_relative_change_accepted():
    """Only a spike if BOTH the absolute AND relative thresholds are exceeded."""
    v = SensorValidator()
    v.validate("sensor.x", 10000.0)
    # +600 W is > 500 W absolute, but only 6% relative – not a spike
    assert v.validate("sensor.x", 10600.0) == 10600.0


def test_large_relative_but_small_absolute_change_accepted():
    v = SensorValidator()
    v.validate("sensor.x", 100.0)
    # +80 W is 80% relative, but only 80 W absolute (< 500 W) – not a spike
    assert v.validate("sensor.x", 180.0) == 180.0


def test_single_spike_does_not_move_baseline():
    v = SensorValidator()
    v.validate("sensor.x", 1000.0)
    assert v.validate("sensor.x", 2000.0) is None   # rejected, pending
    # Next reading back near the original baseline is accepted again –
    # the rejected spike above never became the new reference.
    assert v.validate("sensor.x", 1050.0) == 1050.0


def test_two_consecutive_agreeing_spikes_confirm_new_baseline():
    v = SensorValidator()
    v.validate("sensor.x", 1000.0)
    assert v.validate("sensor.x", 50.0) is None      # first rejection, pending
    # Second reading close to the pending candidate confirms the shift
    assert v.validate("sensor.x", 55.0) == 55.0
    # The new baseline is now adopted – a nearby third reading is accepted
    assert v.validate("sensor.x", 60.0) == 60.0


def test_pending_cleared_after_accepted_reading():
    v = SensorValidator()
    v.validate("sensor.x", 1000.0)
    v.validate("sensor.x", 2000.0)          # rejected, pending = 2000
    v.validate("sensor.x", 1010.0)          # accepted, pending cleared
    # A later spike must start a fresh pending cycle, not reuse 2000.0
    assert v.validate("sensor.x", 3000.0) is None
    assert v.validate("sensor.x", 1020.0) == 1020.0   # would fail if 2000 still pending


def test_independent_entities_tracked_separately():
    v = SensorValidator()
    v.validate("sensor.a", 1000.0)
    v.validate("sensor.b", 5.0)
    assert v.validate("sensor.a", 1900.0) is None
    assert v.validate("sensor.b", 6.0) == 6.0
