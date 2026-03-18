# miniEMS Refined Implementation Plan

## Context

The original `docs/plan.md` defines the intended behavior for miniEMS: a Home Assistant addon that manages a Deye inverter with a home battery using dynamic electricity pricing, Solcast PV forecasting, and temperature-based load prediction. A significant portion of this has already been implemented, but several key features differ or are missing. This plan identifies the gaps and defines what needs to change.

---

## Gap Analysis: Plan vs. Current Implementation

| Feature | Plan | Current App | Status |
|---|---|---|---|
| Solcast PV forecast sensors | Yes (3 sensors) | No – uses internal weather-based estimate | **Missing** |
| Grid charging via switch entity | `switch.*_battery_grid_charging` | Uses work-mode entity | **Diverges** |
| Discharge power = 0 when grid charging | Yes (set to 185 normally) | Not specified this way | **Diverges** |
| `battery_kwh_freetochange` sensor | Yes | Not published | **Missing** |
| `battery_kwh_useable` sensor | Yes | Not published | **Missing** |
| `cost_without_grid_charge` sensor | Yes | Not tracked | **Missing** |
| `cost_fix_price_tarif` sensor | Yes | `load_cost_eur` (partial) | **Incomplete** |
| `real_costs` sensor | Yes | `grid_cost_eur` (partial) | **Incomplete** |
| Feed-in tariff config (€/kWh) | Yes (default 0.08) | Not present | **Missing** |
| fix_price config (€/kWh) | Yes (default 0.30) | Present as `fix_rate` | **Exists** |
| Max SOC default 95 | Yes | Present | **Exists** |
| Battery capacity 25kWh default | Yes | Present | **Exists** |
| Discharge power default 185W | Specified | Not specified | **Missing** |
| Frontend log panel (mode changes) | Yes | Not present | **Missing** |
| Frontend missing sensor warnings | Yes | Not present | **Missing** |
| Frontend missing config warnings | Yes | Not present | **Missing** |
| Temperature fallback prediction rules | Yes (3 rules) | Rolling median fallback | **Diverges** |
| Config from frontend only | Yes | Partly in config.yaml | **Incomplete** |
| 5-min EMS schedule | Yes | Configurable (30s default) | **Keep 30s, keep configurable** |

---

## Cost Calculation & Resilience Evaluation

### Cost Calculation Logic

The formulas are **mathematically correct**:
```
energy_kwh = (power_w / 1000) * (interval_sec / 3600)
cost_eur   = energy_kwh * price_eur_kwh
```

Each tick accumulates: `grid_import_kwh`, `grid_cost_eur`, `pv_used_kwh`, `pv_savings_eur`, `load_total_kwh`, `load_cost_eur`. These are flushed to SQLite after **every tick** and restored on restart via `restore_today()`.

**Architectural gap resolved**: The `sensor.deye8k_battery_power` sensor provides the missing separation. Using the power balance equation, the grid-to-battery flow can be derived directly from existing sensors at every tick — no EMS mode tracking required.

### Resilience Assessment

| Scenario | Current Behavior | Risk |
|---|---|---|
| **Addon reboot** | `restore_today()` loads today's DB row; data since last flush (≤30s) is lost | Low – max 30s gap |
| **Addon update** | SQLite persists; accumulators restored on startup | Low |
| **Downtime gap** (e.g. 5 min crash) | No mechanism to detect or fill the gap – energy during downtime is silently lost | Medium |
| **Sensor spike** | No validation; `pv_w = 999 kW` for one tick adds ~16 kWh to daily totals permanently | High |
| **Sensor unavailable** | Defaults to 0.0 → undercounts, not overcounts | Low |
| **DB rounding** | Values rounded to 4 decimal places on every flush/restore; cumulative precision loss | Low |

### Grid-to-Battery Power Derivation

**Sensor convention** (`sensor.deye8k_battery_power`):
- `battery_power_w > 0` → battery discharging
- `battery_power_w < 0` → battery charging

**Power balance at each tick**:
```
pv_power + grid_power + battery_power = load_power
```

**Derived quantities**:
```python
pv_surplus_w     = max(0, pv_power_w - load_power_w)
battery_charge_w = max(0, -battery_power_w)            # > 0 only when charging
grid_charge_w    = max(0, battery_charge_w - pv_surplus_w)  # grid portion to battery
```

