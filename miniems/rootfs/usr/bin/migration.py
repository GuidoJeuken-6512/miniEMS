"""Config version migrations for miniEMS.

Each _vX_to_vY function migrates the raw config dict between schema versions.
Add a new function and increment CURRENT_VERSION whenever the config schema changes.
"""
import logging

from const import CONFIG_SCHEMA_VERSION, FORECAST_MAX_AGE_SEC, PRICE_MAX_AGE_SEC

_LOGGER = logging.getLogger(__name__)

CURRENT_VERSION = CONFIG_SCHEMA_VERSION


def migrate(data: dict) -> dict:
    """Migrate config dict to CURRENT_VERSION. Returns updated dict."""
    if not isinstance(data, dict):
        _LOGGER.error("Config is %s, not an object – ignoring it and using defaults",
                      type(data).__name__)
        return {"_version": CURRENT_VERSION}

    version = data.get("_version", 0)
    if not isinstance(version, int):
        # A hand-edited config (raw JSON editor) can carry a string here, which
        # would otherwise raise TypeError on the comparison below and put the
        # add-on into a permanent crash loop.
        try:
            version = int(version)
        except (TypeError, ValueError):
            _LOGGER.warning("Config _version=%r is not a number – re-running all migrations", version)
            version = 0

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

    if version < 6:
        data = _v5_to_v6(data)

    if version < 7:
        data = _v6_to_v7(data)

    if version < 8:
        data = _v7_to_v8(data)

    if version < 9:
        data = _v8_to_v9(data)

    if version < 10:
        data = _v9_to_v10(data)

    if version < 11:
        data = _v10_to_v11(data)

    if version < 12:
        data = _v11_to_v12(data)

    if version < 13:
        data = _v12_to_v13(data)

    if version < 14:
        data = _v13_to_v14(data)

    if version < 15:
        data = _v14_to_v15(data)

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
        "inverter_charge_power_entity": "number.deye_battery_charging_power",
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


