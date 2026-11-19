---
revision_date: 2026-08-19
---

# Architecture

## Component Overview

```mermaid
flowchart TB
    WS["HAStateClient<br/>(REST poll every 15s)"] --> EC
    WEATHER["WeatherClient<br/>weather.get_forecasts, 30 min cache"] --> EC

    subgraph EC["EMSController – EMS loop, every 30s"]
        direction LR
        CO["CostOptimizer<br/>+ SensorValidator"]
        CM["ConsumptionModel"]
        BM["BatteryModel"]
        SOL["SolcastClient"]
        EL["EventLog"]
    end

    EC --> INV["InverterController<br/>apply_mode() + confirm/retry"]
    EC --> STORE[("EnergyStore<br/>SQLite: daily_stats, event_log")]
    EC --> SS["status_store {}"]

    SS --> WEB["FastAPI / Uvicorn<br/>Ingress dashboard (port 8080)"]
    SS -->|"GET /api/status"| COORD

    INV -->|"service calls"| HA["HA Core API<br/>http://hassio/homeassistant/api"]
    WEB -.->|"/config-json /options-json<br/>/database /log"| STORE

    subgraph Integration["Custom Integration (custom_components/miniems)"]
        direction LR
        COORD["MiniEMSCoordinator<br/>polls every 30s"] --> SENSORS["sensor.miniems_*<br/>entities in HA"]
    end
```

`InverterController` and `HAStateClient` both talk directly to the `HA Core API`; the `sensor.miniems_*` entities are **not** pushed from the add-on — they are pulled by the Custom Integration via HTTP from `/api/status` (see "Sensor Publishing" below).

## Asyncio Task Graph

Three long-running tasks run concurrently:

```
asyncio.gather(
  ws_client.run()      # polls HA states every 15 s
  _ems_task()          # waits for ready → runs EMS loop every 30 s
  uvi_server.serve()   # FastAPI / Uvicorn HTTP server on port 8080
)
```

`_ems_task` waits on `ws_client.wait_ready()` (an `asyncio.Event`) before
starting, preventing the EMS from running on stale/empty state.

## Sub-system Wiring (per tick)

```
EMSController.update()
  │
  ├─ ws.get_state_value(...)        # read raw sensors (PV/load/grid/SoC/price/…)
  ├─ BatteryModel.free_to_charge()  # kWh headroom calculation
  ├─ BatteryModel.useable()         # kWh discharge capacity
  ├─ ConsumptionModel.predict()     # predicted_load_kwh + source label (uses WeatherClient)
  ├─ _determine_mode()              # _decide() + _commit() → EMSMode (see "Decisions & Control")
  ├─ EventLog.append()              # on mode change OR price change
  ├─ InverterController.apply_mode()# send commands + confirm against real state (see below)
  ├─ CostOptimizer.record_tick()    # energy/cost accumulators
  │    └─ SensorValidator.validate()#   rejects power spikes ONLY for cost/energy accounting –
  │                                 #   the mode logic above reads the raw values unfiltered
  └─ return status_store {}         # fetched by web_server (/api/status) and the Custom Integration
```

!!! note "SensorValidator is not on the decision path"
    `SensorValidator.validate()` is called from `CostOptimizer.record_tick()`, not directly from `EMSController.update()`. A rejected power spike only affects energy/cost accounting — the mode decision (`_decide`) always works on the raw, unfiltered values from `HAStateClient` and relies on its own staleness checks instead (see [Calculations](calculations.md)).

## Authentication Flow

```
SUPERVISOR_TOKEN  ──▶  http://hassio/homeassistant/api
       │ 401?
       ▼
long_lived_token  ──▶  http://hassio/homeassistant/api
       │ 401?
       ▼
    Log error, retry in 10 s
```

Both `HAStateClient` (reads) and `InverterController` (writes) implement
this fallback independently so each can switch tokens at runtime.

`SUPERVISOR_TOKEN` is also used by:

- `WeatherClient` — to call `weather.get_forecasts` and fetch HA latitude
- `web_server.py` — to query `http://supervisor/core/api/config` for the HA
  language (de/en auto-detection)
- `integration_installer.py` — to install/update the Custom Integration
  files under `custom_components/miniems` in HA's config directory. Inside the
  add-on container that is mounted at `/homeassistant` (map
  `homeassistant_config`); `/config` there is the add-on's own space, which HA
  never reads. The installer only writes if a `configuration.yaml` is present
  (since v3.6.1).

## Sensor Publishing: Custom Integration (Pull)

`sensor.miniems_*` entities are **not** pushed from the add-on to HA — they
come from a bundled HA Custom Integration (`custom_components/miniems`,
installed by `integration_installer.py`):

| Component | Role |
|---|---|
| `MiniEMSCoordinator` (`DataUpdateCoordinator`) | Polls `GET http://homeassistant:8080/api/status` (default every 30 s) |
| `MiniEMSSensor` (`SensorEntity`) | Reads the desired fields from the coordinator's result (the `status_store` JSON) |

There is currently **no** MQTT discovery or REST push mechanism inside the add-on itself — sensors are created exclusively through this pull path.

## Sensor Validation (SensorValidator)

Power readings are validated on every tick for cost/energy accounting (not for the mode logic, see above) to reject implausible spikes:

```
reject if: |current − last_accepted| > 500 W  AND  |Δ| / last_accepted > 50%
```

Rejected readings return `None`; `CostOptimizer` substitutes **0 W** for that tick, not the last accepted value. Each entity is tracked independently.

