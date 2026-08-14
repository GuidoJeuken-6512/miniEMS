"""miniEMS custom integration – polls the addon /api/status endpoint."""
from __future__ import annotations

from pathlib import Path

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir

from .coordinator import MiniEMSCoordinator

DOMAIN = "miniems"
PLATFORMS = ["sensor"]
CONF_BASE_URL = "base_url"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_BASE_URL = "http://homeassistant:8080"
DEFAULT_POLL_INTERVAL = 30

_RESTART_MARKER = Path(__file__).parent / ".restart_required"


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up miniEMS from a config entry."""
    base_url = entry.data[CONF_BASE_URL]
    poll_interval = entry.options.get(CONF_POLL_INTERVAL, DEFAULT_POLL_INTERVAL)

    coordinator = MiniEMSCoordinator(hass, base_url, poll_interval)
    await coordinator._async_setup()

    # Initial fetch – raises ConfigEntryNotReady if addon is unreachable
    await coordinator.async_config_entry_first_refresh()

    hass.data.setdefault(DOMAIN, {})[entry.entry_id] = coordinator

    registry = er.async_get(hass)

    # Remove stale entity registry entries from any prior miniEMS config entry.
    # These cause _2 / _3 entity_id suffixes when the integration is deleted and re-added.
    stale = [
        e for e in registry.entities.values()
        if e.platform == DOMAIN and e.config_entry_id != entry.entry_id
    ]
    for stale_entry in stale:
        registry.async_remove(stale_entry.entity_id)

    # Clear original_name for all miniEMS entities so HA uses translation_key
    # instead of cached hardcoded English names from before v1.6.0.
    for reg_entry in registry.entities.values():
        if reg_entry.platform == DOMAIN and reg_entry.original_name is not None:
            registry.async_update_entity(reg_entry.entity_id, original_name=None)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)

    if _RESTART_MARKER.exists():
        try:
            version = _RESTART_MARKER.read_text(encoding="utf-8").strip()
        except OSError:
            version = "unknown"
        ir.async_create_issue(
            hass,
            DOMAIN,
            "restart_required",
            is_fixable=False,
            severity=ir.IssueSeverity.WARNING,
            translation_key="restart_required",
            translation_placeholders={"version": version},
        )
        try:
            _RESTART_MARKER.unlink()
        except OSError:
            pass

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