def _v5_to_v6(data: dict) -> dict:
    """v5 → v6: add Solcast entities, grid charge switch, feed-in tariff, default discharge power, fix_price.

    Also carries forward renamed fields:
      inverter_discharge_power_entity → battery_discharging_power_entity
      inverter_work_mode_entity       → grid_charge_switch_entity (best-effort; different semantics)
    """
    # Carry forward renamed discharge power entity
    if "battery_discharging_power_entity" not in data and "inverter_discharge_power_entity" in data:
        data["battery_discharging_power_entity"] = data["inverter_discharge_power_entity"]
        _LOGGER.info(
            "Migration v5→v6: carried inverter_discharge_power_entity=%r → battery_discharging_power_entity",
            data["battery_discharging_power_entity"],
        )

    defaults = {
        "solcast_today_entity": "sensor.solcast_pv_forecast_prognose_heute",
        "solcast_tomorrow_entity": "sensor.solcast_pv_forecast_prognose_morgen",
        "solcast_remaining_today_entity": "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute",
        "grid_charge_switch_entity": "switch.deye8k_battery_grid_charging",
        "battery_discharging_power_entity": "number.deye8k_battery_discharging_power",
        "feed_in_tariff_eur_kwh": 0.08,
        "default_discharge_power_w": 185,
        "fix_price": 0.30,
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
            _LOGGER.info("Migration v5→v6: set %s = %r", key, default)
    return data


def _v10_to_v11(data: dict) -> dict:
    """v10 → v11: battery limits switch from power (W) to current (A).

    The Deye inverter does not expose a battery charge/discharge *power* entity
    at all – it exposes *current* limits in amperes:
        number.deye8k_battery_max_charging_current     (0–350 A)
        number.deye8k_battery_max_discharging_current  (0–350 A)

    The old *_power_w values were watts and cannot be converted without knowing
    the battery voltage, so they are dropped in favour of the inverter's rated
    current. Old entity ids are only carried over when they actually point at a
    current entity; the previous defaults pointed at entities that do not exist.
    """
    # --- entities ---------------------------------------------------------
    old_charge = str(data.pop("inverter_charge_power_entity", "") or "")
    old_discharge = str(data.pop("battery_discharging_power_entity", "") or "")

    def _pick(old: str, fallback: str, label: str) -> str:
        if old.startswith("number.") and "current" in old:
            return old
        if old:
            _LOGGER.warning(
                "Migration v10→v11: %s=%r is not a battery current entity – using %r",
                label, old, fallback,
            )
        return fallback

    if "inverter_charge_current_entity" not in data:
        data["inverter_charge_current_entity"] = _pick(
            old_charge, "number.deye8k_battery_max_charging_current",
            "inverter_charge_power_entity",
        )
        _LOGGER.info("Migration v10→v11: set inverter_charge_current_entity = %r",
                     data["inverter_charge_current_entity"])

    if "battery_discharging_current_entity" not in data:
        data["battery_discharging_current_entity"] = _pick(
            old_discharge, "number.deye8k_battery_max_discharging_current",
            "battery_discharging_power_entity",
        )
        _LOGGER.info("Migration v10→v11: set battery_discharging_current_entity = %r",
                     data["battery_discharging_current_entity"])

    # --- limits: watts are not convertible, fall back to rated current ------
    for old_key, new_key in (
        ("battery_max_charge_power_w", "battery_max_charge_current_a"),
        ("battery_max_discharge_power_w", "battery_max_discharge_current_a"),
    ):
        old_value = data.pop(old_key, None)
        if new_key not in data:
            data[new_key] = 185
            _LOGGER.info(
                "Migration v10→v11: %s=%r (W) → %s = %d (A)",
                old_key, old_value, new_key, data[new_key],
            )

    # Retired: 185 was the inverter's ampere limit, never a watt throttle.
    if data.pop("default_discharge_power_w", None) is not None:
        _LOGGER.info(
            "Migration v10→v11: removed default_discharge_power_w "
            "(discharge now uses battery_max_discharge_current_a)"
        )
    return data


def _v11_to_v12(data: dict) -> dict:
    """v11 → v12: grid-friendly PV export strategy + mode stability settings.

    Ships with pv_export_priority_enabled=False, so upgrading changes nothing
    until the user switches it on. Everything here honours the existing
    battery_control_simulation switch.
    """
    defaults = {
        "pv_export_priority_enabled": False,
        "pv_charge_margin_factor": 1.2,
        "pv_charge_hysteresis_frac": 0.10,
        "pv_export_min_soc_pct": 30,
        "pv_charge_backstop_hour": 14,
        "export_hold_charge_current_a": 0,
        "mode_dwell_sec": 300,
        "battery_soc_hysteresis_pct": 2,
        "grid_charge_min_free_kwh": 1.0,
        "grid_charge_dark_start_hour": 21,
        "grid_charge_dark_end_hour": 6,
        "sensor_max_age_sec": 300,
        "forecast_max_age_sec": 10800,
        "price_max_age_sec": 10800,
    }
    for key, default in defaults.items():
        if key not in data:
            data[key] = default
            _LOGGER.info("Migration v11→v12: set %s = %r", key, default)
    return data


def _v14_to_v15(data: dict) -> dict:
    """v14 → v15: lifetime energy counters, so the day is cut on our own clock.

    The inverter resets its daily counters on its own clock. Measured on the
    production system that is 4min54s after local midnight, and during that
    window the daily sensor still reports yesterday's closing total – which the
    Source-A override wrote straight into the new day (45.9 kWh of feed-in, in
    the measured case). It self-corrected minutes later, but kWh and cost were
    accumulated over windows offset by those minutes, and a clock skew in the
    opposite direction would have written a 0 into *yesterday* and persisted it.

    Deriving the daily figure from a monotonic lifetime counter removes both.
    """
    for field, entity in (
        ("grid_import_total_entity", "sensor.deye8k_total_energy_import"),
        ("feed_in_total_entity", "sensor.deye8k_total_energy_export"),
        ("load_consumption_total_entity", "sensor.deye8k_total_load_consumption"),
    ):
        if field not in data:
            data[field] = entity
            _LOGGER.info("Migration v14→v15: set %s = %r", field, entity)
    return data


def _v13_to_v14(data: dict) -> dict:
    """v13 → v14: give price_max_age_sec a safety margin over the longest window.

    A time-of-use tariff entity is only written at tier boundaries, so "time
    since last update" legitimately grows to the length of the longest tariff
    window. On this installation that window is exactly 6 h (06:00–12:00), and
    the threshold was exactly 21600s – threshold equal to the longest legitimate
    gap, i.e. a false "Sensor stale: Electricity price" one scheduling jitter
    away. Two minutes of clearance settle that.

    Only corrected when the value still exactly matches the old default; an
    explicitly customized value is left alone.
    """
    if data.get("price_max_age_sec") == 21600:
        data["price_max_age_sec"] = PRICE_MAX_AGE_SEC
        _LOGGER.info(
            "Migration v13→v14: price_max_age_sec was exactly the longest tariff "
            "window (21600s/6h) – raised to %ds for clearance", PRICE_MAX_AGE_SEC,
        )
    return data


def _v12_to_v13(data: dict) -> dict:
    """v12 → v13: load-consumption energy sensor (Quelle A for today_load_total_kwh).

    Before this, today_load_total_kwh (and everything derived from it,
    e.g. today_load_cost_eur) was purely tick-accumulated with no hardware
    counter to fall back on – unlike grid import/feed-in, it permanently
    under-counted across every addon restart. Measured on a live instance:
    ~1.1 kWh (≈23%) low after a single restart earlier that day.

    Also corrects forecast_max_age_sec/price_max_age_sec for configs
    migrated through v11→v12 before v2.0.1 raised those defaults from
    10800s/3h to the current, more realistic values (see const.py) – a
    config already at v12 had the old number baked in permanently and
    never picked up the new default. Only touched if the value still
    exactly matches that old hardcoded default; an explicitly customized
    value is left alone.
    """
    if "load_consumption_entity" not in data:
        data["load_consumption_entity"] = "sensor.deye8k_today_load_consumption"
        _LOGGER.info("Migration v12→v13: set load_consumption_entity = %r",
                     data["load_consumption_entity"])

    if data.get("forecast_max_age_sec") == 10800:
        data["forecast_max_age_sec"] = FORECAST_MAX_AGE_SEC
        _LOGGER.info(
            "Migration v12→v13: forecast_max_age_sec was still the old default "
            "(10800s/3h) – raised to %ds", FORECAST_MAX_AGE_SEC,
        )
    if data.get("price_max_age_sec") == 10800:
        data["price_max_age_sec"] = PRICE_MAX_AGE_SEC
        _LOGGER.info(
            "Migration v12→v13: price_max_age_sec was still the old default "
            "(10800s/3h) – raised to %ds", PRICE_MAX_AGE_SEC,
        )
    return data


def _v9_to_v10(data: dict) -> dict:
    """v9 → v10: add grid import energy sensor entity."""
    if "grid_import_energy_entity" not in data:
        data["grid_import_energy_entity"] = "sensor.deye8k_today_energy_import"
        _LOGGER.info("Migration v9→v10: set grid_import_energy_entity = %r", data["grid_import_energy_entity"])
    return data


def _v8_to_v9(data: dict) -> dict:
    """v8 → v9: add feed-in energy sensor entity."""
    if "feed_in_energy_entity" not in data:
        data["feed_in_energy_entity"] = "sensor.deye8k_today_energy_export"
        _LOGGER.info("Migration v8→v9: set feed_in_energy_entity = %r", data["feed_in_energy_entity"])
    return data


def _v7_to_v8(data: dict) -> dict:
    """v7 → v8: add event log retention setting."""
    if "event_log_retention_days" not in data:
        data["event_log_retention_days"] = 30
        _LOGGER.info("Migration v7→v8: set event_log_retention_days = 30")
    return data


def _v6_to_v7(data: dict) -> dict:
    """v6 → v7: add medium price rate threshold."""
    if "medium_rate_threshold_eur" not in data:
        data["medium_rate_threshold_eur"] = 0.20
        _LOGGER.info("Migration v6→v7: set medium_rate_threshold_eur = 0.20")
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
