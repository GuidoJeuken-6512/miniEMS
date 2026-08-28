"""Resolves each class's roles to real HA entity ids.

Implements the "Auflösungskette" from docs/roadmap/v3.0-geraeteprofile.md:

1. config           – an explicit `entity_overrides["<class>.<role>"]`, or a
                       legacy `*_entity` config field that differs from its
                       shipped default. Always wins.
2. energy_dashboard  – HA's energy-dashboard preferences: sign convention,
                       battery capacity, price entity, and (indirectly) the
                       device ids that seed source 3.
3. profile           – a device profile matched on (manufacturer, model)
                       and/or entity_hints, with the role resolved via
                       translation_key among that device's entities.
4. heuristic         – roles.yaml `hints` matched against the matched
                       device's entities (or, absent a matched device,
                       every registered entity), accepted only if exactly
                       one candidate survives.
5. unresolved        – required → a visible, never-silent gap (the caller
                       must surface this, not guess); optional → degrades
                       exactly like an empty config field does today.

Pure: everything is passed in, nothing is read from HA or disk here – that
is ha_ws_api.py's, energy_dashboard.py's, device_registry.py's,
role_catalog.py's and device_profile.py's job. Not yet wired into
EMSController/InverterController – see the roadmap doc's "Umsetzungsschritte".
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from device_profile import ProfileMatch, match_profiles
from energy_dashboard import EnergyDashboardMap, SignSpec

if TYPE_CHECKING:
    from device_profile import DeviceProfile
    from device_registry import RegistrySnapshot
    from role_catalog import RoleCatalog


@dataclass(frozen=True)
class RoleBinding:
    role: str
    entity_id: str
    source: str          # "config" | "energy_dashboard" | "profile" | "heuristic"
    sign: SignSpec | None = None


@dataclass(frozen=True)
class RoleConflict:
    """A role where more than one source offered a (different) entity.
    `chosen` is what won by priority; `rejected` is everything else that was
    offered, for display in a mapping-preview UI – never silent."""

    role: str
    chosen: RoleBinding
    rejected: tuple[RoleBinding, ...]


@dataclass(frozen=True)
class ClassResolution:
    class_name: str
    bindings: dict[str, RoleBinding] = field(default_factory=dict)
    unresolved_required: tuple[str, ...] = ()
    conflicts: tuple[RoleConflict, ...] = ()
    matched_device_id: str | None = None

    def entity_for(self, role: str) -> str | None:
        binding = self.bindings.get(role)
        return binding.entity_id if binding else None


def resolve_class(
    class_name: str,
    *,
    catalog: "RoleCatalog",
    overrides: dict[str, str],
    energy_map: EnergyDashboardMap,
    registry: "RegistrySnapshot",
    profiles: list["DeviceProfile"],
    config_flags: dict[str, bool],
) -> ClassResolution:
    """Resolve every role of `class_name` against the four live sources.

    `overrides` keys are `"<class>.<role>"` (e.g. "inverter.battery_power")
    → entity_id, already filtered by the caller to only the fields that
    differ from their shipped default (see docs/roadmap/v3.0-geraeteprofile.md,
    "Migration" – "Bestandswerte klassifizieren, nicht zerstören").
    `config_flags` carries the boolean config fields role_catalog's
    `required_if` can reference (e.g. {"battery_control_enabled": True}).
    """
    role_specs = catalog.roles_for(class_name)

    profile_matches = match_profiles(profiles, class_name, registry)
    best_match = _pick_best_device_match(profile_matches)

    bindings: dict[str, RoleBinding] = {}
    conflicts: list[RoleConflict] = []

    for role_name, spec in role_specs.items():
        candidates = _candidates_for_role(
            role_name, spec, overrides, energy_map, registry, best_match, class_name,
        )
        if not candidates:
            continue

        chosen = candidates[0]
        bindings[role_name] = chosen
        rejected = tuple(c for c in candidates[1:] if c.entity_id != chosen.entity_id)
        if rejected:
            conflicts.append(RoleConflict(role_name, chosen, rejected))

    unresolved_required = tuple(
        name for name, spec in role_specs.items()
        if spec.is_required(config_flags) and name not in bindings
    )

    return ClassResolution(
        class_name=class_name,
        bindings=bindings,
        unresolved_required=unresolved_required,
        conflicts=tuple(conflicts),
        matched_device_id=best_match.device.device_id if best_match else None,
    )


def _candidates_for_role(
    role_name: str,
    spec,
    overrides: dict[str, str],
    energy_map: EnergyDashboardMap,
    registry: "RegistrySnapshot",
    best_match: ProfileMatch | None,
    class_name: str,
) -> list[RoleBinding]:
    candidates: list[RoleBinding] = []

    # Source 1: explicit override. Always first, so it's always `chosen`.
    override_key = f"{class_name}.{role_name}"
    override_entity = overrides.get(override_key)
    if override_entity:
        candidates.append(RoleBinding(role_name, override_entity, "config", spec.sign))

    # Source 2: energy dashboard. Offered to every class's matching role
    # name – the dashboard has no notion of which class "owns" a role, so a
    # role name collision across classes (e.g. battery_soc in both
    # `inverter` and `battery`) simply gets the same candidate in both; the
    # resolver run is always scoped to one class at a time regardless.
    eb_entity = energy_map.candidates.get(role_name)
    if eb_entity:
        candidates.append(
            RoleBinding(role_name, eb_entity, "energy_dashboard", energy_map.signs.get(role_name))
        )

    # Source 3: matched device profile.
    if best_match is not None:
        hint = best_match.profile.roles.get(role_name)
        if hint is not None:
            device_entities = registry.entities_of(best_match.device.device_id)
            for key in hint.translation_keys:
                found = [e for e in device_entities if e.translation_key == key]
                if len(found) == 1:
                    sign = best_match.profile.signs.get(role_name, spec.sign)
                    candidates.append(RoleBinding(role_name, found[0].entity_id, "profile", sign))
                    break

    # Source 4: generic heuristic – only within the matched device if there
    # is one, else across the whole registry. Accepted only when the filter
    # (hints + domain, then device_class/unit if still ambiguous) leaves
    # exactly one candidate; anything else is not a confident answer.
    if not any(c.source in ("config", "profile") for c in candidates) and spec.hints:
        pool = (
            registry.entities_of(best_match.device.device_id)
            if best_match is not None else list(registry.entities.values())
        )
        hits = [e for e in pool if e.translation_key in spec.hints]
        if spec.domain:
            hits = [e for e in hits if e.entity_id.split(".", 1)[0] in spec.domain]
        if len(hits) > 1 and spec.device_class:
            narrowed = [e for e in hits if e.device_class in spec.device_class]
            if narrowed:
                hits = narrowed
        if len(hits) > 1 and spec.unit:
            narrowed = [e for e in hits if e.unit_of_measurement in spec.unit]
            if narrowed:
                hits = narrowed
        if len(hits) == 1:
            candidates.append(RoleBinding(role_name, hits[0].entity_id, "heuristic", spec.sign))

    return candidates


def _pick_best_device_match(matches: list[ProfileMatch]) -> ProfileMatch | None:
    """Among candidate (profile, device) pairs: a strong match beats a weak
    one; among equal strength, the one resolving the most roles wins
    (discards an empty duplicate device without special-case code); a true
    tie is never guessed – returns None, which makes every role fall
    through to the heuristic source instead of binding to the wrong device.
    """
    if not matches:
        return None
    strong = [m for m in matches if m.strong]
    pool = strong if strong else matches
    pool = sorted(pool, key=lambda m: m.resolved_role_count, reverse=True)
    if len(pool) == 1:
        return pool[0]
    if pool[0].resolved_role_count > pool[1].resolved_role_count:
        return pool[0]
    return None
