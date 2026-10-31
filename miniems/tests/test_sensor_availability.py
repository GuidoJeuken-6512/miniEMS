"""sensor_availability.py: transition-only logging of sensor outages."""
import logging
from datetime import datetime, timedelta

from sensor_availability import SensorAvailabilityMonitor

T0 = datetime(2026, 10, 29, 13, 0, 0)
ENTITIES = {"sensor.pv": "pv power", "sensor.load": "load power"}


def run(mon, states, sec):
    mon.update(T0 + timedelta(seconds=sec), ENTITIES, states.get,
               {"sensor.pv": "energy accounting counts 0 W meanwhile"})


def test_logs_once_after_debounce_then_recovery(caplog):
    mon = SensorAvailabilityMonitor(debounce_ticks=2)
    states = {"sensor.pv": "1200", "sensor.load": "400"}
    caplog.set_level(logging.INFO, logger="sensor_availability")
    run(mon, states, 0)
    states["sensor.pv"] = "unavailable"
    run(mon, states, 30)
    assert caplog.records == []           # one missing tick is tolerated
    run(mon, states, 60)
    run(mon, states, 90)
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "pv power (sensor.pv)" in warnings[0]
    assert "'unavailable'" in warnings[0]
    assert "since 13:00:30" in warnings[0]
    assert "counts 0 W" in warnings[0]
    assert mon.unavailable == ["sensor.pv"]

    states["sensor.pv"] = "1100"
    run(mon, states, 120)
    info = [r.getMessage() for r in caplog.records if r.levelno == logging.INFO]
    assert info == ["Sensor available again: pv power (sensor.pv) after 90 s"]
    assert mon.unavailable == []


def test_missing_entity_reported_as_not_found(caplog):
    mon = SensorAvailabilityMonitor(debounce_ticks=1)
    caplog.set_level(logging.WARNING, logger="sensor_availability")
    run(mon, {"sensor.pv": "1"}, 0)
    assert "load power (sensor.load), not found in Home Assistant" in caplog.text


def test_single_blip_is_not_logged(caplog):
    mon = SensorAvailabilityMonitor(debounce_ticks=2)
    caplog.set_level(logging.INFO, logger="sensor_availability")
    states = {"sensor.pv": "unknown", "sensor.load": "1"}
    run(mon, states, 0)
    states["sensor.pv"] = "5"
    run(mon, states, 30)
    assert caplog.records == []


def test_non_numeric_states_count_as_available(caplog):
    mon = SensorAvailabilityMonitor(debounce_ticks=1)
    caplog.set_level(logging.WARNING, logger="sensor_availability")
    mon.update(T0, {"switch.grid": "grid charge switch"}, {"switch.grid": "off"}.get)
    assert caplog.records == []
