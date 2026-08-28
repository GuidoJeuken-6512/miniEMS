"""Parses Home Assistant's energy-dashboard preferences into role candidates.

Input is the raw result of `energy/get_prefs` (see `ha_ws_api.get_energy_prefs()`).
Pure `dict -> dataclass`, no I/O – part of the device-detection groundwork,
see docs/roadmap/v3.0-geraeteprofile.md, "Auflösungskette", source 2.

This is deliberately the *seed*, not the full picture: `stat_energy_from` is
a kWh sensor, and the power link (`stat_rate`/`power_config`) is optional and
often absent for sources that only report energy. What the energy dashboard
reliably contributes even then is the one thing hardest to get right
anywhere else – the SIGN CONVENTION (see SignSpec) – plus the battery's
usable capacity and the grid price entity. Most role bindings still come
from the device-profile matcher (source 3 in the resolution chain), not from
here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SignSpec:
    """How a power reading's sign maps to a physical direction.

    Mirrors HA's own `PowerConfig` shape (verified in
    homeassistant/components/energy/data.py) so an energy-dashboard entry can
    be transcribed losslessly instead of re-guessed:

    - "unsigned": the entity never goes negative (e.g. solar production).
    - "signed":   positive means `positive` (e.g. "import", "discharge").
    - "inverted": same as signed, but the entity's own sign is the *opposite*
                  of the physical direction – HA's `stat_rate_inverted`.
    - "split":    two separate entities, one per direction, not one signed
                  value – HA's `stat_rate_from`/`stat_rate_to`. When parsed
                  from an energy-dashboard payload the two entity ids live in
                  EnergyDashboardMap.split_candidates, not in `candidates`.
                  When *declared* in roles.yaml (role_catalog.py), the two
                  directions are instead two other role names – `from_role`/
                  `to_role` below.
    """
    mode: str  # "unsigned" | "signed" | "inverted" | "split"
    positive: str | None = None   # "import" | "export" | "charge" | "discharge"
    from_role: str | None = None  # split only, roles.yaml usage
    to_role: str | None = None    # split only, roles.yaml usage


@dataclass(frozen=True)
class EnergyDashboardMap:
    """Everything usable, extracted from one `energy/get_prefs` payload."""

    # measurement name -> entity_id, for a single power-rate/SoC/price sensor.
    candidates: dict[str, str] = field(default_factory=dict)
    # measurement name -> SignSpec, for every directional entry above (and
    # every split entry below).
    signs: dict[str, SignSpec] = field(default_factory=dict)
    # measurement name -> (import_entity_id, export_entity_id), for sources
    # HA reports as two separate entities rather than one signed one.
    split_candidates: dict[str, tuple[str, str]] = field(default_factory=dict)
    battery_capacity_kwh: float | None = None


def parse_energy_prefs(payload: dict[str, Any] | None) -> EnergyDashboardMap:
    """Build an EnergyDashboardMap from a raw `energy/get_prefs` result.

    A `None`/empty payload yields an empty map, not an error – "no energy
    dashboard configured" is a normal state on a fresh HA install and must
    fall through to the next resolution source cleanly.
    """
    if not payload:
        return EnergyDashboardMap()

    candidates: dict[str, str] = {}
    signs: dict[str, SignSpec] = {}
    split: dict[str, tuple[str, str]] = {}
    capacity: float | None = None

    for source in payload.get("energy_sources") or []:
        if not isinstance(source, dict):
            continue
        kind = source.get("type")

        if kind == "solar":
            entity = source.get("stat_rate")
            if entity:
                candidates["pv_power"] = entity
                signs["pv_power"] = SignSpec(mode="unsigned")

        elif kind == "battery":
            entity, sign = _rate_and_sign(source, positive="discharge")
            if entity and sign is not None:
                candidates["battery_power"] = entity
                signs["battery_power"] = sign
            soc = source.get("stat_soc")
            if soc:
                candidates["battery_soc"] = soc
            cap = source.get("capacity")
            if isinstance(cap, (int, float)):
                capacity = float(cap)

        elif kind == "grid":
            power_config = source.get("power_config") or {}
            from_entity = power_config.get("stat_rate_from")
            to_entity = power_config.get("stat_rate_to")
            if from_entity and to_entity:
                split["grid_power"] = (from_entity, to_entity)
                signs["grid_power"] = SignSpec(mode="split")
            else:
                entity, sign = _rate_and_sign(source, positive="import")
                if entity and sign is not None:
                    candidates["grid_power"] = entity
                    signs["grid_power"] = sign
            price = source.get("entity_energy_price")
            if price:
                candidates["price"] = price

    return EnergyDashboardMap(
        candidates=candidates,
        signs=signs,
        split_candidates=split,
        battery_capacity_kwh=capacity,
    )


def _rate_and_sign(
    source: dict[str, Any], *, positive: str
) -> tuple[str | None, SignSpec | None]:
    """Single-entity power-rate + its sign for a battery/grid source.

    Checked in order: `power_config.stat_rate_inverted`,
    `power_config.stat_rate`, then a legacy top-level `stat_rate` – on the
    live payload this was verified against, both the documented
    `power_config.stat_rate` and a mirrored top-level `stat_rate` carried the
    same entity id, so the fallback costs nothing and covers an
    HA version that only sets one of them.
    """
    power_config = source.get("power_config") or {}
    if power_config.get("stat_rate_inverted"):
        return power_config["stat_rate_inverted"], SignSpec(mode="inverted", positive=positive)
    if power_config.get("stat_rate"):
        return power_config["stat_rate"], SignSpec(mode="signed", positive=positive)
    if source.get("stat_rate"):
        return source["stat_rate"], SignSpec(mode="signed", positive=positive)
    return None, None
