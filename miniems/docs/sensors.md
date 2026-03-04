# Home Assistant Sensors

miniEMS publishes **7 sensor entities** to Home Assistant after every EMS tick.
All entity IDs use the prefix `sensor.miniems_`.

> **Design decision**: Only addon-*calculated* values are published.
> Input sensors (PV power, load, grid, battery SoC, electricity price) already
> exist in HA from the Deye inverter and Octopus Energy integrations.
> Publishing duplicates would create redundant entities.

States are written via `POST /api/states/<entity_id>` to the HA Core REST API.

## Operating Mode

| Entity ID | Friendly Name | Unit | Device Class | State Class |
|---|---|---|---|---|
| `sensor.miniems_mode` | miniEMS Mode | — | — | — |

### `sensor.miniems_mode` Values

| State | Meaning |
|---|---|
| `Idle` | No active charging decision |
| `PV Charging` | PV surplus > threshold → charging from PV |
| `Grid Charging (Cheap Rate)` | Spot price < threshold → charging from grid |
| `Battery Protection (Min SoC)` | SoC below minimum → protecting battery |

## Cost & Savings Sensors

| Entity ID | Friendly Name | Unit | Device Class | State Class |
|---|---|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | miniEMS Today Grid Cost | € | monetary | total_increasing |
| `sensor.miniems_today_pv_savings_eur` | miniEMS Today PV Savings | € | monetary | total_increasing |
| `sensor.miniems_today_pv_used_kwh` | miniEMS Today PV Used | kWh | energy | total_increasing |
| `sensor.miniems_today_grid_import_kwh` | miniEMS Today Grid Import | kWh | energy | total_increasing |
| `sensor.miniems_week_grid_cost_eur` | miniEMS Week Grid Cost | € | monetary | measurement |
| `sensor.miniems_week_pv_savings_eur` | miniEMS Week PV Savings | € | monetary | measurement |

> **Daily sensors** (`today_*`) accumulate since midnight and reset at 00:00.
> They use `state_class: total_increasing` so HA can track them in the
> energy dashboard.
>
> **Weekly sensors** (`week_*`) sum the last 7 calendar days and use
> `state_class: measurement` because the value can decrease when an old day
> drops out of the window.

## Using Sensors in HA

### Energy Dashboard

Add `sensor.miniems_today_pv_used_kwh` as **Solar production** and
`sensor.miniems_today_grid_import_kwh` as **Grid consumption** in
**Settings → Energy**.

### Example Automation

```yaml
trigger:
  - platform: numeric_state
    entity_id: sensor.miniems_battery_soc
    below: 20
action:
  - service: notify.mobile_app
    data:
      message: "miniEMS: Battery SoC below 20%!"
```

### Lovelace Card

```yaml
type: entities
title: miniEMS
entities:
  - sensor.miniems_mode
  - sensor.miniems_pv_power_w
  - sensor.miniems_battery_soc
  - sensor.miniems_today_pv_savings_eur
  - sensor.miniems_today_grid_cost_eur
```
