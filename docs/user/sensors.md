# Home Assistant Sensors

miniEMS publishes sensors to Home Assistant after every EMS tick (default: every 30 s).
All entity IDs use the prefix `sensor.miniems_`.

Sensors are published via **MQTT Discovery** when an MQTT broker is available, or via the **HA REST API** as a fallback. MQTT sensors appear under the **miniEMS** device in HA and support long-term statistics.

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

## Today's Energy & Cost

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | € | Grid electricity cost today |
| `sensor.miniems_today_pv_savings_eur` | € | Savings from PV self-consumption today |
| `sensor.miniems_today_pv_used_kwh` | kWh | PV energy consumed by the house today |
| `sensor.miniems_today_grid_import_kwh` | kWh | Total grid import today |
| `sensor.miniems_today_load_total_kwh` | kWh | Total house load today |
| `sensor.miniems_today_load_cost_eur` | € | Hypothetical cost if all load bought from grid |

All `today_*` sensors accumulate since midnight and have `state_class: total_increasing`.

---

## Cost Comparison (v1.4.0)

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_cost_without_grid_charge` | € | Grid cost minus the portion paid to charge the battery from grid |
| `sensor.miniems_today_cost_fix_price_tarif` | € | What today's load would cost at the configured fixed tariff |
| `sensor.miniems_today_feed_in_kwh` | kWh | Energy exported to grid today |
| `sensor.miniems_today_feed_in_revenue_eur` | € | Revenue from grid export today |
| `sensor.miniems_today_grid_charge_kwh` | kWh | Energy charged into battery from the grid today |

---

## Battery State (v1.4.0)

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_battery_kwh_freetochange` | kWh | How much more can be charged before hitting max SoC |
| `sensor.miniems_battery_kwh_useable` | kWh | How much can be discharged before hitting min SoC |

Formulas:
```
free_to_charge = (max_soc − soc) / 100 × capacity_kwh
useable        = (soc − min_soc)  / 100 × capacity_kwh
```

---

## Weekly / Monthly / Yearly Totals

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_week_grid_cost_eur` | € | Rolling 7-day grid cost |
| `sensor.miniems_week_pv_saved_eur` | € | Rolling 7-day PV savings |
| `sensor.miniems_month_grid_cost_eur` | € | Calendar month grid cost |
| `sensor.miniems_month_pv_savings_eur` | € | Calendar month PV savings |
| `sensor.miniems_month_load_cost_eur` | € | Calendar month hypothetical full-grid cost |
| `sensor.miniems_year_grid_cost_eur` | € | Calendar year grid cost |
| `sensor.miniems_year_pv_savings_eur` | € | Calendar year PV savings |
| `sensor.miniems_year_load_cost_eur` | € | Calendar year hypothetical full-grid cost |

---

## Price Tier Usage

Six sensors track household load (kWh) split by electricity price tier. The tier boundaries are set by `cheap_rate_threshold_eur` and `medium_rate_threshold_eur` in Settings.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_kwh_high_rate` | kWh | Load consumed today at **high** rate (`price ≥ medium_rate_threshold`) |
| `sensor.miniems_today_kwh_medium_rate` | kWh | Load consumed today at **medium** rate |
| `sensor.miniems_today_kwh_low_rate` | kWh | Load consumed today at **low** rate (`price < cheap_rate_threshold`) |
| `sensor.miniems_month_kwh_high_rate` | kWh | Load consumed this calendar month at **high** rate |
| `sensor.miniems_month_kwh_medium_rate` | kWh | Load consumed this calendar month at **medium** rate |
| `sensor.miniems_month_kwh_low_rate` | kWh | Load consumed this calendar month at **low** rate |

All six sensors have `state_class: total_increasing` and are resilient to restarts — values are restored from the local database on startup.

---

## Prediction

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_predicted_load_kwh` | kWh | Predicted daily house load |
| `sensor.miniems_predicted_pv_kwh` | kWh | Internal PV yield estimate (fallback model) |

---

## Using Sensors in HA

### Energy Dashboard

Add these to the HA Energy Dashboard:

| Slot | Entity |
|---|---|
| Solar production | `sensor.miniems_today_pv_used_kwh` |
| Grid consumption | `sensor.miniems_today_grid_import_kwh` |

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
