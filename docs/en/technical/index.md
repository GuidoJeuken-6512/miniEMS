---
revision_date: 2026-04-07
---

# Technical Reference

This section documents the internal design of miniEMS v1.5.3 for contributors and advanced users who want to understand how the system works.

## Module Map

| File | Role |
|---|---|
| `main.py` | Entry point; wires all components, manages asyncio tasks |
| `const.py` | Shared constants: `EMSMode` enum, schema version, API URLs |
| `config_loader.py` | Loads & merges config; runs migration; exposes `Config` dataclass |
| `migration.py` | Schema-version migrations for `config.json` (v0 → v10) |
| `ha_ws_client.py` | Polls HA entity states via REST; handles token fallback |
| `ems_controller.py` | Determines operating mode; wires all sub-systems per tick |
| `cost_optimizer.py` | Accumulates daily/weekly energy cost & savings; detects downtime gaps |
| `consumption_model.py` | Predicts load from history; temperature-based fallback |
| `battery_model.py` | Computes free-to-charge and useable kWh from SoC |
| `sensor_validator.py` | Spike detection — rejects implausible power readings |
| `solcast_client.py` | Reads Solcast PV forecast entities from HA state cache |
| `event_log.py` | Ring buffer (100 entries) for mode-change events |
| `weather_client.py` | Calls `weather.get_forecasts` HA action; 30-min cache |
| `store.py` | SQLite persistence for daily energy history (`/data/miniems.db`) |
| `inverter_controller.py` | Writes charge/discharge controls to inverter entities |
| `mqtt_publisher.py` | Publishes sensors via MQTT Discovery |
| `ha_sensor_publisher.py` | Pushes sensor states to HA Core REST API (fallback) |
| `web_server.py` | FastAPI; serves dashboard, settings, log, and `/api/status` |
| `templates/dashboard.html` | Dashboard Jinja2 template with JS auto-refresh |
| `templates/settings.html` | Settings form Jinja2 template |
| `templates/log.html` | Full mode-change log Jinja2 template |
| `translations/en.yaml` | English UI strings |
| `translations/de.yaml` | German UI strings |
| `static/style.css` | Dashboard CSS |

## Where to Start

- [Architecture](architecture.md) — component diagram, asyncio task graph, auth flow
- [Calculations](calculations.md) — all formulas used in the EMS loop
- [Data Storage](data-storage.md) — config files, SQLite schema, in-memory state
- [API Reference](api.md) — internal HTTP endpoints
- [HA Sensor Reference](sensors.md) — all 28 native Home Assistant sensors
