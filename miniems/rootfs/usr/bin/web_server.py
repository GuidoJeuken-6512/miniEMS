"""FastAPI ingress dashboard for miniEMS.

HTML lives in templates/, CSS in static/style.css.
"""

import asyncio
import json
import logging
import os
from dataclasses import fields as dc_fields
from datetime import date, timedelta
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
from history_backfill import backfill_daily_stats
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
    "load_forecast_enabled", "history_backfill_enabled",
    "hw_control_enabled", "hw_control_simulation",
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
    "load_forecast_min_days",
    "hp_mode_hot_water_code", "hp_request_hot_water_code", "hp_cop_settle_sec",
    "hw_write_scale", "hw_pv_min_export_w", "hw_min_off_sec",
}
# NOTE: any float field missing here falls through to str(value) in _coerce()
# and is persisted as a string, which then raises TypeError on first use.
_FLOAT_FIELDS = {
    "battery_capacity_kwh", "cheap_rate_threshold_eur", "medium_rate_threshold_eur",
    "feed_in_tariff_eur_kwh", "fix_price",
    "daily_base_price_eur", "avg_discharge_tariff_eur_kwh",
    "pv_charge_margin_factor", "pv_charge_hysteresis_frac", "grid_charge_min_free_kwh",
    "grid_charge_min_margin_eur_kwh",
    "heating_limit_temp_c", "pv_forecast_blend_weight",
    "hot_water_min_temp_c", "hw_draw_drop_k",
    "hw_volume_l", "hw_day_target_c", "hw_morning_target_c", "hw_boost_delta_k", "hw_idle_setpoint_c",
    "hw_max_temp_c", "hw_heat_power_kw", "hw_cop_prior",
    "hw_cop_prior_temp_c", "hw_adhoc_delta_k", "hw_decision_tolerance_pct",
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
    hot_water: Any = None,       # HotWaterController instance
    inverter: Any = None,        # InverterController instance
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
        # status_store is only refreshed per EMS tick; the hot-water part is
        # read live so a button press shows up at once.
        if hot_water is not None and hot_water.enabled and "hot_water" in status_store:
            return {**status_store, "hot_water": hot_water.status()}
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

    @app.get("/heatpump", response_class=HTMLResponse)
    async def heatpump_page(request: Request) -> HTMLResponse:
        lang = await get_ha_language(request)
        translations = load_translations(lang)
        return _TEMPLATES.TemplateResponse(
            request, "heatpump.html", {"version": const.VERSION, "translations": translations, "lang": lang}
        )

    @app.post("/api/hot_water/adhoc")
    async def api_hot_water_adhoc(request: Request) -> JSONResponse:
        """Start or cancel a manual hot-water charge. Written exactly like an
        automatic one – setpoint, confirmation and simulation all apply."""
        if hot_water is None or not hot_water.enabled:
            return JSONResponse({"error": "hot-water control not configured"}, status_code=409)
        try:
            body = await request.json()
        except Exception:
            body = {}
        action = (body or {}).get("action")
        if action == "start":
            return JSONResponse(await hot_water.request_adhoc())
        if action == "cancel":
            return JSONResponse(await hot_water.cancel_adhoc())
        return JSONResponse({"error": "action must be 'start' or 'cancel'"}, status_code=400)

    @app.get("/api/hot_water/history")
    async def api_hot_water_history() -> JSONResponse:
        if store is None:
            return JSONResponse({"hours": [], "segments": [], "draws": []})
        since = date.today() - timedelta(days=2)
        hours = await store.query_hp_hours_since(since)
        return JSONResponse({
            "hours": hours[-48:],
            "segments": await store.query_loss_segments(limit=10),
            "draws": await store.query_draws(limit=20),
        })

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

    # ── Control mode: simulation / battery / heat pump, without restart ──

    mode_lock = asyncio.Lock()

    def _control_mode() -> dict[str, Any]:
        c = config
        return {
            # One switch for both: on as soon as either part simulates (the safe reading).
            "simulation": bool(c.battery_control_simulation or c.hw_control_simulation),
            "simulation_mixed": c.battery_control_simulation != c.hw_control_simulation,
            "battery_control": bool(c.battery_control_enabled),
            "hw_control": bool(c.hw_control_enabled),
            "battery_live": bool(c.battery_control_enabled and not c.battery_control_simulation),
            "hw_live": bool(c.hw_control_enabled and not c.hw_control_simulation),
            "history_backfill": bool(c.history_backfill_enabled),
        }

    @app.get("/api/control_mode")
    async def api_control_mode_get() -> JSONResponse:
        if config is None:
            return JSONResponse({"error": "no config"}, status_code=409)
        return JSONResponse(_control_mode())

    @app.post("/api/control_mode")
    async def api_control_mode_post(request: Request) -> JSONResponse:
        """Switch simulation / battery control / hot-water control at runtime.

        Applied to the live Config at once (the controllers read it every tick)
        and persisted to config.json – no restart. Leaving live mode first puts
        the device back into its safe state, exactly like a shutdown would.
        """
        if config is None:
            return JSONResponse({"error": "no config"}, status_code=409)
        try:
            body = await request.json()
        except Exception:
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)
        if not isinstance(body, dict):
            return JSONResponse({"error": "Invalid JSON"}, status_code=400)

        updates: dict[str, bool] = {}
        if "simulation" in body:
            sim = _coerce("battery_control_simulation", body["simulation"])
            updates["battery_control_simulation"] = sim
            updates["hw_control_simulation"] = sim
        if "battery_control" in body:
            updates["battery_control_enabled"] = _coerce("battery_control_enabled", body["battery_control"])
        if "hw_control" in body:
            updates["hw_control_enabled"] = _coerce("hw_control_enabled", body["hw_control"])
        if "history_backfill" in body:
            updates["history_backfill_enabled"] = _coerce(
                "history_backfill_enabled", body["history_backfill"])
        if not updates:
            return JSONResponse({"error": "nothing to change"}, status_code=400)

        async with mode_lock:
            before = _control_mode()
            new_bat_live = (updates.get("battery_control_enabled", config.battery_control_enabled)
                            and not updates.get("battery_control_simulation",
                                                config.battery_control_simulation))
            new_hw_live = (updates.get("hw_control_enabled", config.hw_control_enabled)
                           and not updates.get("hw_control_simulation", config.hw_control_simulation))

            # Leave live mode safely – while the old flags still allow real writes.
            if before["battery_live"] and not new_bat_live and inverter is not None:
                try:
                    await asyncio.wait_for(inverter.restore_safe_defaults(), timeout=10)
                except Exception as exc:
                    _LOGGER.error("Inverter restore on mode switch failed: %s", exc)
            if before["hw_live"] and not new_hw_live and hot_water is not None:
                try:
                    await asyncio.wait_for(hot_water.restore_safe_defaults(), timeout=10)
                except Exception as exc:
                    _LOGGER.error("Hot-water restore on mode switch failed: %s", exc)

            backfill_switched_on = (updates.get("history_backfill_enabled")
                                    and not config.history_backfill_enabled)
            for key, value in updates.items():
                setattr(config, key, value)
            # Simulated writes count as confirmed without being sent – start fresh.
            for ctrl in (inverter, hot_water):
                if ctrl is not None:
                    ctrl.reset_write_state()

            after = _control_mode()
            _LOGGER.info("Control mode switched: battery %s, hot water %s",
                         "LIVE" if after["battery_live"] else
                         ("SIMULATION" if after["battery_control"] else "off"),
                         "LIVE" if after["hw_live"] else
                         ("SIMULATION" if after["hw_control"] else "off"))

            existing: dict = {}
            if os.path.exists(CONFIG_FILE):
                try:
                    with open(CONFIG_FILE, encoding="utf-8") as f:
                        existing = json.load(f)
                except Exception as exc:
                    _LOGGER.warning("Failed to read existing config for merge: %s", exc)
            existing.update(updates)
            try:
                # Atomic: a crash mid-write must not leave a broken config.json.
                tmp = CONFIG_FILE + ".tmp"
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(existing, f, indent=2)
                    f.flush()
                    os.fsync(f.fileno())
                os.replace(tmp, CONFIG_FILE)
            except OSError as exc:
                _LOGGER.error("Failed to write config: %s", exc)
                return JSONResponse({**after, "error": f"active, but not saved: {exc}"},
                                    status_code=500)
        if backfill_switched_on and store is not None:
            # Normally runs at startup; switching it on should not need a restart.
            _LOGGER.info("History backfill switched on – running it now")
            asyncio.create_task(backfill_daily_stats(config, store), name="history_backfill")
        return JSONResponse(after)

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
