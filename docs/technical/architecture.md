# Architecture

## Component Overview

```
┌───────────────────────────────────────────────────────────────────┐
│                         miniEMS Add-on                            │
│                                                                   │
│  ┌─────────────┐    ┌───────────────────────────────────────────┐ │
│  │HAWebSocket  │    │         EMS Loop (30 s tick)              │ │
│  │Client       │───▶│  EMSController                            │ │
│  │(REST poll)  │    │  + CostOptimizer  + SensorValidator       │ │
│  └─────────────┘    │  + ConsumptionModel + BatteryModel        │ │
│                     │  + SolcastClient  + EventLog              │ │
│  ┌──────────────┐   └──────────────┬────────────────────────────┘ │
│  │WeatherClient │──────────────────┘                              │
│  │(HA forecast  │   weather.get_forecasts (daily, 30 m cache)     │
│  │ action API)  │                                                 │
│  └──────────────┘                                                 │
│                          status_store {}                          │
│              ┌────────────────────────────────┐                  │
│              ▼                                ▼                  │
│  ┌───────────────────┐       ┌──────────────────────────────┐   │
│  │MQTTPublisher      │       │  FastAPI / Uvicorn           │   │
│  │(Discovery + data) │       │  Ingress Dashboard           │   │
│  │                   │       │  /  /settings  /log          │   │
│  │HASensorPublisher  │       │  /config-json /options-json  │   │
│  │(REST fallback)    │       │  /database                   │   │
│  │                   │       │  (de/en i18n via YAML)       │   │
│  │(REST fallback)    │       └──────────────────────────────┘   │
│  └────────┬──────────┘                                          │
└───────────┼─────────────────────────────────────────────────────┘
            │
            ▼
   HA Core API  (http://hassio/homeassistant/api)
   sensor.miniems_* entities
```

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
  ├─ SensorValidator.validate()     # reject power spikes
  ├─ BatteryModel.free_to_charge()  # kWh headroom calculation
  ├─ BatteryModel.useable()         # kWh discharge capacity
  ├─ SolcastClient.remaining_today  # Solcast remaining kWh (or None)
  ├─ ConsumptionModel.predict()     # predicted_load_kwh + source label
  ├─ _determine_mode()              # EMS mode logic
  ├─ InverterController.apply_mode()# send commands (or log [SIM])
  ├─ CostOptimizer.record_tick()    # energy/cost accumulators
  ├─ EventLog.append()              # on mode change OR price change
  └─ return status_store {}         # fed to web_server + publishers
```

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

Both `HAWebSocketClient` (reads) and `HASensorPublisher` (writes) implement
this fallback independently so each can switch tokens at runtime.

`SUPERVISOR_TOKEN` is also used by:

- `WeatherClient` — to call `weather.get_forecasts` and fetch HA latitude
- `web_server.py` — to query `http://supervisor/core/api/config` for the HA
  language (de/en auto-detection)

## Sensor Publishing: MQTT vs REST

miniEMS publishes sensors in two ways, with automatic fallback:

| Method | When active | Sensors |
|---|---|---|
| **MQTT Discovery** | When an MQTT broker is reachable | Full 30+ sensor set under the *miniEMS* device |
| **HA REST API** | Always (fallback when MQTT unavailable) | Same sensors via `POST /api/states/sensor.miniems_*` |

MQTT sensors support long-term statistics in HA (`state_class: total_increasing` / `measurement`).

## Sensor Validation (SensorValidator)

Power readings are validated on every tick to reject implausible spikes:

```
reject if: |current − last_accepted| > 500 W  AND  |Δ| / last_accepted > 50%
```

Rejected readings return `None`; the EMS skips the tick for that sensor.
Each entity is tracked independently.

## Downtime Gap Detection

On startup, `CostOptimizer` reads `last_flush_ts` from the SQLite `daily_stats`
table. If the gap between `last_flush_ts` and `datetime.utcnow()` exceeds two
update intervals, a data-gap warning is raised and surfaced in the dashboard
warnings banner.

## Internationalisation (i18n)

On each page request, `web_server.py` queries `http://supervisor/core/api/config`
to determine the HA language (`language` field). The corresponding YAML file
(`translations/de.yaml` or `translations/en.yaml`) is loaded and injected into
both Jinja2 templates and the JavaScript `const T` object so that dynamically
rendered cards are also translated.

Fallback order: HA Supervisor API → `Accept-Language` header → English.
