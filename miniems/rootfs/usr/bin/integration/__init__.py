"""miniEMS custom integration – polls the addon /api/status endpoint."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady

from .coordinator import MiniEMSCoordinator

DOMAIN = "miniems"
PLATFORMS = ["sensor"]
CONF_BASE_URL = "base_url"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_BASE_URL = "http://homeassistant:8080"
DEFAULT_POLL_INTERVAL = 30


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up miniEMS from a config entry."""
    base_url = entry.data[CONF_BASE_URL]
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    coordinator = MiniEMSCoordinator(hass, base_url, poll_interval)
    await coordinator._async_setup()

    # Initial fetch – raises ConfigEntryNotReady if addon is unreachable
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    async def _reload_on_options_update(hass: HomeAssistant, entry: ConfigEntry) -> None:
        await hass.config_entries.async_reload(entry.entry_id)

    entry.async_on_unload(entry.add_update_listener(_reload_on_options_update))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: MiniEMSCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return ok
