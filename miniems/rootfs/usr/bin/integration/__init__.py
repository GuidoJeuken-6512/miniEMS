"""miniEMS custom integration – polls the addon /api/status endpoint."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import aiohttp
from homeassistant.components.frontend import add_extra_js_url
from homeassistant.components.http import StaticPathConfig
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import ConfigEntryNotReady
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.typing import ConfigType

from .coordinator import MiniEMSCoordinator

_LOGGER = logging.getLogger(__name__)

DOMAIN = "miniems"
PLATFORMS = ["sensor"]
CONF_BASE_URL = "base_url"
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_BASE_URL = "http://homeassistant:8080"
DEFAULT_POLL_INTERVAL = 30

_RESTART_MARKER = Path(__file__).parent / ".restart_required"

# Lovelace cards this integration ships, bundled under frontend/. Registered
# once in async_setup() (domain-level, runs even without a config entry) –
# not async_setup_entry() – same pattern as cygnusb/ha-smart-battery-pilot's
# __init__.py, so the card resource exists as soon as the integration is
# loaded at all, not contingent on a working config entry.
_FRONTEND_CARDS = {
    "miniems-flow-card.js": f"/{DOMAIN}/miniems-flow-card.js",
    "miniems-plan-card.js": f"/{DOMAIN}/miniems-plan-card.js",
}

# Auto-generated dashboard (Schritt B3) – same raw-storage-write approach as
# the SEM Community integration's features/dashboard_generator.py (no
# official "create a Lovelace dashboard" API exists), but through HA's own
# Store helper instead of manual open()/json.dump() for atomic, correctly
# JSON-encoded writes. Store is a generic file helper, though – it knows
# nothing about Lovelace's own in-memory DashboardsCollection cache, so a
# running HA instance still won't see the new entry until that cache is
# rebuilt. SEM's own conclusion (and the one followed here): that requires
# an HA restart, there is no live-reload path for a raw storage write.
_DASHBOARD_URL_PATH = "miniems"
_DASHBOARD_STORAGE_KEY = f"lovelace.{_DASHBOARD_URL_PATH}"
SERVICE_GENERATE_DASHBOARD = "generate_dashboard"


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register the bundled Lovelace card resources and the dashboard service."""
    frontend_dir = Path(__file__).parent / "frontend"
    for filename, url in _FRONTEND_CARDS.items():
        card_path = frontend_dir / filename
        if await hass.async_add_executor_job(card_path.exists):
            await hass.http.async_register_static_paths(
                [StaticPathConfig(url, str(card_path), cache_headers=False)]
            )
            add_extra_js_url(hass, url)
        else:
            _LOGGER.warning("miniEMS frontend card not found: %s", card_path)

    if not hass.services.has_service(DOMAIN, SERVICE_GENERATE_DASHBOARD):
        async def _handle_generate_dashboard(call: ServiceCall) -> None:
            for entry in hass.config_entries.async_entries(DOMAIN):
                await async_generate_dashboard(hass, entry)

        hass.services.async_register(
            DOMAIN, SERVICE_GENERATE_DASHBOARD, _handle_generate_dashboard
        )
    return True


async def _fetch_addon_config(hass: HomeAssistant, base_url: str) -> dict[str, Any]:
    """GET /api/config from the addon – same endpoint web_server.py already
    serves for its own config.json editor. Best-effort: an unreachable addon
    just means the flow card's entity fields land empty (still editable by
    hand afterwards), not a failed dashboard generation."""
    try:
        async with aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=10)
        ) as session:
            async with session.get(f"{base_url.rstrip('/')}/api/config") as resp:
                if resp.status == 200:
                    return await resp.json()
                _LOGGER.warning(
                    "miniEMS: /api/config returned HTTP %s – dashboard entity "
                    "fields will be left empty",
                    resp.status,
                )
    except (aiohttp.ClientError, TimeoutError) as err:
        _LOGGER.warning(
            "miniEMS: could not reach addon for dashboard generation: %s", err
        )
    return {}