This is **more accurate than EMS mode tracking** because:
- Reflects actual power flow, not intended mode
- Correctly credits PV for partially covering battery charging
- Works even when the inverter charges outside of EMS-controlled modes

### Required Improvements

1. **Sensor spike detection**: Compare incoming value to previous; if delta exceeds a threshold (e.g. >50% change AND >500W delta in one tick), skip accumulation and log a warning.
2. **Grid charge energy tracking**: Add separate accumulators `grid_charge_kwh` and `grid_charge_cost_eur` in `cost_optimizer.py`, computed from `grid_charge_w` (derived above) — no EMS mode dependency needed. Enables `cost_without_grid_charge = grid_cost_eur - grid_charge_cost_eur`.
3. **Downtime gap detection**: On startup, compare last DB flush timestamp to current time. If gap > 1 interval, log a warning to frontend so user knows accounting has a gap.
4. **DB precision**: Store values with 6 decimal places to avoid cumulative rounding errors.

---

## Code Organization Principles

All code must remain **maintainable and readable**. New functionality is split into dedicated modules rather than growing existing files. Each file has a single, clear responsibility.

### Existing Module Map

```
main.py                 → Entry point, async task wiring
config_loader.py        → Config loading, defaults, persistence
const.py                → Constants and enums
migration.py            → DB schema migrations
store.py                → SQLite access layer (daily_stats table)
ha_ws_client.py         → HA state polling (WebSocket/REST)
ha_sensor_publisher.py  → HA REST sensor publishing fallback
mqtt_publisher.py       → MQTT discovery + publishing
web_server.py           → FastAPI routes, /api/status, /api/settings
ems_controller.py       → EMS mode decision loop
inverter_controller.py  → HA service calls to control inverter
cost_optimizer.py       → Energy/cost accumulation per tick
consumption_model.py    → Load prediction from DB + weather
weather_client.py       → HA weather entity integration
```

### New Files to Create (keep existing files focused)

| New File | Responsibility |
|---|---|
| `sensor_validator.py` | Spike detection, sanity checks on incoming sensor values |
| `solcast_client.py` | Polls Solcast HA entities; exposes `remaining_today_kwh`, `today_kwh`, `tomorrow_kwh` |
| `battery_model.py` | Computes `battery_kwh_freetochange`, `battery_kwh_useable`; encapsulates battery state math |
| `event_log.py` | In-memory ring buffer for EMS mode-change events; used by frontend log panel |

### Existing Files: Scope of Changes (keep changes minimal and focused)

- **`ems_controller.py`**: Only update `_determine_mode()` logic and call `battery_model` + `event_log` + `solcast_client`. No new computation logic inline.
- **`cost_optimizer.py`**: Add two new accumulator fields and call `sensor_validator` before accumulating. No restructuring.
- **`inverter_controller.py`**: Add `enable_grid_charge()` / `disable_grid_charge()` using switch entity. Keep existing method structure.
- **`store.py`**: Add new columns and migrations only. No logic changes.
- **`web_server.py`**: Add `warnings` and `log` to `/api/status` response. No restructuring.

---

## Implementation Steps

### 1. Solcast PV Forecast Integration

**Goal**: Use Solcast sensors instead of internal weather-based PV yield estimate.

**Changes**:
- Add 3 new config fields in `config.yaml` and `config_loader.py`:
  - `solcast_today_entity` (e.g. `sensor.solcast_pv_forecast_prognose_heute`)
  - `solcast_tomorrow_entity` (e.g. `sensor.solcast_pv_forecast_prognose_morgen`)
  - `solcast_remaining_today_entity` (e.g. `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute`)
- Register these in `ha_ws_client.py` for state polling
- Create `solcast_client.py` to wrap Solcast entity reads
- Replace internal PV yield estimate with Solcast remaining-today value in `ems_controller.py`
- Use `solcast_remaining_today` for the grid-charge decision

**Files**: `config_loader.py`, `ha_ws_client.py`, `ems_controller.py`, `config.yaml`, `solcast_client.py` (new)

---

### 2. Inverter Control Refactor (Grid Charging)

