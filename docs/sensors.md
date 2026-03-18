# Home Assistant Sensors

miniEMS publishes **21 sensor entities** to Home Assistant after every EMS
tick.  All entity IDs use the prefix `sensor.miniems_`.

Sensors are published via the HA REST API (`POST /api/states/…`) after every
EMS tick using the `SUPERVISOR_TOKEN`.

> **Design decision**: Only addon-*calculated* values are published.
> Input sensors (PV power, load, grid, battery SoC, price) already exist
> in HA from the Deye/Octopus integrations — publishing duplicates would
> create redundant entities.

---

## Operating Mode

| Entity ID | Friendly Name | Unit |
|-----------|---------------|------|
| `sensor.miniems_mode` | miniEMS Mode | — |

| State | Meaning |
|-------|---------|
| `Idle` | Normal operation, no active charging decision |
| `PV Charging` | PV surplus exceeds threshold → inverter charges battery from PV |
| `Grid Charging (Cheap Rate)` | Spot price below threshold AND model recommends charging → grid charges battery |
| `Battery Protection (Min SoC)` | SoC dropped below minimum → discharging blocked, charging allowed |

---

## Today's Energy & Cost

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_today_grid_cost_eur` | € | `total_increasing` | Cost of electricity imported from grid today |
| `sensor.miniems_today_pv_savings_eur` | € | `total_increasing` | Money saved by using PV instead of buying from grid |
| `sensor.miniems_today_pv_used_kwh` | kWh | `total_increasing` | PV energy directly consumed by the house today |
| `sensor.miniems_today_grid_import_kwh` | kWh | `total_increasing` | Total grid import today |
| `sensor.miniems_today_load_total_kwh` | kWh | `total_increasing` | Total house load today (useful for consumption tracking) |
| `sensor.miniems_today_load_cost_eur` | € | `total_increasing` | Hypothetical cost if all of today's load had been bought from grid at spot prices |

`today_*` sensors accumulate since midnight and reset at 00:00.
`state_class: total_increasing` lets HA track them in the **Energy Dashboard**.

---

## Weekly Totals

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_week_grid_cost_eur` | € | `measurement` | Grid cost – rolling 7-day sum |
| `sensor.miniems_week_pv_savings_eur` | € | `measurement` | PV savings – rolling 7-day sum |

`measurement` is used because the value can decrease when an old day drops out
of the window.

---

## Monthly Totals

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_month_grid_cost_eur` | € | `measurement` | Grid cost this calendar month |
| `sensor.miniems_month_pv_savings_eur` | € | `measurement` | PV savings this calendar month |
| `sensor.miniems_month_load_cost_eur` | € | `measurement` | Hypothetical full-grid load cost this month |

---

## Yearly Totals

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_year_grid_cost_eur` | € | `measurement` | Grid cost this calendar year |
| `sensor.miniems_year_pv_savings_eur` | € | `measurement` | PV savings this calendar year |
| `sensor.miniems_year_load_cost_eur` | € | `measurement` | Hypothetical full-grid load cost this year |

---

## Battery Control (Phase 2)

Only published when the addon has sent inverter commands.

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_charge_power_limit_w` | W | `measurement` | Currently configured maximum charging power |
| `sensor.miniems_discharge_power_limit_w` | W | `measurement` | Currently configured maximum discharging power |

When `battery_control_simulation` is active, these reflect the **intended**
limits (what would be written), not necessarily what the inverter is actually
set to.

---

## Forecast & Prediction (Phase 3)

Published every EMS tick.  Values are `0` until enough history exists.

| Entity ID | Unit | State Class | Description |
|-----------|------|-------------|-------------|
| `sensor.miniems_predicted_load_kwh` | kWh | `measurement` | Predicted total house load for today, based on historical similar days |
| `sensor.miniems_predicted_pv_kwh` | kWh | `measurement` | Predicted PV yield for today, based on cloud cover forecast and recent peak PV |

---

## Using Sensors in HA

### Energy Dashboard

| Dashboard slot | Entity |
|----------------|--------|
| Solar production | `sensor.miniems_today_pv_used_kwh` |
| Grid consumption | `sensor.miniems_today_grid_import_kwh` |

### Example Lovelace card

```yaml
type: entities
title: miniEMS
entities:
  - sensor.miniems_mode
  - sensor.miniems_today_pv_savings_eur
  - sensor.miniems_today_grid_cost_eur
  - sensor.miniems_today_load_cost_eur
  - sensor.miniems_predicted_load_kwh
  - sensor.miniems_predicted_pv_kwh
  - sensor.miniems_charge_power_limit_w
  - sensor.miniems_discharge_power_limit_w
```

### Example Automation

```yaml
alias: Notify when cheap rate starts
trigger:
  - platform: state
    entity_id: sensor.miniems_mode
    to: "Grid Charging (Cheap Rate)"
action:
  - service: notify.mobile_app_your_phone
    data:
      message: "miniEMS: Cheap rate active, charging battery from grid."
```
