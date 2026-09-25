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

from device_registry import RegistryDevice, RegistryEntity, RegistrySnapshot
from energy_dashboard import SignSpec

_LOGGER = logging.getLogger(__name__)

_DEFAULT_DIR = Path(__file__).parent / "profiles"


@dataclass(frozen=True)
class RoleHint:
    """Where to look for one role's entity within a matched device's
    entities. `translation_keys` are tried first, in order; if none of them
    resolves to exactly one entity, `unique_id_suffix` fragments are tried
    the same way. The fallback exists because at least one real integration
    (Shelly) leaves `translation_key` unset on every entity – see
    device_registry.py's module docstring."""

    translation_keys: tuple[str, ...] = ()
    unique_id_suffix: tuple[str, ...] = ()


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
    # A single physical device that HA splits into several registry devices
    # with no parent/child link (observed on a Shelly Gen1 3EM: one device
    # per phase, none of them referencing the others) – see
    # device_registry.py's module docstring. When set, role resolution pools
    # entities across every device sharing the matched device's
    # config_entry_id instead of looking only at that one device.
    group_by_config_entry: bool = False
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
        role_name: RoleHint(
            translation_keys=tuple(spec.get("translation_key") or []),
            unique_id_suffix=tuple(spec.get("unique_id_suffix") or []),
        )
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
        group_by_config_entry=bool(match.get("group_by_config_entry", False)),
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
    # The entity pool this match was scored against – device.device_id's own
    # entities normally, or the pooled entities of its whole config-entry
    # group when profile.group_by_config_entry is set. device_resolver.py
    # binds roles against this same pool, so scoring and binding can never
    # drift apart by recomputing it differently in two places.
    entities: tuple[RegistryEntity, ...] = ()


def _hint_resolves(hint: RoleHint, entity_keys: set[str], uids: tuple[str, ...]) -> bool:
    if any(k in entity_keys for k in hint.translation_keys):
        return True
    # endswith, deliberately not a substring/`in` match: unique_id suffixes
    # can be prefixes of one another (Shelly's "...-power" is a literal
    # prefix of "...-powerFactor"), so `in` would false-positive-match both
    # from a single "power" fragment.
    return bool(hint.unique_id_suffix) and any(
        uid.endswith(frag) for frag in hint.unique_id_suffix for uid in uids
    )


def match_profiles(
    profiles: list[DeviceProfile], class_name: str, registry: RegistrySnapshot
) -> list[ProfileMatch]:
    """Every (profile, device) pairing for `class_name` whose manufacturer/
    model matches (or, absent a model match, whose entity_hints are all
    present), scored by strength and by how many roles resolve. See
    device_resolver.py for how ties/ambiguity are handled – this function
    only enumerates and scores, it never picks a winner.

    A `group_by_config_entry` profile is matched once per config-entry group
    rather than once per device in it (see device_registry.py's module
    docstring) – every device in the group would otherwise look like an
    identical, tied duplicate of every other, and device_resolver's "never
    guess on a true tie" rule would make the whole group unresolvable.
    """
    candidates: list[ProfileMatch] = []
    seen_groups: set[tuple[str, str]] = set()   # (profile_id, config_entry_id)

    for profile in profiles:
        if profile.class_name != class_name:
            continue
        for device in registry.devices.values():
            if profile.group_by_config_entry and device.config_entry_id:
                group_key = (profile.profile_id, device.config_entry_id)
                if group_key in seen_groups:
                    continue
                group_devices = sorted(
                    registry.devices_in_config_entry(device.config_entry_id),
                    key=lambda d: d.device_id,
                )
                pool = tuple(registry.entities_of_group([d.device_id for d in group_devices]))
                representative = group_devices[0] if group_devices else device
            else:
                group_key = None
                pool = tuple(registry.entities_of(device.device_id))
                representative = device

            entity_keys = {e.translation_key for e in pool if e.translation_key}
            uids = tuple(e.unique_id for e in pool if e.unique_id)

            model_match = profile.matches_device(device if group_key is None else representative)
            hints_match = bool(profile.entity_hints) and all(
                h in entity_keys for h in profile.entity_hints
            )
            if not model_match and not hints_match:
                continue

            if group_key is not None:
                seen_groups.add(group_key)

            resolved = sum(1 for hint in profile.roles.values() if _hint_resolves(hint, entity_keys, uids))

            candidates.append(ProfileMatch(
                profile=profile,
                device=representative,
                strong=model_match and (not profile.entity_hints or hints_match),
                resolved_role_count=resolved,
                entities=pool,
            ))
    return candidates
