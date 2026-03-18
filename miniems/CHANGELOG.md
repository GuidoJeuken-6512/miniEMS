<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 1.3.0

### New Features
- **Settings page** in the miniEMS dashboard: all config options are editable
  in the browser; *Save & Restart* writes `/data/config.json` and triggers an
  addon restart via the Supervisor API.
- **Forecast & Prediction** (Phase 3):
  - `weather_client.py`: fetches 24 h OpenWeatherMap forecast (8 × 3 h slots),
    derives average night temperature, PV yield factor and day length.
    Cache TTL: 3 hours.
  - `consumption_model.py`: predicts today's load (temperature-matched
    historical days → 30-day median fallback) and PV yield (75th-percentile
    peak PV × cloud factor × daylight hours).
  - Smart grid-charge gating: `GRID_CHARGING` mode is only triggered when the
    model recommends it (`battery + predicted_pv < predicted_load`).
    Falls back to always-charge during cheap rates when not enough history
    exists (`confidence = none`).
  - SQLite: new `peak_pv_w` and `avg_outdoor_temp_c` columns in `daily_stats`.
- **2 new HA sensors**: `sensor.miniems_predicted_load_kwh`,
  `sensor.miniems_predicted_pv_kwh` (MQTT + REST fallback).
- **Confidence badge** on dashboard: `high` / `low` / `none`.
- **SIM badge** on dashboard mode indicator when battery control is in
  simulation mode.

### Config additions
- `outdoor_temp_entity` – optional HA temperature sensor for historical matching
- `openweathermap_api_key` – OWM API key (leave empty to disable forecast)
- `openweathermap_lat` / `openweathermap_lon` – location for forecast queries

### Schema migration
Config schema v2 → v3 (adds 4 new forecast fields with safe defaults).

---

## 1.2.0

### New Features
- **Battery control** (Phase 2): addon actively controls the Deye inverter via
  HA service calls based on EMS mode.
  - Sets inverter work mode, max charge power, and max discharge power.
  - **Simulation mode** (`battery_control_simulation: true`, default): all
    commands are logged as `[SIM]` but not executed. Safe for testing.
  - **Idempotent**: service calls only sent when value actually changes.
- **4 new config fields**: `battery_control_enabled`,
  `battery_control_simulation`, `battery_max_charge_power_w`,
  `battery_max_discharge_power_w`.
- **5 new entity config fields**: `inverter_work_mode_entity`,
  `inverter_charge_power_entity`, `inverter_discharge_power_entity`,
  `inverter_charge_mode_charge`, `inverter_charge_mode_selfuse`.
- **2 new HA sensors**: `sensor.miniems_charge_power_limit_w`,
  `sensor.miniems_discharge_power_limit_w` (MQTT + REST).

### Schema migration
Config schema v1 → v2 (adds battery control fields with safe defaults).

---

## 1.1.0

### New Features
- **MQTT Discovery**: sensors published via MQTT with `unique_id`, device
  grouping and long-term statistics support. Falls back to REST when Mosquitto
  is not installed.
- **SQLite persistence** (`/data/miniems.db`): daily stats survive restarts.
  Running totals restored from DB on startup — values no longer reset to 0.
- **7 new HA sensors**: `today_load_total_kwh`, `today_load_cost_eur`,
  `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur`,
  `year_grid_cost_eur`, `year_pv_savings_eur`.
- VS Code task "Update and Start Addon" for fast code-only redeployment.

### Bugfixes
- Entity IDs were doubling the device name prefix
  (`sensor.miniems_miniems_…`) — fixed by using short sensor names in MQTT
  Discovery and relying on HA's device-name prepending.

---

## 1.0.1

### Bugfixes
- Rebuild / Update VS Code tasks now patch `homeassistant_api` and
  `services: mqtt:need` into the supervisor in-memory cache to work around
  a supervisor bug in dev builds.

---

## 1.0.0

- Initial release of miniEMS
- WebSocket client for live HA entity states (no polling)
- EMS decision logic: Idle / PV Charging / Grid Charging / Battery Protection
- Cost accounting: grid import cost, PV savings, grid import kWh, PV used kWh
- Weekly aggregated cost/savings sensors
- FastAPI ingress dashboard with auto-refresh
- Config persistence with schema migration (`options.json` → `config.json`)
