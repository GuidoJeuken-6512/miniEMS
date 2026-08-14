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

from const import (
    BATTERY_MAX_CURRENT_A,
    CONFIG_FILE,
    CONFIG_SCHEMA_VERSION,
    FORECAST_MAX_AGE_SEC,
    OPTIONS_FILE,
    PRICE_MAX_AGE_SEC,
    SENSOR_MAX_AGE_SEC,
)
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
    # Battery limits are CURRENT (A) on the Deye, not power (W) – see const.BATTERY_MAX_CURRENT_A
    inverter_charge_current_entity: str = "number.deye8k_battery_max_charging_current"
    battery_max_charge_current_a: int = 185
    battery_max_discharge_current_a: int = 185
    # Forecast / Prediction (Phase 5)
    weather_entity: str = "weather.openweathermap"
    # Solcast PV Forecast entities (Phase 6)
    solcast_today_entity: str = "sensor.solcast_pv_forecast_prognose_heute"
    solcast_tomorrow_entity: str = "sensor.solcast_pv_forecast_prognose_morgen"
    solcast_remaining_today_entity: str = "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute"
    # Grid charge control via switch + discharge power entity (Phase 6)
    grid_charge_switch_entity: str = "switch.deye8k_battery_grid_charging"
    battery_discharging_current_entity: str = "number.deye8k_battery_max_discharging_current"
    # NOTE: default_discharge_power_w (185) was retired. The 185 came from the
    # inverter's ampere limit, not watts – at ~48 V that is ≈8.9 kW, i.e. full
    # power, never a throttle. Discharge now uses battery_max_discharge_current_a.
    # Feed-in tariff and fixed price tariff (Phase 6)
    feed_in_tariff_eur_kwh: float = 0.08
    fix_price: float = 0.30
    # Feed-in energy sensor – daily total from inverter (optional; falls back to calculated)
    feed_in_energy_entity: str = "sensor.deye8k_today_energy_export"
    # Grid import energy sensor – daily total from inverter (optional; falls back to calculated)
    grid_import_energy_entity: str = "sensor.deye8k_today_energy_import"
    # --- Scenario 2: Grid-charge cost / efficiency / ROI (optional; enables bilanz-based calculation) ---
    # Daily battery charge/discharge totals from inverter
    battery_charge_entity: str = "sensor.deye8k_today_battery_charge"
    battery_discharge_entity: str = "sensor.deye8k_today_battery_discharge"
    # Battery capacity sensor – replaces fixed battery_capacity_kwh when set
    battery_capacity_entity: str = "sensor.deye8k_battery_capacity"
    # Battery state enum sensor (charging / discharging / idle)
    battery_state_entity: str = "sensor.deye8k_battery_state"
    # PV production and losses – required for inverter efficiency calculation
    today_production_entity: str = "sensor.deye8k_today_production"
    today_losses_entity: str = "sensor.deye8k_today_losses"
    # Real-time power losses (W) – for live efficiency display
    power_losses_entity: str = "sensor.deye8k_power_losses"
    # --- Grid-friendly PV strategy (Phase 7) ---
    # Master switch. OFF = behave exactly as before (charge on any PV surplus).
    # Honours battery_control_simulation like every other inverter action.
    pv_export_priority_enabled: bool = False
    # Start charging once remaining_forecast <= free_capacity * factor.
    # >1 charges earlier (safer against an over-optimistic forecast).
    pv_charge_margin_factor: float = 1.2
    # Deadband around that trigger so the mode cannot flap (0.10 = ±10 %)
    pv_charge_hysteresis_frac: float = 0.10
    # Never withhold charging from a battery below this SoC
    pv_export_min_soc_pct: int = 30
    # Local hour from which the battery always charges, whatever the forecast says
    pv_charge_backstop_hour: int = 14
    # Charge current while holding (0 A = block charging entirely)
    export_hold_charge_current_a: int = 0
    # A new mode must be requested continuously for this long before it applies
    mode_dwell_sec: int = 300
    # Leave PROTECT_BATTERY only above battery_min_soc + this
    battery_soc_hysteresis_pct: int = 2
    # Deadband on free capacity before grid charging is considered worthwhile
    grid_charge_min_free_kwh: float = 1.0
    # No-forecast fallback window in which grid charging is still allowed
    grid_charge_dark_start_hour: int = 21
    grid_charge_dark_end_hour: int = 6
    # Sensor staleness limits (seconds); see const.py for the rationale
    sensor_max_age_sec: int = SENSOR_MAX_AGE_SEC
    forecast_max_age_sec: int = FORECAST_MAX_AGE_SEC
    price_max_age_sec: int = PRICE_MAX_AGE_SEC
    # Fixed daily base/standing charge (€/day) added on top of energy costs
    daily_base_price_eur: float = 0.0
    # Average discharge tariff for ROI calculation (€/kWh); 0 = auto-derive from price tiers
    avg_discharge_tariff_eur_kwh: float = 0.0

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
        scenario2 = [
            e for e in [
                self.battery_charge_entity,
                self.battery_discharge_entity,
                self.battery_capacity_entity,
                self.battery_state_entity,
                self.today_production_entity,
                self.today_losses_entity,
                self.power_losses_entity,
            ]
            if e
        ]
        return base + solcast + optional + scenario2


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
    "inverter_discharge_power_entity": "battery_discharging_current_entity",
    "battery_discharging_power_entity": "battery_discharging_current_entity",
    "inverter_charge_power_entity": "inverter_charge_current_entity",
}