Since v2.0.6, a rejection alone no longer updates the reference (a single outlier still should not corrupt it immediately) — but two **consecutive** rejected readings that agree with each other count as a real, permanent shift and get adopted. Without that, the reference stayed frozen forever after a real shift and rejected every future real reading until the add-on restarted – observed live for 2.5+ hours. Details: [Calculations](calculations.md).

## Downtime Gap Detection

On startup, `CostOptimizer` reads `last_flush_ts` from the SQLite `daily_stats`
table (`EnergyStore`, `/data/miniems.db`). If the gap between `last_flush_ts`
and `datetime.now(timezone.utc)` exceeds two update intervals, a data-gap
warning is raised and surfaced in the dashboard warnings banner.

## Decisions & Control

The full formulas and thresholds live in [Calculations](calculations.md); the two diagrams here show the control flow at a glance.

### Mode Decision (`EMSController._decide`)

```mermaid
flowchart TD
    A{"SoC available<br/>and fresh?"} -- no --> I0["IDLE (urgent – inverter's own logic)"]
    A -- yes --> B{"SoC < battery_min_soc<br/>(or hysteresis)?"}
    B -- yes --> PROT["PROTECT_BATTERY"]
    B -- no --> C{"SoC ≥ battery_max_soc?"}
    C -- yes --> I1["IDLE – battery full"]
    C -- no --> M{"Maintenance full charge due<br/>and price cheap? (A11)"}
    M -- yes --> G1["GRID_CHARGING"]
    M -- no --> N{"Price ≤ 0? (A5)"}
    N -- yes --> G2["GRID_CHARGING<br/>(leave room for PV)"]
    N -- no --> DP{"Day plan in control?<br/>(battery_plan_enabled)"}
    DP -- yes --> DPS["Slot 0 of the plan:<br/>CHARGE → GRID_CHARGING<br/>HOLD → HOLD_FOR_PEAK / PV_CHARGING<br/>EXPORT → EXPORT_SURPLUS<br/>AUTO → IDLE / PV_CHARGING"]
    DP -- no --> BRG{"Bridge plan<br/>needs a grid charge?"}
    BRG -- yes --> G3["GRID_CHARGING"]
    BRG -- no --> PV{"PV surplus ><br/>pv_surplus_threshold_w?"}
    PV -- yes --> H{"Export hold?<br/>(_should_hold_pv_charge)"}
    H -- yes --> EXP["EXPORT_SURPLUS"]
    H -- no --> PVC["PV_CHARGING"]
    PV -- no --> GC{"cheap and worth it?<br/>(_should_grid_charge)"}
    GC -- yes --> G4["GRID_CHARGING"]
    GC -- no --> HO{"Discharge hold A4?"}
    HO -- yes --> HOLD["HOLD_FOR_PEAK"]
    HO -- no --> I2["IDLE – no action"]

    DPS --> OVR
    G1 & G2 & G3 & G4 & EXP & PVC & HOLD & I2 & I1 --> OVR{"Override C2?<br/>(never above hard limits)"}
    OVR --> COM["_commit: dwell time mode_dwell_sec<br/>(unless urgent)"]
```

Inverter settings per mode (`InverterController.apply_mode`):

| Mode | Grid-charge switch | Charge current | Discharge current |
|---|---|---|---|
| `GRID_CHARGING` | on | stretched power (V1) or max, grid-charge current ≤ `battery_max_grid_charge_current_a` | 0 |
| `PV_CHARGING`, `IDLE` | off | max | max |
| `EXPORT_SURPLUS` | off | `export_hold_charge_current_a` | max |
| `PROTECT_BATTERY`, `HOLD_FOR_PEAK` | off | max (PV may charge) | 0 |

How the prices behind these rules are formed: see the German page
[Preise und Entscheidungen](../../technical/preise-und-entscheidungen.md).

Every branch is **fail closed**: a missing or stale required sensor value always resolves to the safer outcome (no grid charging, no holding back PV charging). `_commit()` additionally debounces a newly proposed mode via `mode_dwell_sec`, unless the decision is `urgent` (SoC protection, missing SoC sensor, an export hold ended by a safety guard).

### Inverter Write Confirmation (`InverterController`, since v2.0.1)

Per channel (charge current, discharge current, grid-charge switch), independently — a service call HA accepted only counts as confirmed once the real state actually matches:

```mermaid
stateDiagram-v2
    [*] --> Unconfirmed: target changes
    Unconfirmed --> Confirmed: real HA state == target
    Unconfirmed --> Unconfirmed: ≥30s since last send\n→ resend service call
    Confirmed --> Unconfirmed: new target
    Confirmed --> [*]
```

`write_unconfirmed` (0–3) is a live count of how many channels are currently unconfirmed; `write_errors` separately counts outright HTTP/connection failures. Both surface as warnings in the dashboard banner.

## Internationalisation (i18n)

On each page request, `web_server.py` queries `http://supervisor/core/api/config`
to determine the HA language (`language` field). The corresponding YAML file
(`translations/de.yaml` or `translations/en.yaml`) is loaded and injected into
both Jinja2 templates and the JavaScript `const T` object so that dynamically
rendered cards are also translated.

Fallback order: HA Supervisor API → `Accept-Language` header → English.

## Dashboard & API Routes (`web_server.py`)

| Route | Purpose |
|---|---|
| `/` | Dashboard (HTML) |
| `/settings` | Settings page (HTML) |
| `/log` | Event log (HTML) |
| `/database` | Database viewer (HTML) |
| `/config-json`, `/options-json` | Raw file editors (HTML) |
| `/api/status` | **Live status values as JSON — polled by the Custom Integration** |
| `/api/database` | Database export as JSON |
| `/api/config` (GET/POST) | Read/write configuration |
| `/api/rawfile/{name}` (GET/POST) | Read/write raw file editors (config.json/options.json) |
