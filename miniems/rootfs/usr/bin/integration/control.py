"""C2 – controls from Home Assistant, shared by switch/select/number/button.

Every entity writes through the add-on's own endpoints – /api/control_mode
for the switches, /api/override for the overrides (with an expiry) – so the
add-on page and HA always show the same state; the value written last wins.
"""
from __future__ import annotations

from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .coordinator import MiniEMSCoordinator

DOMAIN = "miniems"


def device_info(entry: ConfigEntry) -> DeviceInfo:
    from . import CONF_BASE_URL
    return DeviceInfo(
        identifiers={(DOMAIN, "miniems_addon")},
        name="miniEMS",
        manufacturer="miniEMS Addon",
        configuration_url=entry.data.get(CONF_BASE_URL),
    )


def coordinator_of(hass: HomeAssistant, entry: ConfigEntry) -> MiniEMSCoordinator:
    return hass.data[DOMAIN][entry.entry_id]


class MiniEMSControlEntity(CoordinatorEntity[MiniEMSCoordinator]):
    """Base: fixed entity id (see sensor.py on why), translated name."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: MiniEMSCoordinator, entry: ConfigEntry, platform: str,
                 key: str, translation_key: str, icon: str | None = None) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"miniems_{key}"
        self._attr_translation_key = translation_key
        self._attr_device_info = device_info(entry)
        if icon:
            self._attr_icon = icon
        self.entity_id = f"{platform}.miniems_{key}"

    def _block(self, key: str) -> dict[str, Any]:
        data = self.coordinator.data or {}
        value = data.get(key)
        return value if isinstance(value, dict) else {}

    @property
    def available(self) -> bool:
        return super().available and self.coordinator.data is not None
