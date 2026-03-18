# API Reference

miniEMS exposes a small HTTP API served by FastAPI on port 8080 (HA ingress).
All endpoints are internal — they are consumed by the dashboard and the HA
ingress proxy, not intended as a public API.

---

## `GET /`

Renders `dashboard.html` (Jinja2). Returns the live dashboard page.

Query parameters: none.
Response: `text/html`

---

## `GET /settings`

Renders `settings.html` (Jinja2). Returns the configuration form page.

Response: `text/html`

---

## `GET /log`

Renders `log.html` (Jinja2). Returns the unified event log page showing mode changes and price changes.

Response: `text/html`

---

## `GET /config-json`

Renders `config_json.html`. Raw JSON editor for `/data/config.json`.

Response: `text/html`

---

## `GET /options-json`

Renders `options_json.html`. Raw JSON editor for `/data/options.json`.

Response: `text/html`

---

## `GET /database`

Renders `database.html`. Sortable browser for the `daily_stats` SQLite table.

Response: `text/html`

---

## `POST /settings`

Saves configuration submitted from the settings form. Validates and type-coerces
all fields, writes them to `/data/config.json`, then restarts the add-on via the
Supervisor API.

Request: `application/x-www-form-urlencoded`

Response: `303 See Other` → redirects to `/settings` after restart, or returns
`422 Unprocessable Entity` on validation failure.

---

## `GET /api/status`

Returns the current EMS state as JSON. Consumed by the dashboard's JavaScript
auto-refresh (every 5 s) and the log page.

Response: `application/json`

```json
{
  "mode": "Idle",
  "mode_label": "Idle",
  "soc": 72,
  "pv_power_w": 1200,
  "load_power_w": 800,
  "grid_power_w": -400,
  "battery_power_w": 0,
  "price_eur_kwh": 0.31,
  "is_cheap_rate": false,

  "today_grid_cost_eur": 1.23,
  "today_pv_savings_eur": 0.87,
  "today_pv_used_kwh": 3.4,
  "today_grid_import_kwh": 4.1,
  "today_load_total_kwh": 7.5,
  "today_cost_without_grid_charge": 0.95,
  "today_cost_fix_price_tarif": 2.25,
  "today_feed_in_kwh": 0.6,
  "today_feed_in_revenue_eur": 0.05,
  "today_grid_charge_kwh": 1.2,

  "week_grid_cost_eur": 8.40,
  "week_pv_saved_eur": 5.10,
  "month_grid_cost_eur": 31.20,
  "month_pv_savings_eur": 18.40,
  "year_grid_cost_eur": 180.00,
  "year_pv_savings_eur": 95.00,

  "battery_kwh_freetochange": 4.5,
  "battery_kwh_useable": 14.0,

  "predicted_load_kwh": 10.5,
  "predicted_pv_kwh": 8.3,
  "prediction_source": "historical",

  "warnings": [],
  "log": [
    {
      "timestamp": "2026-03-15T06:15:00",
      "state": "on",
      "entry_type": "mode_change",
      "battery_kwh_freetochange": 12.5,
      "battery_kwh_useable": 6.0,
      "predicted_load_kwh": 10.5,
      "price_eur_kwh": null
    },
    {
      "timestamp": "2026-03-15T06:00:00",
      "state": "price_change",
      "entry_type": "price_change",
      "battery_kwh_freetochange": 13.1,
      "battery_kwh_useable": 5.8,
      "predicted_load_kwh": 10.5,
      "price_eur_kwh": 0.0850
    }
  ],

  "simulation_mode": false,
  "last_update": "2026-03-14T07:45:00Z"
}
```

### `warnings` array

Each warning is a string key looked up in the translation files, e.g.:

```json
["warn_missing_pv_entity", "warn_missing_price_entity"]
```

The dashboard renders these with their translated text in the warnings banner.

### `prediction_source`

| Value | Meaning |
|---|---|
| `"historical"` | Median of temperature-matched historical days |
| `"fallback"` | Temperature rule-based estimate |

---

## `GET /api/config`

Returns the current configuration from `/data/config.json` as JSON (internal keys stripped).

Response: `application/json`

---

## `POST /api/config`

Saves updated config keys to `/data/config.json` and restarts the add-on. Values are type-coerced (bool/int/float) based on the known field list in `web_server.py`. Unknown or internal (`_`-prefixed) keys are ignored.

Request: `application/json` — partial or full config object.

Response: `{"status": "restarting"}` or `{"error": "..."}`.

---

## `GET /api/rawfile/{name}`

Returns the raw contents of a config file as JSON. `name` is `config` or `options`.

| name | File |
|---|---|
| `config` | `/data/config.json` |
| `options` | `/data/options.json` |

Response: `application/json` — the file's parsed JSON content, or `{}` if the file does not exist.

---

## `POST /api/rawfile/{name}`

Writes the request body verbatim as JSON to the named file and restarts the add-on. No type coercion is applied — the exact JSON is written.

Request: `application/json`

Response: `{"status": "restarting"}` or `{"error": "..."}`.

---

## `GET /api/database`

Returns all rows from the `daily_stats` SQLite table, newest first.

Response: `application/json`

```json
{
  "rows": [
    {
      "date": "2026-03-15",
      "grid_import_kwh": 4.123,
      "grid_cost_eur": 1.234,
      "pv_used_kwh": 8.456,
      "pv_savings_eur": 2.537,
      "load_total_kwh": 12.579,
      "load_cost_eur": 3.774,
      "avg_price_eur_kwh": 0.3001,
      "avg_outdoor_temp_c": 8.4,
      "ticks": 2880,
      "peak_pv_w": 5863.0,
      "grid_charge_kwh": 1.2,
      "grid_charge_cost_eur": 0.102,
      "feed_in_kwh": 0.5,
      "feed_in_revenue_eur": 0.04,
      "last_flush_ts": "2026-03-15T23:59:58"
    }
  ]
}
```

---

## `GET /static/<file>`

Serves static assets (`style.css`, etc.) from the `static/` directory.

---

## Supervisor API Calls (outbound)

miniEMS makes the following outbound calls to the HA Supervisor/Core:

| Endpoint | Direction | Purpose |
|---|---|---|
| `GET http://hassio/homeassistant/api/states/<entity>` | Read | Poll HA entity states (every 15 s) |
| `POST http://hassio/homeassistant/api/states/sensor.miniems_*` | Write | Publish sensor states (REST fallback) |
| `POST http://hassio/homeassistant/api/services/<domain>/<service>` | Write | Inverter control commands |
| `POST http://hassio/homeassistant/api/services/weather/get_forecasts` | Read | Weather forecast (30 min cache) |
| `GET http://supervisor/core/api/config` | Read | HA language + latitude (on startup) |
| `POST http://supervisor/addons/self/restart` | Action | Triggered by Settings save |
