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
