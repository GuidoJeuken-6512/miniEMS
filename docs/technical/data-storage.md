# Data Storage

## File Overview

| Path | Managed by | Purpose |
|---|---|---|
| `/data/config.json` | miniEMS | Persistent config with schema version |
| `/data/miniems.db` | miniEMS (`store.py`) | SQLite daily energy history |
| HA Core entity registry | Home Assistant | `sensor.miniems_*` long-term state history |
| In-memory `status_store` | miniEMS runtime | Live values for dashboard and publishers |
| In-memory `CostOptimizer` | miniEMS runtime | Intra-day accumulators (flushed at midnight) |
| In-memory `EventLog` | miniEMS runtime | Ring buffer of 100 events (backed by SQLite `event_log` table) |

!!! note "options.json and config.json"
    `options.json` is still read on startup and can override `config.json` for
    values that differ from the dataclass defaults. However, `config.json` is the
    durable source of truth — on every startup the merged result is written back
    to it, so it survives any Supervisor reset of `options.json`.
    Both files can be inspected and edited via the **config.json** and
    **options.json** tabs in the miniEMS UI.

---

## `/data/config.json`

Created and maintained by `config_loader.py`. Survives add-on updates,
Supervisor reloads, and restarts.

```json
{
  "_version": 8,
  "pv_power_entity": "sensor.deye_pv_total_power",
  "battery_soc_entity": "sensor.deye_battery_soc",
  "battery_capacity_kwh": 25.0,
  "battery_min_soc": 10,
  "battery_max_soc": 95,
  "cheap_rate_threshold_eur": 0.28,
  "medium_rate_threshold_eur": 0.20,
  "feed_in_tariff_eur_kwh": 0.08,
  "fix_price": 0.30,
  "battery_control_enabled": false,
  "battery_control_simulation": true,
  "grid_charge_switch_entity": "switch.deye8k_battery_grid_charging",
  "battery_discharging_power_entity": "number.deye8k_battery_discharging_power",
  "battery_max_charge_power_w": 5500,
  "battery_max_discharge_power_w": 5500,
  "default_discharge_power_w": 185,
  "solcast_remaining_today_entity": "sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute",
  "solcast_today_entity": "sensor.solcast_pv_forecast_prognose_heute",
  "solcast_tomorrow_entity": "sensor.solcast_pv_forecast_prognose_morgen",
  "weather_entity": "weather.openweathermap",
  "update_interval_sec": 30,
  "event_log_retention_days": 30,
  "long_lived_token": ""
}
```

### Schema Versioning

`_version` is written by `migration.py`. The current version is **8**.

When the schema changes:

1. Increment `CONFIG_SCHEMA_VERSION` in `const.py`
2. Add a migration function `_vN_to_vN1(data: dict) -> dict`
3. Register it in `migration.py`

Migration chain: v0 → v1 → v2 → v3 → v4 → v5 → v6 → v7 → v8

---

## `/data/miniems.db`

SQLite database managed by `store.py`. Contains two tables:

### `daily_stats` Table

```sql
CREATE TABLE daily_stats (
    date               TEXT PRIMARY KEY,  -- ISO date 'YYYY-MM-DD'
    grid_import_kwh    REAL DEFAULT 0,
    grid_cost_eur      REAL DEFAULT 0,
    pv_used_kwh        REAL DEFAULT 0,
    pv_savings_eur     REAL DEFAULT 0,
    load_total_kwh     REAL DEFAULT 0,
    load_cost_eur      REAL DEFAULT 0,
    avg_price_eur_kwh  REAL DEFAULT 0,
    avg_outdoor_temp_c REAL,
    ticks              INTEGER DEFAULT 0,
    -- added via ALTER TABLE on upgrade:
    peak_pv_w          REAL DEFAULT 0,
    grid_charge_kwh    REAL DEFAULT 0,
    grid_charge_cost_eur REAL DEFAULT 0,
    feed_in_kwh        REAL DEFAULT 0,
    feed_in_revenue_eur REAL DEFAULT 0,
    last_flush_ts      TEXT,              -- UTC ISO timestamp of last write
    kwh_high_rate      REAL DEFAULT 0,   -- load at high price tier
    kwh_medium_rate    REAL DEFAULT 0,   -- load at medium price tier
    kwh_low_rate       REAL DEFAULT 0    -- load at low price tier
);
```

New columns are added automatically via `ALTER TABLE` on first run after upgrade.

### Flush Behaviour

`CostOptimizer` accumulates values in-memory throughout the day. At midnight
(detected by `date.today()` changing), it calls `store.flush_day()` to write
the completed day's row to SQLite. The `last_flush_ts` column is updated on
every flush and used for downtime gap detection on startup.