**Goal**: Align grid charge control with the plan's switch+discharge-power mechanism.

**Current**: Uses work-mode entity (Charging Priority / Self-Use).
**Plan**: Uses `switch.*_battery_grid_charging` + `number.*_battery_discharging_power`.

**Changes**:
- Add 2 new config fields:
  - `grid_charge_switch_entity` (e.g. `switch.deye8k_battery_grid_charging`)
  - `battery_discharging_power_entity` (e.g. `number.deye8k_battery_discharging_power`)
- Update `inverter_controller.py`:
  - `enable_grid_charge()`: call `switch.turn_on(grid_charge_switch_entity)` + set `battery_discharging_power_entity = 0`
  - `disable_grid_charge()`: call `switch.turn_off(grid_charge_switch_entity)` + restore `battery_discharging_power_entity` to configured default (default: 185W)
- Add config field `default_discharge_power_w` (default: 185)
- Keep simulation mode (log-only)

**Files**: `inverter_controller.py`, `config_loader.py`, `config.yaml`

---

### 3. New Computed Sensors

**Goal**: Publish `battery_kwh_freetochange`, `battery_kwh_useable`, `cost_without_grid_charge`, `cost_fix_price_tarif`, `real_costs`.

**Formulas**:
- `battery_kwh_freetochange = (max_soc – soc) / 100 * battery_capacity_kwh`
- `battery_kwh_useable = (soc – min_soc) / 100 * battery_capacity_kwh`
- `real_costs` = actual grid cost paid (energy imported × current price)
- `cost_without_solar` = load_power × price (what cost would be without PV)
- `cost_fix_price_tarif` = load_total_kwh × fix_price
- `cost_without_grid_charge` = `grid_cost_eur - grid_charge_cost_eur`

**Changes**:
- Create `battery_model.py` — `BatteryModel(cfg)` with `free_to_charge_kwh(soc)` and `useable_kwh(soc)`
- Call `BatteryModel` in `ems_controller.py` each cycle; add values to `status_store`
- Add `grid_charge_kwh` and `grid_charge_cost_eur` accumulators in `cost_optimizer.py`, derived each tick from power sensors (no EMS mode dependency):
  ```python
  pv_surplus_w     = max(0, pv_power_w - load_power_w)
  battery_charge_w = max(0, -battery_power_w)          # > 0 only when charging
  grid_charge_w    = max(0, battery_charge_w - pv_surplus_w)
  ```
  enables `cost_without_grid_charge = grid_cost_eur - grid_charge_cost_eur`
- Add `grid_charge_kwh` and `grid_charge_cost_eur` columns to `daily_stats` DB table (migration required)
- Create `sensor_validator.py` — `validate_power(entity, value, previous)` returns `None` on spike (>50% change AND >500W delta)
- Call `sensor_validator` in `cost_optimizer.record_tick()` before accumulating
- Add feed-in tariff config field: `feed_in_tariff_eur_kwh` (default: 0.08)
- Track feed-in revenue when `grid_power_w < 0` (export): `feed_in_kwh * feed_in_tariff`
- On startup: compare last DB flush timestamp to `now()`; if gap > 2× interval, add a warning to the frontend warnings list
- Publish all new sensors via `mqtt_publisher.py`

**Files**: `ems_controller.py`, `cost_optimizer.py`, `mqtt_publisher.py`, `store.py`, `migration.py`, `battery_model.py` (new), `sensor_validator.py` (new)

---

### 4. Grid Charge Logic Alignment

**Goal**: Match plan's grid charge decision logic exactly.

**Logic** (each EMS interval):
```
if price < cheap_rate_threshold
   AND battery_kwh_freetochange > solcast_remaining_today:
    → Grid Charge ON
    → discharging_power = 0
else:
    → Grid Charge OFF
    → discharging_power = default_discharge_power_w (185)
```

**Changes**:
- Keep default EMS interval at 30s (already configurable via `ems_interval_seconds`)
- Update `ems_controller._determine_mode()` to use this logic for grid charging
- Keep PROTECT_BATTERY mode with highest priority (existing)
- Keep PV_CHARGING mode for surplus PV (existing, lower priority than grid charge)

