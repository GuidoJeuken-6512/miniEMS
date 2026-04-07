---
revision_date: 2026-04-06
---

# HA Sensor Reference

miniEMS exposes **30 Home Assistant sensors** via the custom integration bundled in
`miniems/rootfs/usr/bin/integration/`. The integration polls `/api/status` every
30 seconds and registers all sensors under a single **miniEMS** device.

All entity IDs use the prefix `sensor.miniems_`.

> **Scope:** Only addon-native, computed values are registered here. Live power
> readings (PV, load, grid, battery power, SoC) and the electricity price already
> exist in HA via the Deye / Tibber / Octopus integration — miniEMS reads those
> entities but does not duplicate them.

---

## Data Flow

```
EMS Controller (30 s tick)
  └─► status_store (in-memory dict, main.py)
        └─► Web Server  GET /api/status
              └─► HA Coordinator (polls every 30 s)
                    └─► Sensor entities in Home Assistant
```

Spike-filtered live power readings are validated by `SensorValidator` before they
reach the status store. Accumulators (today/month/year) are persisted to SQLite and
restored on add-on restart.

---

## Operating Mode

| Entity ID | Key | Unit | Device Class | State Class |
|---|---|---|---|---|
| `sensor.miniems_mode` | `mode` | — | — | `measurement` |

Current EMS operating mode. Possible values: `Idle`, `PV Charging`,
`Grid Charging`, `Battery Protection`.

Icon: `mdi:home-lightning-bolt`

---

## Battery State (Computed)

Derived by `battery_model.py` from the current SoC, battery capacity, and the
configured min/max SoC limits.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_battery_kwh_freetochange` | `battery_kwh_freetochange` | kWh | `energy` | `measurement` | Headroom until max SoC (chargeable capacity) |
| `sensor.miniems_battery_kwh_useable` | `battery_kwh_useable` | kWh | `energy` | `measurement` | Available until min SoC (dischargeable capacity) |

---

## Today's Energy

Accumulate from midnight; reset daily. Stored in the SQLite `daily_stats` table.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_today_pv_used_kwh` | `today_pv_used_kwh` | kWh | `energy` | `total_increasing` | PV self-consumed by the house |
| `sensor.miniems_today_grid_import_kwh` | `today_grid_import_kwh` | kWh | `energy` | `total_increasing` | Total energy drawn from grid |
| `sensor.miniems_today_load_total_kwh` | `today_load_total_kwh` | kWh | `energy` | `total_increasing` | Total house consumption |
| `sensor.miniems_today_feed_in_kwh` | `today_feed_in_kwh` | kWh | `energy` | `total_increasing` | Energy exported to grid |
| `sensor.miniems_today_grid_charge_kwh` | `today_grid_charge_kwh` | kWh | `energy` | `total_increasing` | Energy charged into battery from grid |

---

## Today's Tariff Tiers

Grid import split by price tier. Thresholds configured via `cheap_rate_threshold`
and `medium_rate_threshold` in settings.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_today_kwh_high_rate` | `today_kwh_high_rate` | kWh | `energy` | `total_increasing` | Load at high-price hours |
| `sensor.miniems_today_kwh_medium_rate` | `today_kwh_medium_rate` | kWh | `energy` | `total_increasing` | Load at medium-price hours |
| `sensor.miniems_today_kwh_low_rate` | `today_kwh_low_rate` | kWh | `energy` | `total_increasing` | Load at low-price hours |

---

## Today's Cost & Savings

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | `today_grid_cost_eur` | € | `monetary` | `total_increasing` | Actual cost of grid import |
| `sensor.miniems_today_pv_savings_eur` | `today_pv_savings_eur` | € | `monetary` | `total_increasing` | Savings from PV self-consumption |
| `sensor.miniems_today_load_cost_eur` | `today_load_cost_eur` | € | `monetary` | `total_increasing` | Hypothetical cost if all load bought from grid |
| `sensor.miniems_today_feed_in_revenue_eur` | `today_feed_in_revenue_eur` | € | `monetary` | `total_increasing` | Revenue from grid export |
| `sensor.miniems_today_cost_without_grid_charge` | `today_cost_without_grid_charge` | € | `monetary` | `total_increasing` | Grid cost minus battery-charging cost |
| `sensor.miniems_today_cost_fix_price_tariff` | `today_cost_fix_price_tariff` | € | `monetary` | `total_increasing` | Today's load at the configured fixed tariff (`fix_price`) |

---

## Weekly Totals

Rolling 7-day window, summed from `daily_stats`.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_week_grid_cost_eur` | `week_grid_cost_eur` | € | `monetary` | `measurement` | 7-day grid cost |
| `sensor.miniems_week_pv_savings_eur` | `week_pv_savings_eur` | € | `monetary` | `measurement` | 7-day PV savings |

