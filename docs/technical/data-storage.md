# Data Storage

## File Overview

| Path | Managed by | Purpose |
|---|---|---|
| `/data/config.json` | miniEMS | Persistent config with schema version |
| `/data/miniems.db` | miniEMS (`store.py`) | SQLite daily energy history |
| HA Core entity registry | Home Assistant | `sensor.miniems_*` long-term state history |
| In-memory `status_store` | miniEMS runtime | Live values for dashboard and publishers |
| In-memory `CostOptimizer` | miniEMS runtime | Intra-day accumulators (flushed at midnight) |
| In-memory `EventLog` | miniEMS runtime | Ring buffer of 100 mode-change events |

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
  "_version": 6,
  "pv_power_entity": "sensor.deye_pv_total_power",
  "battery_soc_entity": "sensor.deye_battery_soc",
  "battery_capacity_kwh": 25.0,
  "battery_min_soc": 10,
  "battery_max_soc": 95,
  "cheap_rate_threshold_eur": 0.28,
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
  "long_lived_token": ""
}
```

### Schema Versioning

`_version` is written by `migration.py`. The current version is **6**.

When the schema changes:

1. Increment `CONFIG_SCHEMA_VERSION` in `const.py`
2. Add a migration function `_vN_to_vN1(data: dict) -> dict`
3. Register it in `MIGRATIONS` list in `migration.py`

Migration chain: v0 → v1 → v2 → v3 → v4 → v5 → v6

---

## `/data/miniems.db`

SQLite database managed by `store.py`. Contains one table:

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
    last_flush_ts      TEXT               -- UTC ISO timestamp of last write
);
```

New columns are added automatically via `ALTER TABLE` on first run after upgrade.

### Flush Behaviour

`CostOptimizer` accumulates values in-memory throughout the day. At midnight
(detected by `date.today()` changing), it calls `store.flush_day()` to write
the completed day's row to SQLite. The `last_flush_ts` column is updated on
every flush and used for downtime gap detection on startup.

### Aggregation

`store.get_aggregated()` computes rolling/calendar aggregates directly in SQL:

```sql
-- Week (rolling 7 days)
SELECT SUM(grid_cost_eur) FROM daily_stats
WHERE date >= date('now', '-6 days')

-- Month (calendar month)
SELECT SUM(grid_cost_eur) FROM daily_stats
WHERE strftime('%Y-%m', date) = strftime('%Y-%m', 'now')
```

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
_grid_charge_kwh     : {date: float}   # v1.4.0
_grid_charge_cost_eur: {date: float}   # v1.4.0
_feed_in_kwh         : {date: float}   # v1.4.0
_feed_in_revenue_eur : {date: float}   # v1.4.0
```

In-memory accumulators reset on add-on restart. Long-term persistence is
provided by the `total_increasing` HA sensors which HA records in its own DB.

### `EventLog` Ring Buffer

`event_log.py` maintains a `deque(maxlen=100)`. Two event types share the same buffer:

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

The log is served via `/api/status` → `log` array and rendered in the `/log` page. The buffer holds the last 100 events across both types combined. Events are lost on restart — they are not persisted to SQLite.
