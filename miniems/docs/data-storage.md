# Data Storage

## File Overview

| Path | Managed by | Purpose |
|---|---|---|
| `/data/options.json` | HA Supervisor | UI configuration values |
| `/data/config.json` | miniEMS | Persistent merged config with version |
| HA Core entity registry | Home Assistant | `sensor.miniems_*` state history |
| In-memory `status_store` | miniEMS runtime | Live values for dashboard API |
| In-memory `CostOptimizer` | miniEMS runtime | Accumulated daily/weekly costs |

---

## `/data/options.json`

Written by the HA Supervisor when the user saves the add-on configuration page.
The add-on reads this file on startup but **never writes to it**.

Example:
```json
{
  "pv_power_entity": "sensor.deye_pv_total_power",
  "battery_soc_entity": "sensor.deye_battery_soc",
  "cheap_rate_threshold_eur": 0.08,
  "long_lived_token": "",
  "update_interval_sec": 30
}
```

---

## `/data/config.json`

Created and maintained by `config_loader.py`. Survives supervisor reloads and
add-on updates that reset `options.json` to defaults.

```json
{
  "_version": 1,
  "pv_power_entity": "sensor.deye_pv_total_power",
  "battery_soc_entity": "sensor.deye_battery_soc",
  "cheap_rate_threshold_eur": 0.08,
  "long_lived_token": "",
  "update_interval_sec": 30
}
```

### Merge Priority (on every startup)

```
options.json value ≠ dataclass default  →  options.json wins (user changed it in UI)
options.json value = dataclass default  →  config.json wins (options.json was reset)
key missing from config.json            →  options.json is used (new field)
```

### Schema Version

`_version` is written by `migration.py`. When the schema changes, increment
`CURRENT_VERSION` and add a migration function `_vN_to_vN1(data)`.

---

## HA Entity States (`sensor.miniems_*`)

States are **stateless REST writes** — miniEMS calls:

```
POST http://hassio/homeassistant/api/states/sensor.miniems_<name>
{
  "state": "<value>",
  "attributes": { "unit_of_measurement": "...", ... }
}
```

Home Assistant stores these in its own database (SQLite / MariaDB). The
add-on does not maintain a local copy beyond `status_store`.

---

## In-Memory Runtime Data

### `status_store` (shared dict)

Updated by `ems_loop` after every EMS tick. Read by:
- `web_server` → `/api/status` endpoint → dashboard
- `ha_sensor_publisher` → REST POST to HA Core

Keys: see [HA Sensors](sensors.md) for the full list.

### `CostOptimizer` accumulators

Stored as `defaultdict(float)` keyed by `date`:

```python
_grid_import_kwh  : {date: float}
_pv_used_kwh      : {date: float}
_grid_cost_eur    : {date: float}
_pv_saved_eur     : {date: float}
```

These reset on add-on restart. For persistent energy statistics use HA's
energy dashboard, which reads from the `total_increasing` sensors.