---

## Monthly Totals

Calendar month, summed from `daily_stats`.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_month_grid_cost_eur` | `month_grid_cost_eur` | € | `monetary` | `measurement` | Month grid cost |
| `sensor.miniems_month_pv_savings_eur` | `month_pv_savings_eur` | € | `monetary` | `measurement` | Month PV savings |
| `sensor.miniems_month_load_cost_eur` | `month_load_cost_eur` | € | `monetary` | `measurement` | Month hypothetical full-grid cost |
| `sensor.miniems_month_kwh_high_rate` | `month_kwh_high_rate` | kWh | `energy` | `total_increasing` | Month load at high rate |
| `sensor.miniems_month_kwh_medium_rate` | `month_kwh_medium_rate` | kWh | `energy` | `total_increasing` | Month load at medium rate |
| `sensor.miniems_month_kwh_low_rate` | `month_kwh_low_rate` | kWh | `energy` | `total_increasing` | Month load at low rate |

---

## Yearly Totals

Calendar year, summed from `daily_stats`.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_year_grid_cost_eur` | `year_grid_cost_eur` | € | `monetary` | `measurement` | Year grid cost |
| `sensor.miniems_year_pv_savings_eur` | `year_pv_savings_eur` | € | `monetary` | `measurement` | Year PV savings |
| `sensor.miniems_year_load_cost_eur` | `year_load_cost_eur` | € | `monetary` | `measurement` | Year hypothetical full-grid cost |

---

## Predictions

Calculated by `consumption_model.py` (load) and `solcast_client.py` / internal
model (PV). Updated once per day or when conditions change.

| Entity ID | Key | Unit | Device Class | State Class | Description |
|---|---|---|---|---|---|
| `sensor.miniems_predicted_load_kwh` | `predicted_load_kwh` | kWh | `energy` | `measurement` | Predicted daily house load |
| `sensor.miniems_predicted_pv_kwh` | `predicted_pv_kwh` | kWh | `energy` | `measurement` | Internal PV yield estimate (fallback when Solcast unavailable) |

### Prediction source

The `/api/status` response includes a `prediction_source` field (not a HA sensor):

| Value | Meaning |
|---|---|
| `"historical"` | Median of temperature-matched days from `daily_stats` |
| `"fallback"` | Temperature rule-based estimate |

---

## Sensor Count Summary

| Category | Count |
|---|---|
| Operating mode | 1 |
| Battery state (computed) | 2 |
| Today's energy | 5 |
| Today's tariff tiers | 3 |
| Today's cost & savings | 6 |
| Weekly totals | 2 |
| Monthly totals | 6 |
| Yearly totals | 3 |
| Predictions | 2 |
| **Total** | **30** |

---

## Fields in `/api/status` Not Exposed as HA Sensors

The following fields are returned by the REST endpoint and used by the dashboard
but are **not** registered as Home Assistant sensor entities:

| Field | Type | Description |
|---|---|---|
| `is_cheap_rate` | bool | Current price below cheap-rate threshold |
| `price_tier` | string | `"low"` / `"medium"` / `"high"` |
| `battery_control_active` | bool | Battery control loop running |
| `battery_control_simulation` | bool | Simulation mode active |
| `warnings` | array | Configuration/sensor availability warning keys |
| `log` | array | Event log entries (mode and price changes) |
| `solcast_remaining_today_kwh` | float? | Remaining Solcast forecast for today |
| `solcast_today_kwh` | float? | Solcast total forecast today |
| `solcast_tomorrow_kwh` | float? | Solcast total forecast tomorrow |
| `charge_power_limit_w` | int? | Active charge power limit |
| `discharge_power_limit_w` | int? | Active discharge power limit |
| `prediction_confidence` | string | `"high"` / `"medium"` / `"low"` / `"none"` |
| `prediction_source` | string | `"historical"` or `"fallback"` |
| `temp_today_c` | float? | Today's outdoor temperature from HA weather entity |
| `temp_tomorrow_c` | float? | Tomorrow's outdoor temperature forecast |

---

## Integration File Locations

| File | Purpose |
|---|---|
| `integration/sensor.py` | `SENSOR_DESCRIPTIONS` tuple — all 36 sensor definitions |
| `integration/coordinator.py` | `DataUpdateCoordinator` — polls `/api/status` every 30 s |
| `integration/__init__.py` | Integration setup and device registration |
