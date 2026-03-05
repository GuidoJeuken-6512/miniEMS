# Architecture

## Component Overview

```
┌─────────────────────────────────────────────────────┐
│                   miniEMS Add-on                    │
│                                                     │
│  ┌─────────────┐    ┌──────────────┐               │
│  │HAWebSocket  │    │  EMS Loop    │               │
│  │Client       │───▶│  (30 s tick) │               │
│  │(REST poll)  │    └──────┬───────┘               │
│  └─────────────┘           │                       │
│                            ▼                       │
│                   ┌────────────────┐               │
│                   │ EMSController  │               │
│                   │ + CostOptimizer│               │
│                   └───────┬────────┘               │
│                           │ status_store {}        │
│              ┌────────────┴────────────┐           │
│              ▼                         ▼           │
│  ┌─────────────────┐      ┌──────────────────────┐ │
│  │HASensorPublisher│      │  FastAPI / Uvicorn   │ │
│  │(REST POST)      │      │  Ingress Dashboard   │ │
│  └────────┬────────┘      └──────────────────────┘ │
└───────────┼─────────────────────────────────────────┘
            │
            ▼
   HA Core API  (http://hassio/homeassistant/api)
   sensor.miniems_* entities
```

## Modules

| File | Role |
|---|---|
| `main.py` | Entry point; wires components, manages asyncio tasks |
| `config_loader.py` | Loads & merges `options.json` + `config.json`; runs migration |
| `migration.py` | Schema version migrations for `config.json` |
| `ha_ws_client.py` | Polls HA states via REST; handles token fallback |
| `ems_controller.py` | Determines operating mode from live sensor values |
| `cost_optimizer.py` | Accumulates daily/weekly energy cost & savings |
| `ha_sensor_publisher.py` | Pushes 13 computed sensor states to HA Core |
| `web_server.py` | FastAPI app; serves ingress dashboard + `/api/status` |
| `templates/dashboard.html` | Dashboard HTML (Jinja2 template) |
| `static/style.css` | Dashboard CSS |

## Asyncio Task Graph

Three long-running tasks run concurrently:

```
asyncio.gather(
  ws_client.run()      # polls HA states every 15 s
  _ems_task()          # waits for ready → runs EMS loop every 30 s
  uvi_server.serve()   # FastAPI / Uvicorn HTTP server on port 8080
)
```

The `_ems_task` waits on `ws_client.wait_ready()` (an `asyncio.Event`) before
starting, preventing the EMS from running on stale/empty state.

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
