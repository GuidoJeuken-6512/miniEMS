"""FastAPI ingress dashboard for miniEMS.

HTML lives in templates/, CSS in static/style.css.
"""

import asyncio
import json
import logging
import os
from dataclasses import fields as dc_fields
from pathlib import Path
from typing import Any

import aiohttp
import yaml
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

import const
import ha_ws_api
from config_loader import Config
from const import CONFIG_FILE, OPTIONS_FILE, SUPERVISOR_RESTART_URL
from device_profile import load_profiles
from device_registry import RegistrySnapshot
from device_resolver import profile_status, resolve_class
from energy_dashboard import parse_energy_prefs
from legacy_entity_fields import inverter_overrides
from role_catalog import load_role_catalog

_LOGGER = logging.getLogger(__name__)

_DIR = Path(__file__).parent
_TEMPLATES = Jinja2Templates(directory=str(_DIR / "templates"))


async def get_ha_language(request: Request) -> str:
    """Determine UI language from HA Supervisor API or Accept-Language header."""
    supervisor_token = getattr(request.app.state, "supervisor_token", "")
    if supervisor_token:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    "http://supervisor/core/api/config",
                    headers={"Authorization": f"Bearer {supervisor_token}"},
                    timeout=aiohttp.ClientTimeout(total=3),
                ) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        lang = data.get("language", "en")
                        if lang not in ("de", "en"):
                            lang = "en"
                        return lang
        except Exception:
            pass
    # Fallback: Accept-Language header
    accept = request.headers.get("accept-language", "")
    if accept:
        lang = accept.split(",")[0].split("-")[0]
        if lang in ("de", "en"):
            return lang
    return "en"


def load_translations(lang: str) -> dict:
    tfile = _DIR / 'translations' / f'{lang}.yaml'
    if not tfile.exists():
        tfile = _DIR / 'translations' / 'en.yaml'
    try:
        with open(tfile, encoding='utf-8') as f:
            return yaml.safe_load(f) or {}
    except Exception:
        return {}

# Config field type map for coercion
_BOOL_FIELDS = {
    "battery_control_enabled", "battery_control_simulation",
    "pv_export_priority_enabled", "device_detection_enabled",
}
# Dict-valued fields never have a settings.html form control (no free-text
# input can represent a dict safely) – but /api/config's merge loop below
# runs every posted key through _coerce() regardless of source, and without
# this set a dict value would fall through to the str(value) branch and get
# silently stringified/corrupted on save. See
# docs/roadmap/v3.0-geraeteprofile.md, "Migration" – "Mine im selben Schritt
# entschärfen".
_DICT_FIELDS = {"entity_overrides"}
_INT_FIELDS = {
    "battery_min_soc", "battery_max_soc", "pv_surplus_threshold_w",
    "update_interval_sec", "battery_max_charge_current_a", "battery_max_discharge_current_a",
    "event_log_retention_days",
    "pv_export_min_soc_pct", "pv_charge_backstop_hour", "export_hold_charge_current_a",
    "mode_dwell_sec", "battery_soc_hysteresis_pct",
    "grid_charge_dark_start_hour", "grid_charge_dark_end_hour",
    "sensor_max_age_sec", "forecast_max_age_sec", "price_max_age_sec",
    "inverter_write_stuck_threshold_sec",
}
# NOTE: any float field missing here falls through to str(value) in _coerce()
# and is persisted as a string, which then raises TypeError on first use.
_FLOAT_FIELDS = {
    "battery_capacity_kwh", "cheap_rate_threshold_eur", "medium_rate_threshold_eur",
    "feed_in_tariff_eur_kwh", "fix_price",
    "daily_base_price_eur", "avg_discharge_tariff_eur_kwh",
    "pv_charge_margin_factor", "pv_charge_hysteresis_frac", "grid_charge_min_free_kwh",
}


def _coerce(key: str, value: Any) -> Any:
    """Convert a form/JSON value to the correct Python type for the config key."""
    if key in _BOOL_FIELDS:
        if isinstance(value, bool):
            return value
        return str(value).lower() in ("true", "1", "on", "yes")
    if key in _INT_FIELDS:
        try:
            return int(value) if value is not None else 0
        except (TypeError, ValueError):
            return 0
    if key in _FLOAT_FIELDS:
        try:
            return float(value) if value is not None else 0.0
        except (TypeError, ValueError):
            return 0.0
    if key in _DICT_FIELDS:
        return value if isinstance(value, dict) else {}
    return str(value) if value is not None else ""


