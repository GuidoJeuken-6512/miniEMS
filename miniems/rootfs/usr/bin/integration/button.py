"""C2 – buttons: hot water now, end the overrides."""
from __future__ import annotations

from homeassistant.components.button import ButtonEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .control import MiniEMSControlEntity, coordinator_of

# key → (path, payload, icon)
_BUTTONS = {
    "hot_water_now": ("/api/hot_water/adhoc", {"action": "start"}, "mdi:water-boiler-alert"),
    "end_overrides": ("/api/override", {"clear": "all"}, "mdi:restore"),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = coordinator_of(hass, entry)
    async_add_entities([MiniEMSButton(coordinator, entry, key, path, payload, icon)
                        for key, (path, payload, icon) in _BUTTONS.items()])


class MiniEMSButton(MiniEMSControlEntity, ButtonEntity):
    def __init__(self, coordinator, entry, key: str, path: str, payload: dict, icon: str) -> None:
        super().__init__(coordinator, entry, "button", key, key, icon)
        self._path, self._payload = path, payload

    async def async_press(self) -> None:
        await self.coordinator.async_post(self._path, dict(self._payload))
