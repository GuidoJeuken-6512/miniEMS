# Copilot Instructions for miniEMS Home Assistant Add-on

## Project Overview

- **miniEMS** is a modular Python add-on for Home Assistant, focused on energy management and cost analysis.
- The backend is built with **FastAPI** and exposes both a web UI (via Home Assistant Ingress) and a REST API.
- Data is collected from Home Assistant sensors, stored in SQLite, and used for cost comparison between energy strategies.

## Key Components

- `miniems_app.py`: FastAPI main app, CORS, lifespan, route registration, background workers.
- `config.py`: Pydantic config models, load/save logic, bashio integration.
- `database.py`: SQLite operations, schema migrations.
- `cost_calculation.py`: Core cost analysis logic.
- `ha_integration.py`: Home Assistant API, sensor discovery, updates, and naming conventions.
- `utils.py`: Helpers for schedule validation, unit conversion, sensor type detection.
- `migrations.py`: Handles DB schema upgrades.
- All code is under `miniems/rootfs/usr/bin/`.

## Developer Workflows

- **Build**: Use the provided `Dockerfile` and `requirements.txt` for reproducible builds. See Home Assistant add-on docs for build details.
- **Start/Restart**: Use VS Code tasks: "Start Home Assistant", "Restart Home Assistant".
- **Logs**: Check `/tmp/supervisor.log` or use Home Assistant's add-on log panel.
- **API Testing**: Access endpoints like `/api/health`, `/api/sensors/energy`, `/api/config` via the web UI or direct HTTP requests.

## Project-Specific Patterns & UI Structure

- **Sensor Naming**: Follows `sensor.miniems_cost_{comparison_type}_{scenario}_{metric}_{period}` (see DOCS.md for details).
- **No template engine**: All HTML is defined as Python strings directly in the FastAPI route functions.
- **Page Structure**: Each main UI page (Hauptseite, Energiekosten, etc.) is a separate FastAPI route with its own HTML string. There is no shared template logic.
- **Cost Summary Block**: The cost summary block (Hauskosten, PV, etc.) is only rendered on the Energiekosten page, not on the Hauptseite/main page.
- **Background workers**: Use module-level variables for status, started via FastAPI lifespan.
- **Routes**: Grouped by domain (sensors, config, energy_costs, logs, health) and registered via FastAPI routers. Each UI page is a separate route.
- **Configuration**: Managed via YAML and Pydantic models, with atomic file writes.
- **Database**: Schema migrations are versioned and handled on startup.

## Integration Points

- **Home Assistant**: Communicates via REST API, uses long-lived tokens if needed.
- **Docker**: All code runs in a container; see `Dockerfile` for build steps and dependencies.

## Conventions & Gotchas

- All sensor/entity IDs and attributes must match Home Assistant conventions for discovery.
- Timezones and energy prices are hardcoded; update as needed for new deployments.
- Avoid duplicate endpoints (e.g., `/api/energy-costs/current`).
- All imports must be updated if modules are refactored.

## References

- See `miniEMS/README.md` and `miniEMS/DOCS.md` for detailed architecture, API, and integration info.
- Example API endpoints and sensor conventions are documented in `miniEMS/README.md` and `miniEMS/DOCS.md`.

---

For any unclear or missing patterns, please provide feedback to improve these instructions.