def _resolve_class_json(
    class_name: str,
    *,
    catalog: Any,
    overrides: dict[str, str],
    energy_map: Any,
    registry: Any,
    profiles: list[Any],
    live_config: "Config",
) -> dict[str, Any]:
    """resolve_class() for one class, JSON-shaped for /api/devices. `battery_
    control_enabled` is the only config_flags entry any role currently
    declares required_if on (inverter's three control roles) – harmless to
    pass for every class, roles.yaml's other classes simply don't reference it."""
    resolution = resolve_class(
        class_name,
        catalog=catalog,
        overrides=overrides,
        energy_map=energy_map,
        registry=registry,
        profiles=profiles,
        config_flags={"battery_control_enabled": live_config.battery_control_enabled},
    )
    return {
        "status": profile_status(resolution),
        "matched_device_id": resolution.matched_device_id,
        "bindings": {
            role: {"entity_id": b.entity_id, "source": b.source}
            for role, b in resolution.bindings.items()
        },
        "unresolved_required": list(resolution.unresolved_required),
        "conflicts": [
            {
                "role": c.role,
                "chosen": {"entity_id": c.chosen.entity_id, "source": c.chosen.source},
                "rejected": [{"entity_id": r.entity_id, "source": r.source} for r in c.rejected],
            }
            for c in resolution.conflicts
        ],
    }


