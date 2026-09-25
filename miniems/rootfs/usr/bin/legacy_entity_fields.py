"""Maps the pre-v2.1.0 flat `Config.*_entity` fields to (class, role) pairs.

The bridge between the legacy flat-entity-field config and the new
class/role model (docs/roadmap/v3.0-geraeteprofile.md). A user's
already-configured entity, when it differs from its shipped default, becomes
a resolver "config" override (resolution source 1) – see
device_resolver.resolve_class()'s `overrides` parameter – instead of being
discarded or silently overridden by detection.

Deliberately NOT auto-derived from the Config dataclass field names: a
name-based guess (`pv_power_entity` -> `pv_power`) happens to work for most
fields here, but relying on that silently would let a future rename on
either side drift apart unnoticed. Every mapping is listed explicitly, and
`test_role_catalog.py`/`test_web_server.py` assert every value here is a
real role in the shipped roles.yaml.
"""
from __future__ import annotations

# Config field name -> role name (all in the "inverter" class – the legacy
# config has never had a separate battery slot).
INVERTER_ENTITY_FIELDS: dict[str, str] = {
    "pv_power_entity": "pv_power",
    "battery_soc_entity": "battery_soc",
    "battery_power_entity": "battery_power",
    "battery_voltage_entity": "battery_voltage",
    "grid_power_entity": "grid_power",
    "load_power_entity": "load_power",
    "battery_capacity_entity": "battery_capacity_kwh",
    "battery_state_entity": "battery_state",
    "grid_import_energy_entity": "grid_import_energy",
    "grid_import_total_entity": "grid_import_energy_total",
    "feed_in_energy_entity": "feed_in_energy",
    "feed_in_total_entity": "feed_in_energy_total",
    "load_consumption_entity": "load_consumption_energy",
    "load_consumption_total_entity": "load_consumption_energy_total",
    "today_production_entity": "today_production",
    "today_losses_entity": "today_losses",
    "power_losses_entity": "power_losses",
    "grid_charge_switch_entity": "grid_charge_enable",
    "inverter_charge_current_entity": "battery_charge_limit",
    "battery_discharging_current_entity": "battery_discharge_limit",
}


def inverter_overrides(config: object, defaults: object) -> dict[str, str]:
    """`entity_overrides["inverter.<role>"]` for every legacy field that
    differs from its shipped default – see module docstring. `config` and
    `defaults` are both `Config` instances (the live one and a fresh
    `Config()`); kept as plain objects here rather than importing
    config_loader, so this module has no dependency of its own.
    """
    overrides: dict[str, str] = {}
    for field_name, role in INVERTER_ENTITY_FIELDS.items():
        value = getattr(config, field_name, "") or ""
        default = getattr(defaults, field_name, "") or ""
        if value and value != default:
            overrides[f"inverter.{role}"] = value
    return overrides
