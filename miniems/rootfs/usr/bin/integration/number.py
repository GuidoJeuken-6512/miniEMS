"""C2 – duration of an override (15 min – 6 h)."""
from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .control import MiniEMSControlEntity, coordinator_of


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry,
                            async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([MiniEMSOverrideMinutes(coordinator_of(hass, entry), entry)])


class MiniEMSOverrideMinutes(MiniEMSControlEntity, NumberEntity):
    _attr_native_min_value = 15
    _attr_native_max_value = 360
    _attr_native_step = 15
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry) -> None:
        super().__init__(coordinator, entry, "number", "override_minutes", "override_minutes",
                         "mdi:timer-outline")

    @property
    def native_value(self) -> float | None:
        return self._block("overrides").get("minutes")

    async def async_set_native_value(self, value: float) -> None:
        await self.coordinator.async_post("/api/override", {"minutes": int(value)})
