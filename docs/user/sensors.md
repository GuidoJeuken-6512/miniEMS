---
revision_date: 2026-04-06
---

# Home Assistant Sensors

miniEMS registers **30 sensors** in Home Assistant via the bundled custom integration.
All entity IDs use the prefix `sensor.miniems_`. The integration polls `/api/status`
every 30 s and registers all entities under the **miniEMS** device with long-term
statistics support.

> **Scope — addon-native only.** Live power readings (PV, load, grid, battery power,
> SoC) and the electricity price are **not** duplicated here. Those sensors already
> exist in HA from your inverter integration (e.g. Deye) and your price integration
> (e.g. Tibber, Octopus Energy). miniEMS reads those entities internally but does not
> re-publish them.

---

## Operating Mode

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_mode` | — | Current EMS mode string |

| Value | Meaning |
|---|---|
| `Idle` | No active action — monitoring only |
| `PV Charging` | PV surplus detected — charging from solar |
| `Grid Charging (Cheap Rate)` | Cheap rate active and battery needs charging |
| `Battery Protection (Min SoC)` | SoC below minimum — discharging blocked |

---

## Battery State

Computed from the current SoC and the configured capacity / SoC limits.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_battery_kwh_freetochange` | kWh | Headroom until max SoC (chargeable capacity) |
| `sensor.miniems_battery_kwh_useable` | kWh | Available until min SoC (dischargeable capacity) |

```
free_to_charge = (max_soc − soc) / 100 × capacity_kwh
useable        = (soc − min_soc)  / 100 × capacity_kwh
```

---

## Today's Energy

Accumulate from midnight; reset daily. `state_class: total_increasing`.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_pv_used_kwh` | kWh | PV energy self-consumed by the house today |
| `sensor.miniems_today_grid_import_kwh` | kWh | Total grid import today |
| `sensor.miniems_today_load_total_kwh` | kWh | Total house load today |
| `sensor.miniems_today_feed_in_kwh` | kWh | Energy exported to grid today |
| `sensor.miniems_today_grid_charge_kwh` | kWh | Energy charged into battery from the grid today |

---

## Today's Cost & Savings

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | € | Actual cost of grid import today |
| `sensor.miniems_today_pv_savings_eur` | € | Savings from PV self-consumption today |
| `sensor.miniems_today_load_cost_eur` | € | Hypothetical cost if all load were bought from grid |
| `sensor.miniems_today_feed_in_revenue_eur` | € | Revenue from grid export today |
| `sensor.miniems_today_cost_without_grid_charge` | € | Grid cost minus the portion paid to charge the battery from grid |
| `sensor.miniems_today_cost_fix_price_tariff` | € | What today's load would cost at the configured fixed tariff |

---

## Price Tier Usage

Load (kWh) split by electricity price tier. Tier boundaries are set by
`cheap_rate_threshold_eur` and `medium_rate_threshold_eur` in Settings.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_kwh_high_rate` | kWh | Load today at **high** rate (`price ≥ medium_rate_threshold`) |
| `sensor.miniems_today_kwh_medium_rate` | kWh | Load today at **medium** rate |
| `sensor.miniems_today_kwh_low_rate` | kWh | Load today at **low** rate (`price < cheap_rate_threshold`) |
| `sensor.miniems_month_kwh_high_rate` | kWh | Load this calendar month at **high** rate |
| `sensor.miniems_month_kwh_medium_rate` | kWh | Load this calendar month at **medium** rate |
| `sensor.miniems_month_kwh_low_rate` | kWh | Load this calendar month at **low** rate |

All six sensors have `state_class: total_increasing` and are restored from the
database on restart.

---

## Weekly / Monthly / Yearly Totals

Aggregated from the `daily_stats` database table.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_week_grid_cost_eur` | € | Rolling 7-day grid cost |
| `sensor.miniems_week_pv_savings_eur` | € | Rolling 7-day PV savings |
| `sensor.miniems_month_grid_cost_eur` | € | Calendar month grid cost |
| `sensor.miniems_month_pv_savings_eur` | € | Calendar month PV savings |
| `sensor.miniems_month_load_cost_eur` | € | Calendar month hypothetical full-grid cost |
| `sensor.miniems_year_grid_cost_eur` | € | Calendar year grid cost |
| `sensor.miniems_year_pv_savings_eur` | € | Calendar year PV savings |
| `sensor.miniems_year_load_cost_eur` | € | Calendar year hypothetical full-grid cost |

---

## Predictions

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_predicted_load_kwh` | kWh | Predicted daily house load (temperature-matched historical data) |
| `sensor.miniems_predicted_pv_kwh` | kWh | Internal PV yield estimate (fallback when Solcast unavailable) |

---

## Using Sensors in HA

### Example Lovelace card

```yaml
type: entities
title: miniEMS
entities:
  - sensor.miniems_mode
  - sensor.miniems_today_grid_cost_eur
  - sensor.miniems_today_pv_savings_eur
  - sensor.miniems_today_cost_without_grid_charge
  - sensor.miniems_battery_kwh_freetochange
  - sensor.miniems_battery_kwh_useable
  - sensor.miniems_predicted_load_kwh
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
      message: "miniEMS: Charging from cheap grid rate."
```
