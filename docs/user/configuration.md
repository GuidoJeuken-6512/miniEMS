# Configuration

All settings are managed through the **Settings** tab in the miniEMS dashboard.
Values are stored in `/data/config.json` and survive restarts, updates, and Supervisor reloads.

!!! info "No HA add-on config UI"
    As of v1.4.0, the HA add-on "Configuration" tab is intentionally empty.
    All configuration is done through the miniEMS Settings page.

---

## Inverter Entities

Find these in **HA → Developer Tools → States** — filter by `deye` to locate your exact entity IDs.

| Setting | Default | Description |
|---|---|---|
| `pv_power_entity` | `sensor.deye_pv_total_power` | Total PV output (W) |
| `battery_soc_entity` | `sensor.deye_battery_soc` | Battery state of charge (%) |
| `battery_power_entity` | `sensor.deye_battery_power` | Battery power (W) — positive = charging on the Deye 8K |
| `grid_power_entity` | `sensor.deye_grid_power` | Grid power (W) — positive = import, negative = export |
| `load_power_entity` | `sensor.deye_load_power` | House load (W) |

---

## Battery Settings

| Setting | Default | Description |
|---|---|---|
| `battery_capacity_kwh` | `25.0` | Usable battery capacity in kWh |
| `battery_min_soc` | `10` | Minimum SoC (%). Discharging is blocked below this value |
| `battery_max_soc` | `95` | Maximum SoC (%). Charging stops when reached |

---

## Electricity Price

| Setting | Default | Description |
|---|---|---|
| `electricity_price_entity` | `sensor.octopus_a_10fc0646_electricity_price` | Current spot price sensor (€/kWh) |
| `cheap_rate_threshold_eur` | `0.28` | Grid charging triggers when price is **below** this value — also the **low** tier ceiling |
| `medium_rate_threshold_eur` | `0.20` | Price tier boundary: **low** below `cheap_rate_threshold_eur`, **medium** between the two, **high** at or above this value |
| `feed_in_tariff_eur_kwh` | `0.08` | Revenue per kWh exported to the grid |
| `fix_price` | `0.30` | Fixed-rate tariff for the "Cost at Fix Price" comparison sensor |

!!! info "Price tier logic"
    Three tiers classify every tick's consumption for the [Price Tier Usage](dashboard.md#price-tier-usage) dashboard section and the six `sensor.miniems_*kwh*rate` HA sensors:

    | Tier | Condition |
    |---|---|
    | **Low** | `price < cheap_rate_threshold_eur` |
    | **Medium** | `cheap_rate_threshold_eur ≤ price < medium_rate_threshold_eur` |
    | **High** | `price ≥ medium_rate_threshold_eur` |

    The current tier is also shown next to the price on the dashboard (green / amber / red).

---

## Battery Control

!!! warning "Enable Simulation Mode first"
    Before enabling live control, run with `Simulation Mode` on. Verify the log shows the correct commands for your inverter.

| Setting | Default | Description |
|---|---|---|
| `battery_control_enabled` | `false` | Master switch for inverter control |
| `battery_control_simulation` | `true` | Log commands but do not send them |
| `inverter_charge_power_entity` | `number.deye_battery_charging_power` | Entity for setting charge power (W) |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Switch entity that enables/disables grid charging |
| `battery_discharging_power_entity` | `number.deye8k_battery_discharging_power` | Entity for setting discharge power limit (W) |
| `battery_max_charge_power_w` | `5500` | Maximum charging power (W) |
| `battery_max_discharge_power_w` | `5500` | Maximum discharging power (W) |
| `default_discharge_power_w` | `185` | Discharge power during normal (non-grid-charge) operation |

### How inverter control works

| EMS Mode | Grid Charge Switch | Discharge Power |
|---|---|---|
| Grid Charging | `switch.turn_on` | Set to `0` W |
| PV Charging | `switch.turn_off` | Restored to `default_discharge_power_w` |
| Battery Protection | `switch.turn_off` | Set to `0` W |
| Idle | `switch.turn_off` | Restored to `default_discharge_power_w` |

Commands are idempotent — miniEMS only sends a service call when the value actually changes.

---

## Solcast PV Forecast

[Solcast](https://solcast.com/) provides highly accurate rooftop PV forecasts. Install the Solcast HA integration and configure the entities here.

| Setting | Default | Description |
|---|---|---|
| `solcast_remaining_today_entity` | `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute` | Expected PV remaining for today (kWh) — used in grid-charge decision |
| `solcast_today_entity` | `sensor.solcast_pv_forecast_prognose_heute` | Total expected PV for today (kWh) — dashboard display |
| `solcast_tomorrow_entity` | `sensor.solcast_pv_forecast_prognose_morgen` | Expected PV for tomorrow (kWh) — dashboard display |

!!! tip "Why Solcast remaining matters"
    The grid-charge decision compares `battery_kwh_freetochange` vs `solcast_remaining_today_kwh`.
    If the battery has more room than the sun can deliver today, miniEMS charges from the grid.
    If Solcast is unavailable, the internal temperature-based prediction is used as a fallback.

---

## Forecast & Prediction

| Setting | Default | Description |
|---|---|---|
| `weather_entity` | `weather.openweathermap` | HA weather entity for temperature-based load prediction |

The prediction model uses historical consumption data from similar-temperature days. If no history exists yet, temperature-based fallback rules apply:

| Condition | Predicted Load |
|---|---|
| Night temp < 0 °C and day temp < 0 °C | 30 kWh |
| Night temp < 0 °C and day temp < 10 °C | 20 kWh |
| Night temp > 0 °C and day temp < 15 °C | 10 kWh |

---

## EMS Parameters

| Setting | Default | Description |
|---|---|---|
| `pv_surplus_threshold_w` | `200` | Minimum PV surplus (W) to trigger PV Charging mode |
| `update_interval_sec` | `30` | How often the EMS loop runs (seconds) |
| `event_log_retention_days` | `30` | How many days of event log entries to keep in the database |

---

## Authentication

| Setting | Default | Description |
|---|---|---|
| `long_lived_token` | *(empty)* | HA long-lived token — used as fallback if the Supervisor token is rejected with a 401. Usually not needed. |

To create one: **HA → Profile → Long-Lived Access Tokens → Create Token**.
