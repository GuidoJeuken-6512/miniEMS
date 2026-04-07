"""Load add-on configuration with persistent storage and migration support.

Priority (highest → lowest):
  1. /data/options.json — supervisor-managed UI values that differ from defaults
     (user actively changed them in the HA config page)
  2. /data/config.json  — previously persisted values
     (survives options.json resets caused by supervisor reloads / addon updates)
  3. Dataclass defaults

On every startup the merged result is written back to /data/config.json so the
user's settings are never lost even if options.json is reset to defaults.
"""
import json
import logging
import os
from dataclasses import dataclass, fields

from const import CONFIG_FILE, CONFIG_SCHEMA_VERSION, OPTIONS_FILE
from migration import migrate

_LOGGER = logging.getLogger(__name__)

CURRENT_VERSION = CONFIG_SCHEMA_VERSION


@dataclass
class Config:
    # Deye Inverter Entities
    pv_power_entity: str = "sensor.deye_pv_total_power"
    battery_soc_entity: str = "sensor.deye_battery_soc"
    battery_power_entity: str = "sensor.deye_battery_power"
    grid_power_entity: str = "sensor.deye_grid_power"
    load_power_entity: str = "sensor.deye_load_power"
    battery_capacity_kwh: float = 10.0
    battery_min_soc: int = 15
    battery_max_soc: int = 95
    # Authentication
    long_lived_token: str = ""
    # Octopus Energy Entities
    electricity_price_entity: str = "sensor.octopus_energy_electricity_current_rate"
    cheap_rate_threshold_eur: float = 0.10
    medium_rate_threshold_eur: float = 0.20
    # EMS Parameters
    pv_surplus_threshold_w: int = 200
    update_interval_sec: int = 30
    event_log_retention_days: int = 30
    # Battery Control (Phase 2)
    battery_control_enabled: bool = False
    battery_control_simulation: bool = True
    inverter_charge_power_entity: str = "number.deye_battery_charging_power"
    battery_max_charge_power_w: int = 3000
    battery_max_discharge_power_w: int = 3000
    # Forecast / Prediction (Phase 5)
    weather_entity: str = "weather.openweathermap"
    # Solcast PV Forecast entities (Phase 6)
    solcast_today_entity: str = "sensor.solcast_pv_forecast_prognose_heute"
    solcast_tomorrow_entity: str = "sensor.solcast_pv_forecast_prognose_morgen"
    solcast_remaining_today_entity: str = "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute"
    # Grid charge control via switch + discharge power entity (Phase 6)
    grid_charge_switch_entity: str = "switch.deye8k_battery_grid_charging"
    battery_discharging_power_entity: str = "number.deye8k_battery_discharging_power"
    default_discharge_power_w: int = 185
    # Feed-in tariff and fixed price tariff (Phase 6)
    feed_in_tariff_eur_kwh: float = 0.08
    fix_price: float = 0.30
    # Feed-in energy sensor – daily total from inverter (optional; falls back to calculated)
    feed_in_energy_entity: str = "sensor.deye8k_today_energy_export"
    # Grid import energy sensor – daily total from inverter (optional; falls back to calculated)
    grid_import_energy_entity: str = "sensor.deye8k_today_energy_import"

    @property
    def monitored_entities(self) -> list[str]:
        base = [
            self.pv_power_entity,
            self.battery_soc_entity,
            self.battery_power_entity,
            self.grid_power_entity,
            self.load_power_entity,
            self.electricity_price_entity,
        ]
        solcast = [
            e for e in [
                self.solcast_today_entity,
                self.solcast_tomorrow_entity,
                self.solcast_remaining_today_entity,
            ]
            if e
        ]
        optional = [e for e in [self.feed_in_energy_entity, self.grid_import_energy_entity] if e]
        return base + solcast + optional


def _defaults() -> dict:
    cfg = Config()
    return {f.name: getattr(cfg, f.name) for f in fields(cfg)}


def _load_json(path: str) -> dict:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f) or {}
    except (OSError, json.JSONDecodeError) as exc:
        _LOGGER.error("Failed to read %s: %s – ignored", path, exc)
        return {}


_OPTIONS_RENAMES: dict[str, str] = {
    # old options.json key → current Config field name
    "inverter_discharge_power_entity": "battery_discharging_power_entity",
}


def load_config() -> Config:
    """Load, merge, migrate and persist configuration."""
    defs = _defaults()

    # Load both sources
    stored = migrate(_load_json(CONFIG_FILE))
    options = _load_json(OPTIONS_FILE)

    # Carry forward renamed keys that may only exist in options.json
    for old_key, new_key in _OPTIONS_RENAMES.items():
        if old_key in options and new_key not in options:
            options[new_key] = options[old_key]
            _LOGGER.info("options.json rename: %s → %s = %r", old_key, new_key, options[new_key])

    merged: dict = {}
    for key in defs:
        opt_val = options.get(key)  # None = key not present in options.json

        # options.json wins only when the key is explicitly present AND differs
        # from the dataclass default (i.e. the user actively changed it via UI).
        # A missing key (None) must NOT win over a previously saved value.
        if opt_val is not None and opt_val != defs[key]:
            merged[key] = opt_val
        else:
            # Fall back to persisted value; use default if not yet stored
            merged[key] = stored.get(key, defs[key])

    merged["_version"] = CURRENT_VERSION

    # Persist so the next startup survives an options.json reset
    try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as f:
            json.dump(merged, f, indent=2)
    except OSError as exc:
        _LOGGER.error("Failed to write %s: %s", CONFIG_FILE, exc)

    cfg = Config()
    for key, value in merged.items():
        if not key.startswith("_") and hasattr(cfg, key):
            setattr(cfg, key, value)

    _LOGGER.info(
        "Config loaded (v%d): %d entities monitored",
        CURRENT_VERSION,
        len(cfg.monitored_entities),
    )
    return cfg