### Aggregation

`store._aggregate()` computes rolling/calendar aggregates directly in SQL:

```sql
-- Month (calendar month)
SELECT SUM(grid_cost_eur), SUM(kwh_high_rate), SUM(kwh_medium_rate), SUM(kwh_low_rate)
FROM daily_stats
WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
```

---

### `event_log` Table

Stores every mode-change and price-change event so the log survives restarts and updates.

```sql
CREATE TABLE event_log (
    id                       INTEGER PRIMARY KEY AUTOINCREMENT,
    timestamp                TEXT NOT NULL,   -- ISO 8601 local time
    entry_type               TEXT NOT NULL,   -- "mode_change" | "price_change"
    state                    TEXT NOT NULL,   -- "on" | "off" | "price_change"
    battery_kwh_freetochange REAL DEFAULT 0,
    battery_kwh_useable      REAL DEFAULT 0,
    predicted_load_kwh       REAL,
    price_eur_kwh            REAL             -- NULL for mode_change entries
);
```

#### Write path

`EventLog.append()` is called by `EMSController` on every mode change or price change. It:

1. Adds the entry to the in-memory `deque(maxlen=100)`
2. Immediately inserts a row into `event_log` via `store.append_event()`

#### Startup restore

`EventLog.restore_from_db()` is called in `main.py` after `store.open()`. It reads the last 100 rows (newest first), reverses them, and fills the in-memory buffer — so `to_list()` is immediately populated.

#### Daily cleanup

Once per day (on the first EMS tick after midnight) `EMSController` calls `EventLog.cleanup_old_entries(retention_days)`, which executes:

```sql
DELETE FROM event_log
WHERE timestamp < datetime('now', '-N days')
```

The retention window is set by `event_log_retention_days` in config (default: 30 days).

---

## HA Entity States (`sensor.miniems_*`)

States are written via two paths:

### MQTT Discovery (preferred)

miniEMS publishes config topics to `homeassistant/sensor/miniems_*/config` on
startup, then data to `homeassistant/sensor/miniems_*/state` on each tick.
Sensors appear under the **miniEMS** device in HA.

### REST API (fallback)

```
POST http://hassio/homeassistant/api/states/sensor.miniems_<name>
{
  "state": "<value>",
  "attributes": { "unit_of_measurement": "...", "state_class": "..." }
}
```

HA stores these states in its own database. The add-on does not maintain a
local copy beyond `status_store`.

---

## In-Memory Runtime State

### `status_store` (shared dict)

Updated by `ems_loop` after every EMS tick. Read by:

- `web_server` → `/api/status` → dashboard auto-refresh
- `mqtt_publisher` → MQTT state topics
- `ha_sensor_publisher` → REST POST to HA Core

### `CostOptimizer` Accumulators

Per-day `defaultdict(float)` keyed by `date.today()`:

```python
_grid_import_kwh     : {date: float}
_pv_used_kwh         : {date: float}
_grid_cost_eur       : {date: float}
_pv_saved_eur        : {date: float}
_load_total_kwh      : {date: float}
_grid_charge_kwh     : {date: float}
_grid_charge_cost_eur: {date: float}
_feed_in_kwh         : {date: float}
_feed_in_revenue_eur : {date: float}
_kwh_high_rate       : {date: float}   # price tier accumulators
_kwh_medium_rate     : {date: float}
_kwh_low_rate        : {date: float}
```

In-memory accumulators reset on add-on restart. Long-term persistence is
provided by the `total_increasing` HA sensors which HA records in its own DB,
and by the `daily_stats` restore on startup.

### `EventLog` Ring Buffer + SQLite

`event_log.py` maintains a `deque(maxlen=100)` **backed by the `event_log` SQLite table**. Two event types share the same buffer:

```python
@dataclass
class LogEntry:
    timestamp: str              # ISO 8601 string
    state: str                  # "on" | "off" | "price_change"
    battery_kwh_freetochange: float
    battery_kwh_useable: float
    predicted_load_kwh: float | None
    entry_type: str             # "mode_change" | "price_change"
    price_eur_kwh: float | None # set for price_change entries only
```

| `entry_type` | Triggered when | `state` value |
|---|---|---|
| `mode_change` | EMS switches operating mode | `"on"` (grid charging started) or `"off"` |
| `price_change` | Electricity price sensor reports a new value | `"price_change"` |

Price-change entries are only recorded when the price differs from the previously observed value. The very first reading after startup is not logged.

The log is served via `/api/status` → `log` array and rendered in the `/log` page. The buffer holds the last 100 events across both types combined. Events **survive restarts** — they are persisted to SQLite and restored on startup. See the [`event_log` table schema](#event_log-table) above.
