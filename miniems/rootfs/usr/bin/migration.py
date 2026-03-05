"""Config version migrations for miniEMS.

Each _vX_to_vY function migrates the raw config dict between schema versions.
Add a new function and increment CURRENT_VERSION whenever the config schema changes.
"""
import logging

from const import CONFIG_SCHEMA_VERSION

_LOGGER = logging.getLogger(__name__)

CURRENT_VERSION = CONFIG_SCHEMA_VERSION


def migrate(data: dict) -> dict:
    """Migrate config dict to CURRENT_VERSION. Returns updated dict."""
    version = data.get("_version", 0)
    if version >= CURRENT_VERSION:
        return data

    _LOGGER.info("Migrating config v%d → v%d", version, CURRENT_VERSION)

    if version < 1:
        data = _v0_to_v1(data)

    if version < 2:
        data = _v1_to_v2(data)

    if version < 3:
        data = _v2_to_v3(data)

    if version < 4:
        data = _v3_to_v4(data)

    if version < 5:
        data = _v4_to_v5(data)

    data["_version"] = CURRENT_VERSION
    return data


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

def _v0_to_v1(data: dict) -> dict:
    """v0 → v1: rename GBP fields to EUR (currency switch)."""
    if "cheap_rate_threshold_gbp" in data and "cheap_rate_threshold_eur" not in data:
        data["cheap_rate_threshold_eur"] = data.pop("cheap_rate_threshold_gbp")
        _LOGGER.info("Migrated cheap_rate_threshold_gbp → cheap_rate_threshold_eur")
    return data


def _v1_to_v2(data: dict) -> dict:
    """v1 → v2: add battery control fields (Phase 2)."""
    defaults = {
        "battery_control_enabled": False,
        "battery_control_simulation": True,
        "inverter_work_mode_entity": "select.deye_work_mode",
        "inverter_charge_power_entity": "number.deye_battery_charging_power",
        "inverter_discharge_power_entity": "number.deye_battery_discharging_power",
        "inverter_charge_mode_charge": "Charging Priority",
        "inverter_charge_mode_selfuse": "Self-Use",
        "battery_max_charge_power_w": 3000,
        "battery_max_discharge_power_w": 3000,
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
            _LOGGER.info("Migration v1→v2: set %s = %r", key, default)
    return data


def _v2_to_v3(data: dict) -> dict:
    """v2 → v3: add forecast / prediction fields (Phase 3)."""
    defaults = {
        "outdoor_temp_entity": "",
        "openweathermap_api_key": "",
        "openweathermap_lat": 0.0,
        "openweathermap_lon": 0.0,
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
            _LOGGER.info("Migration v2→v3: set %s = %r", key, default)
    return data


def _v4_to_v5(data: dict) -> dict:
    """v4 → v5: replace sensor entity fields with single weather entity."""
    if "weather_entity" not in data:
        data["weather_entity"] = "weather.openweathermap"
        _LOGGER.info("Migration v4→v5: set weather_entity = %r", data["weather_entity"])
    for key in ("weather_temperature_entity", "weather_cloud_coverage_entity"):
        if key in data:
            data.pop(key)
            _LOGGER.info("Migration v4→v5: removed %s", key)
    return data


def _v3_to_v4(data: dict) -> dict:
    """v3 → v4: replace OWM API fields with HA sensor entity fields."""
    # Migrate outdoor_temp_entity → weather_temperature_entity
    if "weather_temperature_entity" not in data:
        old = data.pop("outdoor_temp_entity", "")
        data["weather_temperature_entity"] = old or "sensor.openweathermap_temperature"
        _LOGGER.info("Migration v3→v4: outdoor_temp_entity → weather_temperature_entity = %r", data["weather_temperature_entity"])
    # Add cloud coverage entity
    if "weather_cloud_coverage_entity" not in data:
        data["weather_cloud_coverage_entity"] = "sensor.openweathermap_cloud_coverage"
        _LOGGER.info("Migration v3→v4: set weather_cloud_coverage_entity = %r", data["weather_cloud_coverage_entity"])
    # Remove old OWM API fields
    for key in ("openweathermap_api_key", "openweathermap_lat", "openweathermap_lon"):
        if key in data:
            data.pop(key)
            _LOGGER.info("Migration v3→v4: removed %s", key)
    return data
