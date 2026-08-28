"""Loads and matches device profiles (profiles/**/*.yaml) against the HA
device registry.

See docs/roadmap/v3.0-geraeteprofile.md. Deliberately does NOT read
Solarman's `inverter_definitions/` at runtime (see that doc's revision
note) – profiles are miniEMS's own, authored offline, optionally derived
from Solarman as a starting point (see profiles/SOURCES.md for licensing).

`model` is intentionally never required to be non-empty on its own and never
glob-matched: HA's device registry `model` field can be a literal glob
string copied out of an upstream integration's own definition file (Deye's
Solarman devices report `model: "SG0*LP3"` verbatim), a firmware version, or
null – see device_registry.py's module docstring for the live-verified
examples. A profile needs `match.manufacturer` and at least one of
`match.model` / `match.entity_hints`.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from device_registry import RegistryDevice, RegistrySnapshot
from energy_dashboard import SignSpec

_LOGGER = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).parent / "profiles"


@dataclass(frozen=True)
class RoleHint:
    """Where to look for one role's entity within a matched device's
    entities – translation_key candidates, tried in order."""

    translation_keys: tuple[str, ...]


@dataclass(frozen=True)
class ActuatorControl:
    """`control.<role>` from the profile – how to translate the EMS's
    Watt-based request into this actuator's native value. Interpreted by
    device_control.py (a later step); this module only parses it."""

    quantity: str = ""             # "power" | "boolean"
    domain: str = ""               # "number" | "switch" | "select"
    actuator_unit: str = ""        # "A" | "W" | "%" (quantity == "power")
    via: str = ""                  # "native_power" | "dc_current" | "ac_current" | "percent_of_rated"
    voltage_role: str = ""
    voltage_fallback_v: float = 0.0
    phases_role: str = ""
    voltage_fixed_v: float = 0.0
    rated_power_w: float = 0.0
    limits_from: str = ""          # "entity_attributes"
    fallback_limits: dict[str, float] = field(default_factory=dict)
    on: dict[str, Any] = field(default_factory=dict)    # quantity == "boolean"
    off: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceProfile:
    profile_id: str
    class_name: str
    manufacturer: str
    models: tuple[str, ...] = ()
    integration: str = ""
    entity_hints: tuple[str, ...] = ()
    roles: dict[str, RoleHint] = field(default_factory=dict)
    signs: dict[str, SignSpec] = field(default_factory=dict)
    control: dict[str, ActuatorControl] = field(default_factory=dict)
    modes: dict[str, dict[str, Any]] = field(default_factory=dict)
    caveats: tuple[str, ...] = ()
    verified: bool = False
    derived_from: str = ""

    def matches_device(self, device: RegistryDevice) -> bool:
        """Exact `(manufacturer, model)` match only – see module docstring
        on why `model` is never glob-matched. A profile without any
        `models` never matches on model alone (entity_hints-only profiles
        are scored separately in match_profiles())."""
        if device.manufacturer != self.manufacturer:
            return False
        if not self.models:
            return False
        return device.model in self.models


def _load_one(path: Path) -> DeviceProfile | None:
    try:
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.error("Failed to load device profile %s: %s", path, exc)
        return None

    match = raw.get("match") or {}
    manufacturer = match.get("manufacturer")
    models = match.get("model")
    entity_hints = tuple(match.get("entity_hints") or [])
    if not manufacturer or (not models and not entity_hints):
        _LOGGER.error(
            "Device profile %s rejected: match.manufacturer and at least one of "
            "match.model / match.entity_hints are required", path,
        )
        return None
    if isinstance(models, str):
        models = [models]

    roles = {
        role_name: RoleHint(translation_keys=tuple(spec.get("translation_key") or []))
        for role_name, spec in (raw.get("roles") or {}).items()
        if isinstance(spec, dict)
    }
    signs = {
        role_name: SignSpec(mode=spec.get("mode", "unsigned"), positive=spec.get("positive"))
        for role_name, spec in (raw.get("signs") or {}).items()
        if isinstance(spec, dict)
    }
    control = {
        role_name: ActuatorControl(
            quantity=spec.get("quantity", ""),
            domain=spec.get("domain", ""),
            actuator_unit=spec.get("actuator_unit", ""),
            via=spec.get("via", ""),
            voltage_role=spec.get("voltage_role", ""),
            voltage_fallback_v=float(spec.get("voltage_fallback_v") or 0),
            phases_role=spec.get("phases_role", ""),
            voltage_fixed_v=float(spec.get("voltage_fixed_v") or 0),
            rated_power_w=float(spec.get("rated_power_w") or 0),
            limits_from=spec.get("limits_from", ""),
            fallback_limits=spec.get("fallback_limits") or {},
            on=spec.get("on") or {},
            off=spec.get("off") or {},
        )
        for role_name, spec in (raw.get("control") or {}).items()
        if isinstance(spec, dict)
    }

    return DeviceProfile(
        profile_id=raw.get("id", path.stem),
        class_name=raw.get("class", ""),
        manufacturer=manufacturer,
        models=tuple(models or ()),
        integration=match.get("integration", ""),
        entity_hints=entity_hints,
        roles=roles,
        signs=signs,
        control=control,
        modes=raw.get("modes") or {},
        caveats=tuple(raw.get("caveats") or []),
        verified=bool(raw.get("verified", False)),
        derived_from=raw.get("derived_from", ""),
    )


def load_profiles(directory: Path | str | None = None) -> list[DeviceProfile]:
    """Load every `*.yaml` under `profiles/<class>/*.yaml`. A directory that
    doesn't exist yields an empty list (clean, not an error)."""
    d = Path(directory) if directory is not None else _DEFAULT_DIR
    if not d.is_dir():
        return []
    profiles: list[DeviceProfile] = []
    for path in sorted(d.rglob("*.yaml")):
        profile = _load_one(path)
        if profile is not None:
            profiles.append(profile)
    return profiles


@dataclass(frozen=True)
class ProfileMatch:
    profile: DeviceProfile
    device: RegistryDevice
    strong: bool                # (manufacturer, model) match AND all entity_hints present
    resolved_role_count: int    # how many of the profile's roles resolve on this device


def match_profiles(
    profiles: list[DeviceProfile], class_name: str, registry: RegistrySnapshot
) -> list[ProfileMatch]:
    """Every (profile, device) pairing for `class_name` whose manufacturer/
    model matches (or, absent a model match, whose entity_hints are all
    present), scored by strength and by how many roles resolve. See
    device_resolver.py for how ties/ambiguity are handled – this function
    only enumerates and scores, it never picks a winner.
    """
    candidates: list[ProfileMatch] = []
    for profile in profiles:
        if profile.class_name != class_name:
            continue
        for device in registry.devices.values():
            device_entities = registry.entities_of(device.device_id)
            entity_keys = {e.translation_key for e in device_entities if e.translation_key}

            model_match = profile.matches_device(device)
            hints_match = bool(profile.entity_hints) and all(
                h in entity_keys for h in profile.entity_hints
            )
            if not model_match and not hints_match:
                continue

            resolved = 0
            for hint in profile.roles.values():
                if any(k in entity_keys for k in hint.translation_keys):
                    resolved += 1

            candidates.append(ProfileMatch(
                profile=profile,
                device=device,
                strong=model_match and (not profile.entity_hints or hints_match),
                resolved_role_count=resolved,
            ))
    return candidates
