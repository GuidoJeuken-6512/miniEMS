"""C2 – override per device, always with an expiry (number.miniems_override_minutes)."""
from __future__ import annotations

from typing import Any

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .control import MiniEMSControlEntity, coordinator_of

_DEVICES = {
    "battery": (["auto", "grid_charge", "hold", "no_grid_charge"], "mdi:battery-clock"),
    "ev": (["auto", "now", "pv", "stop"], "mdi:car-clock"),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = coordinator_of(hass, entry)
    async_add_entities([MiniEMSOverrideSelect(coordinator, entry, device, options, icon)
                        for device, (options, icon) in _DEVICES.items()])


class MiniEMSOverrideSelect(MiniEMSControlEntity, SelectEntity):
    def __init__(self, coordinator, entry, device: str, options: list[str], icon: str) -> None:
        super().__init__(coordinator, entry, "select", f"{device}_override",
                         f"{device}_override", icon)
        self._device = device
        self._attr_options = options

    @property
    def current_option(self) -> str | None:
        item = self._block("overrides").get(self._device) or {}
        return item.get("value", "auto")

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        item = self._block("overrides").get(self._device) or {}
        return {"until": item.get("until"), "source": item.get("source")}

    async def async_select_option(self, option: str) -> None:
        await self.coordinator.async_post("/api/override", {
            "device": self._device, "value": option, "source": "home_assistant"})
