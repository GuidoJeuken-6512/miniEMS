"""C2 – switches: simulation, controls on/off, PV priority battery ↔ EV."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .control import MiniEMSControlEntity, coordinator_of

# key → (control_mode field read, request field written, icon)
_SWITCHES = {
    "simulation": ("simulation", "simulation", "mdi:test-tube"),
    "battery_control": ("battery_control", "battery_control", "mdi:battery-sync"),
    "hot_water_control": ("hw_control", "hw_control", "mdi:water-boiler"),
    "ev_control": ("ev_control", "ev_control", "mdi:ev-station"),
    "heating_control": ("heating_control", "heating_control", "mdi:radiator"),
}


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    coordinator = coordinator_of(hass, entry)
    entities: list[SwitchEntity] = [
        MiniEMSModeSwitch(coordinator, entry, key, read, write, icon)
        for key, (read, write, icon) in _SWITCHES.items()
    ]
    entities.append(MiniEMSPrioritySwitch(coordinator, entry))
    async_add_entities(entities)


class MiniEMSModeSwitch(MiniEMSControlEntity, SwitchEntity):
    def __init__(self, coordinator, entry, key: str, read: str, write: str, icon: str) -> None:
        super().__init__(coordinator, entry, "switch", key, key, icon)
        self._read, self._write = read, write

    @property
    def is_on(self) -> bool | None:
        mode = self._block("control_mode")
        return None if self._read not in mode else bool(mode[self._read])

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_post("/api/control_mode", {self._write: True})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_post("/api/control_mode", {self._write: False})


class MiniEMSPrioritySwitch(MiniEMSControlEntity, SwitchEntity):
    """On = the EV gets the PV surplus before the battery."""

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "switch", "ev_priority", "ev_priority", "mdi:car-electric")

    @property
    def is_on(self) -> bool | None:
        prio = self._block("control_mode").get("charge_priority")
        return None if prio is None else prio == "ev"

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self.coordinator.async_post("/api/control_mode", {"charge_priority": "ev"})

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self.coordinator.async_post("/api/control_mode", {"charge_priority": "battery"})