def _validate(cfg: "Config") -> None:
    """Clamp values that would be rejected by, or unsafe for, the inverter.

    Battery limits are written straight into HA `number` entities whose range
    is 0–BATTERY_MAX_CURRENT_A. A hand-edited config (or a value carried over
    from the old watt-based fields) must never reach the inverter unclamped.
    """
    for field_name in ("battery_max_charge_current_a", "battery_max_discharge_current_a"):
        value = getattr(cfg, field_name)
        clamped = max(0, min(BATTERY_MAX_CURRENT_A, int(value)))
        if clamped != value:
            _LOGGER.warning(
                "%s = %r is out of range (0–%d A) – clamped to %d",
                field_name, value, BATTERY_MAX_CURRENT_A, clamped,
            )
            setattr(cfg, field_name, clamped)

    if cfg.battery_min_soc >= cfg.battery_max_soc:
        _LOGGER.warning(
            "battery_min_soc (%d) >= battery_max_soc (%d) – battery control disabled",
            cfg.battery_min_soc, cfg.battery_max_soc,
        )
        cfg.battery_control_enabled = False

    # --- grid-friendly PV strategy -------------------------------------
    # The export hold must never be able to strand the battery empty.
    if cfg.pv_export_min_soc_pct <= cfg.battery_min_soc:
        new = min(cfg.battery_min_soc + 10, cfg.battery_max_soc)
        _LOGGER.warning(
            "pv_export_min_soc_pct (%d) must exceed battery_min_soc (%d) – raised to %d",
            cfg.pv_export_min_soc_pct, cfg.battery_min_soc, new,
        )
        cfg.pv_export_min_soc_pct = new

    clamps = (
        ("pv_charge_margin_factor", 0.5, 3.0),
        ("pv_charge_hysteresis_frac", 0.0, 0.5),
        ("pv_charge_backstop_hour", 0, 23),
        ("grid_charge_dark_start_hour", 0, 23),
        ("grid_charge_dark_end_hour", 0, 23),
        ("mode_dwell_sec", 0, 3600),
        ("export_hold_charge_current_a", 0, BATTERY_MAX_CURRENT_A),
    )
    for name, lo, hi in clamps:
        value = getattr(cfg, name)
        clamped = max(lo, min(hi, value))
        if clamped != value:
            _LOGGER.warning("%s = %r is out of range (%s–%s) – clamped to %s",
                            name, value, lo, hi, clamped)
            setattr(cfg, name, clamped)

    if cfg.pv_export_priority_enabled and not cfg.battery_control_enabled:
        _LOGGER.warning(
            "pv_export_priority_enabled has no effect while battery_control_enabled is off"
        )


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

    _validate(cfg)

    _LOGGER.info(
        "Config loaded (v%d): %d entities monitored",
        CURRENT_VERSION,
        len(cfg.monitored_entities),
    )
    return cfg