**Files**: `ems_controller.py`

---

### 5. Temperature-Based Prediction Fallback Rules

**Goal**: Add explicit temperature-fallback rules when no DB history is available; improve prediction labels in frontend.

**Prediction source labels**:
- Data from DB (temperature-matched historical days) → label: **"historically estimated"**
- No similar days in DB → use temperature fallback rules → label: **"fallback estimation"**

**Fallback rules** (when no similar days in DB):
- `min_temp < 0 AND max_temp < 0` → 30 kWh
- `min_temp < 0 AND max_temp < 10` → 20 kWh
- `min_temp > 0 AND max_temp < 15` → 10 kWh
- Otherwise → fallback estimation with closest available data

**Changes**:
- Replace rolling-median fallback in `consumption_model.py` with these temperature rules
- Add `prediction_source` field to result: `"historical"` | `"fallback"`
- Pass source to frontend via `/api/status`; display appropriate label in dashboard

**Files**: `consumption_model.py`, `dashboard.html`

---

### 6. Frontend: Log Panel

**Goal**: Show a scrollable log of grid charge mode changes in the dashboard.

**Log entry format**: `{on/off} | {dateTime} | battery_kwh_freetochange | battery_kwh_useable | predicted_load_kwh`

**Changes**:
- Create `event_log.py` — `EventLog(max_entries=100)` ring buffer, serialisable to JSON
- Append entry to `EventLog` whenever grid charge mode changes in `ems_controller.py`
- Expose via `/api/status` → `log`
- Add log panel to `dashboard.html` (scrollable table)

**Files**: `event_log.py` (new), `ems_controller.py`, `web_server.py`, `dashboard.html`

---

### 7. Frontend: Sensor & Config Warnings

**Goal**: Show warnings if required sensors are unavailable or config is incomplete.

**Changes**:
- In `web_server.py` `/api/status`, add `warnings: []` list
- Check for: any required sensor entity returning `None`/`unavailable`, any required config field empty or missing
- Show warnings prominently in `dashboard.html` (banner or collapsible panel)
- Required sensors: all PV/battery/grid entities, price entity, Solcast entities, weather entity

**Files**: `web_server.py`, `dashboard.html`

---

### 8. Config: Frontend-Only, Simplified config.yaml

**Goal**: All user configuration done in frontend settings page, not in HA addon config UI.

**Changes**:
- Remove all user-configurable fields from `config.yaml` schema (entities, thresholds, etc.)
- Keep only technical add-on fields in `config.yaml` (port, ingress, arch)
- Ensure `config_loader.py` reads entirely from `/data/config.json` (frontend-persisted)
- Settings page already supports this; just clean up config.yaml schema

**Files**: `config.yaml`, `config_loader.py`

---

## Configuration Fields (Complete List)

```yaml
# Entities
pv_power_entity: sensor.deye8k_pv_power
battery_soc_entity: sensor.deye8k_battery
battery_power_entity: sensor.deye8k_battery_power
grid_power_entity: sensor.deye8k_grid_power
load_power_entity: sensor.deye8k_load_power
battery_charging_power_entity: number.deye8k_battery_charging_power
battery_discharging_power_entity: number.deye8k_battery_discharging_power
grid_charge_switch_entity: switch.deye8k_battery_grid_charging
price_entity: sensor.octopus_a_10fc0646_electricity_price
weather_entity: weather.openweathermap
solcast_today_entity: sensor.solcast_pv_forecast_prognose_heute
solcast_tomorrow_entity: sensor.solcast_pv_forecast_prognose_morgen
solcast_remaining_today_entity: sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute

# Prices
cheap_rate_threshold: 0.28      # €/kWh
feed_in_tariff_eur_kwh: 0.08   # €/kWh (NEW)
fix_price: 0.30                 # €/kWh

# Inverter
max_charge_power_w: 5500
max_discharge_power_w: 5500
default_discharge_power_w: 185  # NEW – normal discharge limit

# Battery
battery_capacity_kwh: 25
battery_min_soc: 10
battery_max_soc: 95

# EMS
ems_interval_seconds: 30        # configurable, default 30s
simulation_mode: false
```

---

## MQTT Sensors to Publish (Complete List)

