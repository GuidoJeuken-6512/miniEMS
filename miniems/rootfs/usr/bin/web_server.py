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

from const import CONFIG_FILE, OPTIONS_FILE, SUPERVISOR_RESTART_URL, VERSION

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
_BOOL_FIELDS = {"battery_control_enabled", "battery_control_simulation"}
_INT_FIELDS = {
    "battery_min_soc", "battery_max_soc", "pv_surplus_threshold_w",
    "update_interval_sec", "battery_max_charge_power_w", "battery_max_discharge_power_w",
    "default_discharge_power_w", "event_log_retention_days",
}
_FLOAT_FIELDS = {
    "battery_capacity_kwh", "cheap_rate_threshold_eur", "medium_rate_threshold_eur",
    "openweathermap_lat", "openweathermap_lon",
    "feed_in_tariff_eur_kwh", "fix_price",
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
    return str(value) if value is not None else ""


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
            "dashboard.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
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
            "settings.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/log", response_class=HTMLResponse)
    async def log_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            "log.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/config-json", response_class=HTMLResponse)
    async def config_json_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            "config_json.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/options-json", response_class=HTMLResponse)
    async def options_json_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            "options_json.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/database", response_class=HTMLResponse)
    async def database_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            "database.html", {"request": request, "version": VERSION, "translations": translations, "lang": lang}
        )

    @app.get("/api/database")
    async def api_database() -> JSONResponse:
        if store is None:
            return JSONResponse({"rows": [], "error": "Store not available"})
        rows = await store.query_all_days()
        return JSONResponse({"rows": rows})

    @app.get("/api/config")
    async def api_config() -> dict[str, Any]:
        """Return current config from disk (config.json or dataclass defaults)."""
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, encoding="utf-8") as f:
                    data = json.load(f)
                data.pop("_version", None)
                return data
            except Exception:
                pass
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
            except Exception:
                pass

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
