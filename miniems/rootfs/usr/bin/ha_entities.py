"""C7 – entity picker and validation on save (docs/roadmap/gesamtplan.md, C7).

The settings page offers, for every entity field, the matching HA entities
(filtered by domain and unit) and refuses to save an entity that does not
exist or has the wrong domain. A sensor that is merely unavailable right now
is accepted – it may come back.

``FIELD_FILTERS`` says which domains (and, for sensors, which units) a field
accepts. Fields not listed accept any entity of any domain.

The entity list comes from HA's REST API (GET /api/states via the
supervisor), cached for CACHE_SEC.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

CORE_STATES_URL = "http://supervisor/core/api/states"
CACHE_SEC = 60

_POWER = ("W", "kW")
_ENERGY = ("kWh", "Wh", "MWh")
_TEMP = ("°C", "K")

# field → (domains, units or None)
FIELD_FILTERS: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {
    "pv_power_entity": (("sensor",), _POWER),
    "battery_power_entity": (("sensor",), _POWER),
    "grid_power_entity": (("sensor",), _POWER),
    "load_power_entity": (("sensor",), _POWER),
    "battery_soc_entity": (("sensor",), ("%",)),
    "battery_voltage_entity": (("sensor",), ("V",)),
    "electricity_price_entity": (("sensor",), None),
    "inverter_charge_current_entity": (("number",), None),
    "inverter_grid_charge_current_entity": (("number",), None),
    "battery_discharging_current_entity": (("number",), None),
    "grid_charge_switch_entity": (("switch", "select"), None),
    "grid_export_limit_entity": (("number",), None),
    "feed_in_energy_entity": (("sensor",), _ENERGY),
    "grid_import_energy_entity": (("sensor",), _ENERGY),
    "load_consumption_entity": (("sensor",), _ENERGY),
    "grid_import_total_entity": (("sensor",), _ENERGY),
    "feed_in_total_entity": (("sensor",), _ENERGY),
    "load_consumption_total_entity": (("sensor",), _ENERGY),
    "battery_charge_entity": (("sensor",), _ENERGY),
    "battery_discharge_entity": (("sensor",), _ENERGY),
    "battery_capacity_entity": (("sensor",), _ENERGY),
    "weather_entity": (("weather",), None),
    "outdoor_temp_entity": (("sensor",), _TEMP),
    "boil_temp_entity": (("sensor",), _TEMP),
    "hw_target_entity": (("sensor", "number"), _TEMP),
    "hw_setpoint_entity": (("number",), None),
    "hw_workday_entity": (("binary_sensor", "input_boolean"), None),
    "hw_thermal_energy_entity": (("sensor",), _ENERGY),
    "feed_in_price_entity": (("sensor",), None),
    "grid_dimming_entity": (("binary_sensor",), None),
    "ev_charge_power_entity": (("sensor",), _POWER),
    "ev_plugged_entity": (("binary_sensor", "sensor"), None),
    "ev_status_entity": (("sensor",), None),
    "ev_enable_entity": (("switch",), None),
    "ev_current_entity": (("number",), None),
    "ev_phases_entity": (("select", "number", "input_select"), None),
    "ev_soc_entity": (("sensor",), ("%",)),
    "ev_mode_entity": (("input_select", "select"), None),
    "ev_target_soc_entity": (("input_number", "number"), None),
    "ev_departure_entity": (("input_datetime", "datetime", "time"), None),
    "heating_offset_entity": (("number",), None),
    "heating_surplus_entity": (("number",), None),
    "heating_room_temp_entity": (("sensor",), _TEMP),
}
# Comma-separated lists: every element is checked with this filter.
LIST_FILTERS: dict[str, tuple[tuple[str, ...], tuple[str, ...] | None]] = {
    "phase_current_entities": (("sensor",), ("A",)),
    "phase_power_entities": (("sensor",), _POWER),
    "heat_pump_power_entity": (("sensor",), _POWER),
}


def entity_fields() -> list[str]:
    return sorted(set(FIELD_FILTERS) | set(LIST_FILTERS))


def _split(raw: Any) -> list[str]:
    return [e.strip() for e in str(raw or "").split(",") if e.strip()]


def matches(state: dict, domains: tuple[str, ...], units: tuple[str, ...] | None) -> bool:
    entity_id = state.get("entity_id", "")
    if entity_id.split(".", 1)[0] not in domains:
        return False
    if units is None:
        return True
    unit = (state.get("attributes") or {}).get("unit_of_measurement")
    # A sensor without a unit (not yet reported) is offered, a wrong unit not.
    return unit is None or unit in units


def candidates(field: str, states: list[dict]) -> list[dict]:
    flt = FIELD_FILTERS.get(field) or LIST_FILTERS.get(field)
    out = []
    for st in states:
        if flt is None or matches(st, *flt):
            out.append({"entity_id": st.get("entity_id"),
                        "name": (st.get("attributes") or {}).get("friendly_name"),
                        "unit": (st.get("attributes") or {}).get("unit_of_measurement"),
                        "state": st.get("state")})
    out.sort(key=lambda x: x["entity_id"] or "")
    return out


def validate(values: dict[str, Any], states: list[dict]) -> list[dict]:
    """Problems with the entity fields in ``values`` (empty = all fine)."""
    by_id = {s.get("entity_id"): s for s in states}
    problems: list[dict] = []

    def check(field: str, entity: str, flt) -> None:
        st = by_id.get(entity)
        if st is None:
            problems.append({"field": field, "entity_id": entity, "problem": "does not exist"})
            return
        domains, units = flt
        if entity.split(".", 1)[0] not in domains:
            problems.append({"field": field, "entity_id": entity,
                             "problem": f"wrong domain (expected {', '.join(domains)})"})
            return
        unit = (st.get("attributes") or {}).get("unit_of_measurement")
        if units is not None and unit is not None and unit not in units:
            problems.append({"field": field, "entity_id": entity,
                             "problem": f"unit {unit} (expected {', '.join(units)})"})

    for field, flt in FIELD_FILTERS.items():
        entity = str(values.get(field) or "").strip()
        if entity:
            check(field, entity, flt)
    for field, flt in LIST_FILTERS.items():
        for entity in _split(values.get(field)):
            check(field, entity, flt)
    return problems


class EntityCache:
    """All HA states, fetched on demand and cached for CACHE_SEC."""

    def __init__(self, token: str) -> None:
        self._token = token
        self._states: list[dict] | None = None
        self._at = 0.0

    async def states(self) -> list[dict] | None:
        if self._states is not None and time.monotonic() - self._at < CACHE_SEC:
            return self._states
        if not self._token:
            return None
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    CORE_STATES_URL, headers={"Authorization": f"Bearer {self._token}"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        _LOGGER.warning("Entity list: HTTP %d", resp.status)
                        return self._states
                    data = await resp.json()
        except Exception as exc:
            _LOGGER.warning("Entity list unavailable: %s", exc)
            return self._states
        if isinstance(data, list):
            self._states = data
            self._at = time.monotonic()
        return self._states