| Sensor | Unit | Formula |
|---|---|---|
| `miniems_mode` | – | Current EMS mode |
| `miniems_battery_kwh_freetochange` | kWh | `(max_soc - soc) / 100 * capacity` |
| `miniems_battery_kwh_useable` | kWh | `(soc - min_soc) / 100 * capacity` |
| `miniems_predicted_load_kwh` | kWh | From consumption_model |
| `miniems_real_costs_eur` | € | Actual grid import cost today |
| `miniems_cost_without_solar_eur` | € | `load * price` (no PV savings) |
| `miniems_cost_without_grid_charge_eur` | € | `grid_cost_eur - grid_charge_cost_eur` |
| `miniems_cost_fix_price_tarif_eur` | € | `load_total_kwh * fix_price` |
| `miniems_feed_in_kwh` | kWh | Daily solar export to grid |
| `miniems_feed_in_revenue_eur` | € | `feed_in_kwh * feed_in_tariff` |
| `miniems_grid_import_kwh` | kWh | Daily grid import |
| `miniems_pv_used_kwh` | kWh | Daily PV self-consumption |
| `miniems_pv_savings_eur` | € | Savings from PV |
| Weekly/Monthly/Yearly variants | € | Aggregated from DB |

---

## Critical Files to Modify / Create

### New Files

| New File | Responsibility |
|---|---|
| `sensor_validator.py` | `validate_power(entity, value, previous) → float\|None`; logs warning and returns None on spike |
| `solcast_client.py` | `SolcastClient(cfg, ws_client)` — exposes `remaining_today_kwh`, `today_kwh`, `tomorrow_kwh` |
| `battery_model.py` | `BatteryModel(cfg)` — `free_to_charge_kwh(soc)`, `useable_kwh(soc)` |
| `event_log.py` | `EventLog(max_entries=100)` — ring buffer of mode-change events, serialisable to JSON |

### Modified Files

| File | Changes |
|---|---|
| `config.yaml` | Remove user config schema; keep only technical options |
| `config_loader.py` | Add new fields: Solcast entities, feed_in_tariff, grid_charge_switch_entity, default_discharge_power_w |
| `ha_ws_client.py` | Register Solcast entities for polling |
| `ems_controller.py` | Update `_determine_mode()` to use `SolcastClient` + `BatteryModel`; append to `EventLog` on mode change |
| `inverter_controller.py` | Add `enable_grid_charge()` / `disable_grid_charge()` using switch + discharge power entity |
| `consumption_model.py` | Replace median fallback with 3-rule temperature fallback; add `prediction_source` field |
| `cost_optimizer.py` | Add grid_charge accumulators derived from `battery_power_w` (no EMS mode dependency); call `sensor_validator`; detect downtime gap on startup |
| `store.py` | Add `grid_charge_kwh`, `grid_charge_cost_eur`, `feed_in_kwh`, `feed_in_revenue_eur` columns; 6dp precision |
| `migration.py` | Add migration step for new DB columns |
| `mqtt_publisher.py` | Register new sensors |
| `web_server.py` | Add `warnings` and `log` to `/api/status`; expose `EventLog` entries |
| `dashboard.html` | Add log panel; add warnings banner; show prediction source label |

---

## Verification

1. **Grid charge logic**: Set price below threshold with battery not full, confirm switch turns on and discharge power goes to 0
2. **Battery kWh sensors**: Confirm `battery_kwh_freetochange` and `battery_kwh_useable` appear in MQTT and HA
3. **Solcast integration**: Confirm remaining-today value used in grid charge decision (visible in log panel)
4. **Cost sensors**: Verify `cost_without_grid_charge` increases only during grid-charge periods
5. **Frontend warnings**: Remove a sensor entity from config, confirm warning banner appears
6. **Log panel**: Trigger a mode change, confirm log entry appears in dashboard
7. **Prediction fallback**: With empty DB, set weather to freezing temps, confirm 30 kWh prediction with "fallback estimation" label
8. **Prediction historical**: With matching DB data, confirm "historically estimated" label
9. **Spike detection**: Simulate a sensor spike, confirm it is skipped and a warning is logged
10. **Simulation mode**: Run with `simulation_mode: true`, confirm no actual HA service calls are made
