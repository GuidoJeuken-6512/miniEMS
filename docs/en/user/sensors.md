---
revision_date: 2026-04-24
---

# Home Assistant Sensors

miniEMS registers **up to 34 sensors** in Home Assistant via the bundled custom integration.
All entity IDs use the prefix `sensor.miniems_`. The integration polls `/api/status`
every 30 s and registers all entities under the **miniEMS** device with long-term
statistics support.

> **Multilingual sensor names (from v1.6.0):** Sensor display names in the Home Assistant
> UI automatically follow the language configured in HA.
> Supported languages: **English** and **German**.
> Entity IDs (e.g. `sensor.miniems_today_grid_cost_eur`) remain unchanged.

> **Scope — addon-native only.** Live power readings (PV, load, grid, battery power,
> SoC) and the electricity price are **not** duplicated here. Those sensors already
> exist in HA from your inverter integration (e.g. Deye) and your price integration
> (e.g. Tibber, Octopus Energy). miniEMS reads those entities internally but does not
> re-publish them.

> **Scenario 2 sensors** (inverter efficiency, energy-balance grid charge, ROI) are only
> available when the corresponding inverter entities have been configured
> (`today_production_entity`, `today_losses_entity`, `battery_discharge_entity`, etc.).

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
| `sensor.miniems_battery_capacity_kwh` | kWh | Usable total battery capacity |

```
free_to_charge = (max_soc − soc) / 100 × capacity_kwh
useable        = (soc − min_soc)  / 100 × capacity_kwh
```

> When `battery_capacity_entity` is configured, the capacity is read directly from the
> inverter (e.g. 25.8 kWh). Otherwise the config value `battery_capacity_kwh` is used.

---

## Today's Energy

Accumulate from midnight; reset daily. `state_class: total_increasing`.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_pv_used_kwh` | kWh | PV energy self-consumed by the house today |
| `sensor.miniems_today_load_total_kwh` | kWh | Total house load today |
| `sensor.miniems_today_grid_charge_kwh` | kWh | Energy charged into battery from the grid today |

---

## Today's Cost & Savings

> For a full explanation of every sensor, including a worked example: [Costs &
> Savings](costs.md).

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_grid_cost_eur` | € | Actual cost of grid import today |
| `sensor.miniems_today_pv_savings_eur` | € | Savings from PV self-consumption today |
| `sensor.miniems_today_load_cost_eur` | € | Hypothetical cost if all load were bought from grid |
| `sensor.miniems_today_feed_in_revenue_eur` | € | Revenue from grid export today |
| `sensor.miniems_today_cost_without_grid_charge` | € | Grid cost minus the portion paid to charge the battery from grid |
| `sensor.miniems_today_cost_fix_price_tariff` | € | What today's load would cost at the fixed tariff (incl. base price if configured) |
| `sensor.miniems_today_base_price_eur` | € | Daily standing charge / base price (only when `daily_base_price_eur > 0` configured) |

### What do the cost sensors mean in comparison?

The different cost sensors enable direct scenario comparison:

| Sensor | Scenario | Use |
|---|---|---|
| `today_grid_cost_eur` | **Actual**: What you really paid today (incl. grid charging) | Your real daily bill |
| `today_cost_without_grid_charge` | **Without grid charging**: What it would have cost without battery charging from grid | Shows whether grid charging increased total costs |
| `today_load_cost_eur` | **Hypothetical**: Full load at dynamic spot price, as if no PV were present | Shows the value of your PV system |
| `today_cost_fix_price_tariff` | **Fixed-tariff comparison**: What the same load would cost on a classic flat tariff | Shows whether the dynamic tariff is more beneficial |

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

## Scenario 2: Grid Charging — Efficiency & ROI

These sensors are **optional** and only available when the following entities are set in the
configuration: `today_production_entity`, `today_losses_entity`, `battery_discharge_entity`,
`battery_charge_entity`.

| Entity | Unit | Description |
|---|---|---|
| `sensor.miniems_today_efficiency_pct` | % | Today's inverter efficiency |
| `sensor.miniems_today_grid_charge_kwh_bilanz` | kWh | Energy charged from grid into battery (energy-balance method) |
| `sensor.miniems_today_grid_charge_cost_bilanz_eur` | € | Cost of grid charging today (balance-based) |
| `sensor.miniems_today_grid_charge_roi_eur` | € | Net profit of the grid-charging strategy today |

### What do these sensors mean?

#### `miniems_today_efficiency_pct` — Inverter Efficiency

The inverter converts stored energy into usable electricity — with losses. Efficiency
describes how much of the produced PV energy actually reaches the household.

```
η = (PV production today − losses today) / PV production today × 100
```

**Example:** PV production 32.5 kWh, losses 2.6 kWh → η = 92.0 %

> Typical values: 88–95 %. The higher, the more efficiently the inverter is operating.

---

#### `miniems_today_grid_charge_kwh_bilanz` — Grid Charge (Energy Balance)

Shows how much energy **actually flowed from the grid into the battery** today.
The calculation uses the inverter's energy-balance formula — more precise than
approximating from instantaneous power readings:

```
grid_charge_energy = grid_import_today − load_consumption_today + battery_discharge_today
```

**Why not just use the grid import?**
Part of the grid import covers household consumption directly. The formula subtracts
this portion, leaving only the true battery-charging share.

**Plausibility check:** The value must be ≥ 0 and ≤ total battery charge today.

---

#### `miniems_today_grid_charge_roi_eur` — Grid Charging ROI

The most important sensor for evaluating the grid-charging strategy:

```
profit = (grid_charge_kWh × efficiency × avg_discharge_tariff) − grid_charge_cost
```

- **Positive value** → Grid charging paid off today ✅
- **Negative value** → Grid charging cost more than the benefit ❌
- **No value shown** → Discharge tariff (`avg_discharge_tariff_eur_kwh`) not configured

> **Configuration required:** `avg_discharge_tariff_eur_kwh` must be set to the average
> tariff at which the battery discharges (e.g. 0.394366 for pure evening peak, or
> 0.369376 for mixed evening/night discharge).

**Concrete example:**
```
Grid charge: 5.5 kWh, η: 92 %, discharge tariff: 0.394 €/kWh, charge cost: 1.51 €
Usable energy: 5.5 × 0.92 = 5.06 kWh
Saving: 5.06 × 0.394 = 1.99 €
ROI = 1.99 − 1.51 = +0.48 € ✅
```

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