async def async_generate_dashboard(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Create (or refresh) the miniEMS dashboard with both bundled cards.

    Pre-fills the flow card's entity fields from the addon's own config.json
    (already configured once by the user in the addon's Settings page) –
    see integration/sensor.py's comment on why this integration does not
    publish its own pv/battery/grid/load sensors; the card needs those
    entity ids from *somewhere*, and the addon's config is the one place
    they already exist without asking the user again.
    """
    base_url = entry.data.get(CONF_BASE_URL, DEFAULT_BASE_URL)
    addon_cfg = await _fetch_addon_config(hass, base_url)

    dashboard_config = {
        "views": [
            {
                "title": "miniEMS",
                "type": "sections",
                "sections": [
                    {
                        "type": "grid",
                        "cards": [
                            {
                                "type": "custom:miniems-flow-card",
                                "pv_entity": addon_cfg.get("pv_power_entity", ""),
                                "battery_power_entity": addon_cfg.get(
                                    "battery_power_entity", ""
                                ),
                                "battery_soc_entity": addon_cfg.get(
                                    "battery_soc_entity", ""
                                ),
                                "grid_entity": addon_cfg.get("grid_power_entity", ""),
                                "load_entity": addon_cfg.get("load_power_entity", ""),
                            },
                            {
                                "type": "custom:miniems-plan-card",
                                "entity": "sensor.miniems_energy_plan_deficit_kwh",
                            },
                        ],
                    }
                ],
            }
        ],
    }

    await Store(hass, 1, _DASHBOARD_STORAGE_KEY).async_save(
        {"config": dashboard_config}
    )

    registry_store: Store = Store(hass, 1, "lovelace_dashboards")
    registry_data = (await registry_store.async_load()) or {"items": []}
    items: list[dict[str, Any]] = registry_data.setdefault("items", [])
    existing = next(
        (item for item in items if item.get("url_path") == _DASHBOARD_URL_PATH), None
    )
    if existing is not None:
        existing.update(
            {"title": "miniEMS", "icon": "mdi:solar-power", "show_in_sidebar": True}
        )
    else:
        items.append(
            {
                "id": _DASHBOARD_URL_PATH,
                "url_path": _DASHBOARD_URL_PATH,
                "title": "miniEMS",
                "icon": "mdi:solar-power",
                "show_in_sidebar": True,
                "require_admin": False,
                "mode": "storage",
            }
        )
    await registry_store.async_save(registry_data)
    _LOGGER.info(
        "miniEMS dashboard generated/updated at url_path=%s", _DASHBOARD_URL_PATH
    )


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

    # Remove orphaned entities *within this same config entry* whose unique_id
    # is no longer produced by SENSOR_DESCRIPTIONS – typically because a
    # sensor's `key` was renamed (e.g. a typo fix) across a release. The check
    # above only catches entities left behind by a fully deleted-and-re-added
    # config entry; it does nothing for this case, since config_entry_id is
    # unchanged. Left alone, such an orphan keeps its entity_id slug forever
    # (permanently "unavailable"), and the entity re-registered under the new
    # unique_id collides with that slug and gets a "_2" suffix instead of the
    # clean name. Deferred import: sensor.py imports DOMAIN from this module,
    # so importing it back at module load time would be circular.
    from .sensor import SENSOR_DESCRIPTIONS
    current_unique_ids = {d.key for d in SENSOR_DESCRIPTIONS}
    orphaned = [
        e for e in registry.entities.values()
        if e.platform == DOMAIN
        and e.config_entry_id == entry.entry_id
        and e.unique_id not in current_unique_ids
    ]
    for orphan in orphaned:
        _LOGGER.info(
            "Removing orphaned miniEMS entity %s (unique_id=%r no longer produced)",
            orphan.entity_id, orphan.unique_id,
        )
        registry.async_remove(orphan.entity_id)

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

    # One-shot: create the bundled dashboard the first time this entry is
    # ever set up. Gated on an entry.options flag (not e.g. the storage file's
    # existence) so a user who deleted the generated dashboard on purpose
    # doesn't get it silently recreated on the next HA restart – re-adding it
    # is what the generate_dashboard service is for.
    if not entry.options.get("_dashboard_generated", False):
        async def _generate_and_restart() -> None:
            try:
                await async_generate_dashboard(hass, entry)
            except Exception as err:  # noqa: BLE001 – never crash the entry over this
                _LOGGER.error("miniEMS: dashboard generation failed: %s", err)
                return
            # Setting this triggers _reload_on_options_update above, which
            # re-enters async_setup_entry and (seeing the flag now set)
            # skips this block on that pass – see async_generate_dashboard's
            # docstring for why a restart is still needed to actually show it.
            hass.config_entries.async_update_entry(
                entry, options={**entry.options, "_dashboard_generated": True}
            )
            _LOGGER.info(
                "miniEMS dashboard created – restarting Home Assistant so "
                "Lovelace picks it up"
            )
            await hass.services.async_call("homeassistant", "restart", {}, blocking=False)

        entry.async_create_background_task(
            hass, _generate_and_restart(), "miniems_generate_dashboard"
        )

    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload a config entry."""
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        coordinator: MiniEMSCoordinator = hass.data[DOMAIN].pop(entry.entry_id)
        await coordinator.async_shutdown()
    return ok