def create_app(
    status_store: dict[str, Any],
    config: Any = None,          # Config dataclass instance
    supervisor_token: str = "",
    store: Any = None,           # EnergyStore instance
) -> FastAPI:
    """Create FastAPI app; status_store is the shared dict updated by EMS loop."""
    app = FastAPI(title="miniEMS", docs_url=None, redoc_url=None)
    app.state.supervisor_token = supervisor_token

    app.mount("/static", StaticFiles(directory=str(_DIR / "static")), name="static")

    # ── Dashboard ──────────────────────────────────────────────────────

    @app.get("/", response_class=HTMLResponse)
    async def dashboard(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "dashboard.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/api/status")
    async def api_status() -> dict[str, Any]:
        return status_store

    # ── Settings ───────────────────────────────────────────────────────

    @app.get("/settings", response_class=HTMLResponse)
    async def settings_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "settings.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/log", response_class=HTMLResponse)
    async def log_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "log.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/config-json", response_class=HTMLResponse)
    async def config_json_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "config_json.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/options-json", response_class=HTMLResponse)
    async def options_json_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "options_json.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/database", response_class=HTMLResponse)
    async def database_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "database.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/api/database")
    async def api_database() -> JSONResponse:
        if store is None:
            return JSONResponse({"rows": [], "error": "Store not available"})
        rows = await store.query_all_days()
        return JSONResponse({"rows": rows})

    # ── Devices (read-only preview, see docs/roadmap/v3.0-geraeteprofile.md) ──

    @app.get("/devices", response_class=HTMLResponse)
    async def devices_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "devices.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/api/devices")
    async def api_devices() -> JSONResponse:
        """What HA's energy dashboard and device/entity registry know –
        purely informational, nothing here feeds the control path yet.
        Degrades to an `error` field rather than a 5xx: the WS registry
        query is a nice-to-have preview, not something a dashboard visit
        should ever fail on.
        """
        llt = getattr(config, "long_lived_token", "") if config is not None else ""
        try:
            prefs, raw_devices, raw_entities = await ha_ws_api.get_registry_snapshot(llt)
        except ha_ws_api.HAWebSocketError as exc:
            return JSONResponse({"error": f"Could not query Home Assistant: {exc}"})

        energy_map = parse_energy_prefs(prefs)
        snapshot = RegistrySnapshot.from_lists(raw_devices, raw_entities)

        devices = [
            {
                "manufacturer": device.manufacturer,
                "model": device.model,
                "name": device.name,
                "entity_count": len(snapshot.entities_of(device.device_id)),
            }
            for device in snapshot.devices.values()
        ]
        devices.sort(key=lambda d: (d["manufacturer"] or "", d["name"] or ""))

        # Mapping preview: resolve every implemented class's roles against
        # the same data, so the page shows what miniEMS would actually bind
        # – not just what HA reports. Purely a preview: resolve_class() is
        # not called anywhere in the control path yet, see
        # docs/roadmap/v3.0-geraeteprofile.md. "battery" is left out here –
        # its only profile (pylontech_force) is unverified and the legacy
        # config has never had a separate battery slot to compare against.
        live_config = config if config is not None else Config()
        # entity_overrides (explicit, "<class>.<role>" keyed – set via the raw
        # config.json editor today, an entity-picker later) wins over a
        # legacy *_entity field that merely differs from its default: both
        # are resolution source 1, but entity_overrides is the more
        # deliberate signal of the two. resolve_class() only ever reads the
        # keys prefixed for the class it was called with, so one merged dict
        # can be reused across every class's call.
        overrides = {**inverter_overrides(live_config, Config()), **(live_config.entity_overrides or {})}
        catalog = load_role_catalog()
        profiles = load_profiles()
        resolutions = {
            class_name: _resolve_class_json(
                class_name, catalog=catalog, overrides=overrides, energy_map=energy_map,
                registry=snapshot, profiles=profiles, live_config=live_config,
            )
            for class_name in ("inverter", "energy_meter")
        }

        return JSONResponse({
            "energy_dashboard": {
                "candidates": energy_map.candidates,
                "signs": {k: {"mode": v.mode, "positive": v.positive} for k, v in energy_map.signs.items()},
                "split_candidates": energy_map.split_candidates,
                "battery_capacity_kwh": energy_map.battery_capacity_kwh,
            },
            "devices": devices,
            "resolutions": resolutions,
        })

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        """Return current config from disk (config.json or dataclass defaults)."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                data.pop("_version", None)
                return data
            except Exception as exc:
                _LOGGER.warning("Failed to read config file: %s", exc)
        # Fall back to in-memory config
        if config is not None:
            return {
                f.name: getattr(config, f.name)
                for f in dc_fields(config)
            }
        return {}

    @app.post("/api/config")
    async def save_config(request: Request) -> JSONResponse:
        """Save updated config to disk and restart the addon via Supervisor."""
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        # Load existing config to preserve unknown/internal keys
        existing: dict = {}
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    existing = json.load(f)
            except Exception as exc:
                _LOGGER.warning("Failed to read existing config for merge: %s", exc)

        # Merge and coerce types
        for key, value in body.items():
            if not key.startswith("_"):
                existing[key] = _coerce(key, value)

        try:
            with open(CONFIG_FILE, "w", encoding="utf-8") as f:
                json.dump(existing, f, indent=2)
        except OSError as exc:
            _LOGGER.error("Failed to write config: %s", exc)
            return JSONResponse({"error": f"Write failed: {exc}"}, status_code=500)

        _LOGGER.info("Config saved – scheduling addon restart")

        async def _do_restart() -> None:
            await asyncio.sleep(0.4)   # let the response reach the browser first
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        SUPERVISOR_RESTART_URL,
                        headers={"Authorization": f"Bearer {supervisor_token}"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
            except Exception:
                pass   # process will be killed before the response comes back

        asyncio.create_task(_do_restart())
        return JSONResponse({"status": "restarting"})

    # ── Raw file viewer / editor ────────────────────────────────────────

    _RAW_FILES = {
        "config":  CONFIG_FILE,
        "options": OPTIONS_FILE,
    }

    @app.get("/api/rawfile/{name}")
    async def raw_file_get(name: str) -> JSONResponse:
        path = _RAW_FILES.get(name)
        if path is None:
            return JSONResponse({"error": "Unknown file"}, status_code=404)
        if not os.path.exists(path):
            return JSONResponse({})
        try:
            with open(path, encoding="utf-8") as f:
                return JSONResponse(json.load(f))
        except (OSError, json.JSONDecodeError) as exc:
            return JSONResponse({"error": str(exc)}, status_code=500)

    @app.post("/api/rawfile/{name}")
    async def raw_file_post(name: str, request: Request) -> JSONResponse:
        path = _RAW_FILES.get(name)
        if path is None:
            return JSONResponse({"error": "Unknown file"}, status_code=404)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        try:
            with open(path, "w", encoding="utf-8") as f:
                json.dump(body, f, indent=2)
        except OSError as exc:
            _LOGGER.error("Failed to write %s: %s", path, exc)
            return JSONResponse({"error": f"Write failed: {exc}"}, status_code=500)

        _LOGGER.info("Raw file %s saved via UI – scheduling addon restart", path)

        async def _do_raw_restart() -> None:
            await asyncio.sleep(0.4)
            try:
                async with aiohttp.ClientSession() as session:
                    await session.post(
                        SUPERVISOR_RESTART_URL,
                        headers={"Authorization": f"Bearer {supervisor_token}"},
                        timeout=aiohttp.ClientTimeout(total=5),
                    )
            except Exception:
                pass

        asyncio.create_task(_do_raw_restart())
        return JSONResponse({"status": "restarting"})

    return app
