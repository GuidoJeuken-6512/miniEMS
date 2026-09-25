"""Loads and provides typed access to the generic role catalog (roles.yaml).

Single source of truth for both the device resolver (device_resolver.py) and
the settings entity-picker filter (planned) – see
docs/roadmap/v3.0-geraeteprofile.md, "Rollenkatalog". Contains no
Solarman-specific strings: `hints` are plain words matched against an
entity's translation_key as a last-resort heuristic (resolution source 4),
not an import of a device-definition file.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from energy_dashboard import SignSpec

_LOGGER = logging.getLogger(__name__)

_DEFAULT_PATH = Path(__file__).parent / "roles.yaml"


@dataclass(frozen=True)
class RoleSpec:
    """One role's declared meaning and recognition rules."""

    name: str
    class_name: str
    kind: str = "measurement"      # measurement | actuator | timestamp | enum
    quantity: str = ""             # power | energy | energy_lifetime | soc | voltage |
                                    # current | temperature | price | boolean | timestamp
    required: bool = False
    required_if: str | None = None   # a Config boolean field name
    domain: tuple[str, ...] = ()
    device_class: tuple[str, ...] = ()   # empty = no filter
    unit: tuple[str, ...] = ()           # empty = no filter (measurement roles)
    accepts_unit: tuple[str, ...] = ()   # actuator's possible native units
    sign: SignSpec | None = None
    hints: tuple[str, ...] = ()

    def is_required(self, config_flags: dict[str, bool]) -> bool:
        """Whether this role must resolve, given the live config's relevant
        boolean flags (e.g. {"battery_control_enabled": True})."""
        if self.required:
            return True
        if self.required_if:
            return bool(config_flags.get(self.required_if, False))
        return False


@dataclass(frozen=True)
class RoleCatalog:
    """All classes/roles loaded from roles.yaml."""

    classes: dict[str, dict[str, RoleSpec]] = field(default_factory=dict)

    def roles_for(self, class_name: str) -> dict[str, RoleSpec]:
        return self.classes.get(class_name, {})

    def role(self, class_name: str, role_name: str) -> RoleSpec | None:
        return self.classes.get(class_name, {}).get(role_name)

    def required_roles(self, class_name: str, config_flags: dict[str, bool]) -> list[str]:
        return [
            name for name, spec in self.roles_for(class_name).items()
            if spec.is_required(config_flags)
        ]


def _parse_sign(raw: dict[str, Any] | None) -> SignSpec | None:
    if not raw:
        return None
    return SignSpec(
        mode=raw.get("mode", "unsigned"),
        positive=raw.get("positive"),
        from_role=raw.get("from_role"),
        to_role=raw.get("to_role"),
    )


def _as_tuple(value: Any) -> tuple[str, ...]:
    if value is None:
        return ()
    if isinstance(value, (list, tuple)):
        return tuple(str(v) for v in value)
    return (str(value),)


def load_role_catalog(path: Path | str | None = None) -> RoleCatalog:
    """Load roles.yaml. A missing file or unparsable YAML yields an empty
    catalog (logged, not raised) – the resolver then simply has nothing to
    resolve against, instead of crashing the add-on over a data file.
    """
    p = Path(path) if path is not None else _DEFAULT_PATH
    try:
        with open(p, encoding="utf-8") as f:
            raw = yaml.safe_load(f) or {}
    except (OSError, yaml.YAMLError) as exc:
        _LOGGER.error("Failed to load role catalog %s: %s", p, exc)
        return RoleCatalog()

    classes: dict[str, dict[str, RoleSpec]] = {}
    for class_name, class_data in (raw.get("classes") or {}).items():
        if not isinstance(class_data, dict):
            continue
        roles: dict[str, RoleSpec] = {}
        for role_name, role_data in (class_data.get("roles") or {}).items():
            if not isinstance(role_data, dict):
                continue
            roles[role_name] = RoleSpec(
                name=role_name,
                class_name=class_name,
                kind=role_data.get("kind", "measurement"),
                quantity=role_data.get("quantity", ""),
                required=bool(role_data.get("required", False)),
                required_if=role_data.get("required_if"),
                domain=_as_tuple(role_data.get("domain")),
                device_class=_as_tuple(role_data.get("device_class")),
                unit=_as_tuple(role_data.get("unit")),
                accepts_unit=_as_tuple(role_data.get("accepts_unit")),
                sign=_parse_sign(role_data.get("sign")),
                hints=_as_tuple(role_data.get("hints")),
            )
        classes[class_name] = roles
    return RoleCatalog(classes=classes)
