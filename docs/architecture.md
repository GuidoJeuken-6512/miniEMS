# Architecture

## Component Overview

```
┌───────────────────────────────────────────────────────────┐
│                      miniEMS Add-on                       │
│                                                           │
│  ┌─────────────┐    ┌──────────────────────────────────┐ │
│  │HAWebSocket  │    │          EMS Loop (30 s tick)    │ │
│  │Client       │───▶│  EMSController                   │ │
│  │(REST poll)  │    │  + CostOptimizer                 │ │
│  └─────────────┘    │  + ConsumptionModel              │ │
│                     └──────────────┬─────────────────── ┘ │
│  ┌──────────────┐                  │                      │
│  │WeatherClient │──────────────────┘                      │
│  │(HA forecast  │   weather.get_forecasts (daily, 30 m)   │
│  │ action API)  │                                         │
│  └──────────────┘                                         │
│                        status_store {}                     │
│              ┌─────────────────────────────┐              │
│              ▼                             ▼              │
│  ┌─────────────────┐      ┌───────────────────────────┐  │
│  │HASensorPublisher│      │  FastAPI / Uvicorn        │  │
│  │(REST POST)      │      │  Ingress Dashboard        │  │
│  └────────┬────────┘      │  (de/en i18n via YAML)    │  │
└───────────┼───────────────┴───────────────────────────┘  │
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
| `migration.py` | Schema version migrations for `config.json` (v0 → v5) |
| `const.py` | Shared constants: `EMSMode` enum, schema version, API URLs |
| `ha_ws_client.py` | Polls HA states via REST; handles token fallback |
| `ems_controller.py` | Determines operating mode from live sensor values |
| `cost_optimizer.py` | Accumulates daily/weekly energy cost & savings |
| `consumption_model.py` | Predicts load & PV yield; recommends grid-charge |
| `weather_client.py` | Calls `weather.get_forecasts` HA action; 30-min cache |
| `store.py` | SQLite persistence for daily energy history |
| `ha_sensor_publisher.py` | Pushes computed sensor states to HA Core |
| `inverter_controller.py` | Writes work-mode & power-limit entities to the inverter |
| `web_server.py` | FastAPI app; serves ingress dashboard + `/api/status`; i18n |
| `templates/dashboard.html` | Dashboard HTML (Jinja2 + JS; translations via `T` object) |
| `templates/settings.html` | Settings page HTML |
| `static/style.css` | Dashboard CSS |
| `translations/de.yaml` | German UI strings |
| `translations/en.yaml` | English UI strings |

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

The `SUPERVISOR_TOKEN` is also used by:

- `WeatherClient` — to call `weather.get_forecasts` and fetch HA instance latitude
- `web_server.py` — to query `http://supervisor/core/api/config` for the HA
  language setting (de/en auto-detection)

## Prognose-Berechnung (ConsumptionModel)

Die Prognosen werden in `consumption_model.py` berechnet und einmal pro EMS-Tick
(30 s) aktualisiert.  Das Ergebnis fließt in die Grid-Charge-Empfehlung ein und
wird im Dashboard unter **Prognose & Vorhersage** angezeigt.

### Prognostizierter Verbrauch (`predicted_load_kwh`)

Der erwartete Tagesverbrauch wird temperaturbasiert oder als rollender Median
aus der SQLite-Historie ermittelt:

```
Wenn Wetter-Entity konfiguriert UND Forecast verfügbar:
  target_temp  = Tageshöchstwert von morgen (aus HA-Forecast)
  ähnliche Tage = Einträge der letzten 60 Tage mit |temp − target| ≤ 4 °C
  Falls ≥ 3 ähnliche Tage gefunden:
    → Median(load_total_kwh) dieser Tage  [Confidence: "high"]
  Sonst:
    → 30-Tage-Rolling-Median aller Tage mit Verbrauch > 0 [Confidence: "low"]
Sonst (kein Wetter):
  → 30-Tage-Rolling-Median                                [Confidence: "low"]
Keine Historie:
  → 0.0 kWh                                               [Confidence: "none"]
```

### Prognostizierter PV-Ertrag (`predicted_pv_kwh`)

Der erwartete PV-Ertrag basiert auf dem historischen Peak-Leistungswert der
letzten 14 Tage, skaliert mit Bewölkung und Tageslänge:

```
peaks        = peak_pv_w aller Tage der letzten 14 Tage (nur Werte > 100 W)
p75          = 75. Perzentil von peaks  (konservative Schätzung)

Mit Forecast:
  clear_frac  = Ø(1 − cloud_coverage/100) über alle Forecast-Slots
  pv_factor   = clear_frac × min(1,0 ; daylight_h / 12,0)
  daylight_h  = astronomische Tageslänge (Breitengrad der HA-Instanz, aktueller Monat)

Ohne Forecast:
  pv_factor   = 0,5   (neutrale Annahme)
  daylight_h  = geschätzte Tageslänge für Breite 51 °N

Ertrag = (p75 / 1000 kW) × pv_factor × daylight_h
```

### Grid-Charge-Empfehlung (`should_grid_charge`)

```
usable_kwh = Batteriekapazität × max(0 ; SoC − min_SoC) / 100

should_grid_charge = (predicted_load > 0)
                   AND (usable_kwh + predicted_pv) < predicted_load
```

Wenn Batterie + erwarteter PV-Ertrag den erwarteten Verbrauch nicht decken
können, empfiehlt miniEMS das Nachladen aus dem Netz.

### Wetterdaten-Cache

`WeatherClient` ruft `weather.get_forecasts` (HA-Action-API) mit `type: daily`
ab und cached das Ergebnis für **30 Minuten** — passend zur typischen
Aktualisierungsrate der HA OpenWeatherMap-Integration.  Der Breitengrad der
HA-Instanz wird einmalig von `http://supervisor/core/api/config` gelesen und
für die Tageslängenberechnung gecacht.

## Internationalisation (i18n)

The dashboard and settings page are fully translated.  On each page request,
`web_server.py` queries `http://supervisor/core/api/config` to determine the
HA language (`language` field).  The corresponding YAML file
(`translations/de.yaml` or `translations/en.yaml`) is loaded and injected
into both Jinja2 templates and the JavaScript `const T` object so that
dynamically rendered cards are also translated.

Fallback order: HA Supervisor API → `Accept-Language` header → English.
