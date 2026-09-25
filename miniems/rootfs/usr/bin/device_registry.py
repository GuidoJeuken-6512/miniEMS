"""Indexes Home Assistant's device/entity registry lists for fast lookups.

Input is the raw result of `config/device_registry/list` and
`config/entity_registry/list` (see `ha_ws_api.py`). Pure – no I/O. Part of
the device-detection groundwork, see docs/roadmap/v3.0-geraeteprofile.md.

Two HA quirks this deliberately works around (both verified live against a
production installation):

- `device_class` on an entity-registry entry is almost always `null`; the
  real value lives in `original_device_class` (`device_class` is a user
  override, present only when someone actually changed it in the UI).
  `RegistryEntity.device_class` below already resolves that – callers never
  need to know the split exists.
- a device's `model` field is not a reliable match key: it can be a literal
  glob string copied out of an integration's own device-definition file
  (Solarman's Deye devices report `model: "SG0*LP3"`, no globbing performed
  by HA), a firmware version string (`model: "V0.0.8-3K"`), or `null`.
  `devices_matching()` therefore does an exact string comparison, never a
  glob/regex match, and callers must not treat a missing `model` as
  disqualifying on its own – see the device-profile matcher's own hint-based
  fallback.

A third quirk, found live against a Shelly Gen1 3EM (docs/roadmap/
v3.0-geraeteprofile.md, I7): a single physical device can show up as
*several* registry devices with no formal parent/child link between them –
this integration's `parent_device_id` is `null` on every one of them. HA
still groups them under one shared `config_entry_id`, though, which is what
`devices_in_config_entry()`/`entities_of_group()` below use to treat such a
group as one logical device for role resolution – see device_profile.py's
`group_by_config_entry`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class RegistryEntity:
    entity_id: str
    device_id: str | None
    platform: str | None
    unit_of_measurement: str | None
    device_class: str | None       # effective: override (device_class) or original_device_class
    translation_key: str | None
    original_name: str | None
    # Stable, integration-assigned id (e.g. "EC64C9C6A0C2-emeter_0-power").
    # translation_key is unset on every entity of at least one real
    # integration observed so far (Shelly) – unique_id is the only
    # role-matching key a device profile can then use, see
    # device_profile.RoleHint.unique_id_suffix.
    unique_id: str | None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "RegistryEntity":
        return cls(
            entity_id=raw.get("entity_id", ""),
            device_id=raw.get("device_id"),
            platform=raw.get("platform"),
            unit_of_measurement=raw.get("unit_of_measurement"),
            device_class=raw.get("device_class") or raw.get("original_device_class"),
            translation_key=raw.get("translation_key"),
            original_name=raw.get("original_name"),
            unique_id=raw.get("unique_id"),
        )


@dataclass(frozen=True)
class RegistryDevice:
    device_id: str
    manufacturer: str | None
    model: str | None
    name: str | None
    # Which integration config entry created this device – see module
    # docstring, "a single physical device can show up as several registry
    # devices". None for a device registered without one (rare).
    config_entry_id: str | None = None

    @classmethod
    def from_raw(cls, raw: dict[str, Any]) -> "RegistryDevice":
        return cls(
            device_id=raw.get("id", ""),
            manufacturer=raw.get("manufacturer"),
            model=raw.get("model"),
            name=raw.get("name_by_user") or raw.get("name"),
            config_entry_id=raw.get("config_entry_id"),
        )


class RegistrySnapshot:
    """Indexed view over one device/entity registry pull."""

    def __init__(
        self, devices: dict[str, RegistryDevice], entities: dict[str, RegistryEntity]
    ) -> None:
        self.devices = devices
        self.entities = entities
        self._entities_by_device: dict[str, list[str]] = {}
        for entity in entities.values():
            if entity.device_id:
                self._entities_by_device.setdefault(entity.device_id, []).append(entity.entity_id)

    @classmethod
    def from_lists(
        cls, devices: list[dict[str, Any]] | None, entities: list[dict[str, Any]] | None
    ) -> "RegistrySnapshot":
        dev_map: dict[str, RegistryDevice] = {}
        for raw in devices or []:
            if isinstance(raw, dict) and raw.get("id"):
                dev_map[raw["id"]] = RegistryDevice.from_raw(raw)

        ent_map: dict[str, RegistryEntity] = {}
        for raw in entities or []:
            if isinstance(raw, dict) and raw.get("entity_id"):
                entity = RegistryEntity.from_raw(raw)
                ent_map[entity.entity_id] = entity

        return cls(dev_map, ent_map)

    def entities_of(self, device_id: str) -> list[RegistryEntity]:
        """All entities registered on `device_id`, or [] if unknown/empty."""
        return [self.entities[eid] for eid in self._entities_by_device.get(device_id, [])]

    def device_of(self, entity_id: str) -> RegistryDevice | None:
        """The device `entity_id` belongs to, or None (unregistered entity,
        or a device-less helper entity)."""
        entity = self.entities.get(entity_id)
        if entity is None or not entity.device_id:
            return None
        return self.devices.get(entity.device_id)

    def devices_matching(self, manufacturer: str, models: list[str]) -> list[RegistryDevice]:
        """Devices with an exact `manufacturer` match and a `model` in
        `models` – exact string comparison only, see module docstring."""
        return [
            d for d in self.devices.values()
            if d.manufacturer == manufacturer and d.model in models
        ]

    def devices_in_config_entry(self, config_entry_id: str | None) -> list[RegistryDevice]:
        """Every device sharing `config_entry_id` – the group a
        `group_by_config_entry` profile treats as one logical device. `None`
        always yields `[]`: a device with no config entry of its own is
        never "grouped" with anything."""
        if not config_entry_id:
            return []
        return [d for d in self.devices.values() if d.config_entry_id == config_entry_id]

    def entities_of_group(self, device_ids: list[str]) -> list[RegistryEntity]:
        """Union of entities_of() over several devices, deduplicated by
        entity_id (a device can in principle appear twice in `device_ids`)."""
        seen: dict[str, RegistryEntity] = {}
        for device_id in device_ids:
            for entity in self.entities_of(device_id):
                seen[entity.entity_id] = entity
        return list(seen.values())
