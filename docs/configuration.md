# Configuration

All options can be set via the **Settings** page in the miniEMS dashboard
(click the Settings tab) or via the add-on **Configuration** page in Home
Assistant.  Values are stored in `/data/config.json` and survive restarts,
updates, and supervisor reloads.

---

## Inverter Entities

These entity IDs come from the **ha-solarman** integration.
Open **Developer Tools → States** in HA and filter by `deye` to find your exact names.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `pv_power_entity` | string | `sensor.deye_pv_total_power` | Total PV output power (W). Sum of all strings. |
| `battery_soc_entity` | string | `sensor.deye_battery_soc` | Battery state of charge (%). |
| `battery_power_entity` | string | `sensor.deye_battery_power` | Battery charge/discharge power (W, positive = charging). |
| `battery_voltage_entity` | string | `sensor.deye_battery_voltage` | Battery terminal voltage (V). |
| `grid_power_entity` | string | `sensor.deye_grid_power` | Grid power (W, positive = import from grid, negative = export). |
| `load_power_entity` | string | `sensor.deye_load_power` | House load power (W). |
| `battery_capacity_kwh` | float | `10.0` | Usable battery capacity in kWh (nameplate × usable fraction). |
| `battery_min_soc` | int (0–100) | `15` | Minimum SoC (%). The addon will not discharge below this and will switch to Battery Protection mode. |
| `battery_max_soc` | int (0–100) | `95` | Maximum SoC (%). Charging stops when reached. |

---

## Authentication

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `long_lived_token` | string | *(empty)* | HA long-lived access token. Used as fallback when the Supervisor token is rejected (401). Create one under **Profile → Long-Lived Access Tokens** in HA. Usually not needed. |

---

## Octopus Energy / Electricity Price

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `electricity_price_entity` | string | `sensor.octopus_energy_electricity_current_rate` | Current spot price (€/kWh). Any sensor with a numeric price in €/kWh works. |
| `cheap_rate_threshold_eur` | float | `0.10` | Grid charging is triggered when the price is **below** this value (€/kWh). Set to your typical off-peak rate. |

---

## EMS Parameters

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `pv_surplus_threshold_w` | int | `200` | Minimum PV surplus over load (W) to switch into **PV Charging** mode. Prevents mode flip-flop on cloud shadows. |
| `update_interval_sec` | int (10–300) | `30` | How often the EMS decision loop runs and costs are accumulated (s). |

---

## Battery Control

> **Requires ha-solarman integration.**
> Battery control is **off by default**. Enable `battery_control_enabled` only
> after verifying the entity names and mode values are correct.
> Use **Simulation Mode** first to test the logic without touching the inverter.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `battery_control_enabled` | bool | `false` | Enable active inverter control. When `false`, the addon only monitors and calculates; it does not send any commands to the inverter. |
| `battery_control_simulation` | bool | `true` | **Simulation mode**: control commands are logged with `[SIM]` but **not** sent to HA. Safe for testing. A `SIM` badge appears in the dashboard. |
| `battery_max_charge_power_w` | int | `3000` | Maximum battery charging power (W). Must not exceed the inverter's rated charge capacity. |
| `battery_max_discharge_power_w` | int | `3000` | Maximum battery discharging power (W). Must not exceed the inverter's rated discharge capacity. |

### Inverter Control Entities

These are the ha-solarman entities the addon writes to when battery control is
enabled.  Find them in **Developer Tools → States**.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `inverter_work_mode_entity` | string | `select.deye_work_mode` | The **select** entity that controls the Deye inverter's operating mode. This is written by the addon to switch between self-use and grid-charge modes. Find it by searching for `work_mode` in HA states. |
| `inverter_charge_power_entity` | string | `number.deye_battery_charging_power` | The **number** entity that sets the maximum charging power (W). The addon writes this to cap or maximise the charge rate depending on the EMS mode. |
| `inverter_discharge_power_entity` | string | `number.deye_battery_discharging_power` | The **number** entity that sets the maximum discharging power (W). The addon sets this to `0` when it wants to prevent discharge (e.g. during grid charging or battery protection). |

### Mode Value Strings

The Deye inverter's work mode is controlled via a `select` entity.
The **exact string values** depend on your inverter model and firmware version.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `inverter_charge_mode_charge` | string | `Charging Priority` | Value written to the work mode entity when the addon wants to **charge from the grid** (cheap rate). The inverter will draw from the grid to charge the battery. Common values: `"Charging Priority"`, `"Grid Charge"`. |
| `inverter_charge_mode_selfuse` | string | `Self-Use` | Value written to the work mode entity for **normal operation** (PV charging, self-use). The inverter uses PV first, then battery, then grid. Common values: `"Self-Use"`, `"Self Use"`. |

> **How to find the correct values for your inverter:**
> 1. Open **HA → Developer Tools → States**
> 2. Find your `select.deye_work_mode` entity
> 3. In the entity's **attributes**, look for the `options` list
> 4. Copy the exact strings for charge and self-use modes
>
> The values are **case-sensitive** and must match exactly what HA shows.

### Mode Behaviour Summary

| EMS Mode | Work Mode | Charge Power | Discharge Power |
|----------|-----------|--------------|-----------------|
| `Grid Charging` | `inverter_charge_mode_charge` | `battery_max_charge_power_w` | `0` W (no discharge while grid-charging) |
| `PV Charging` | `inverter_charge_mode_selfuse` | `battery_max_charge_power_w` | `battery_max_discharge_power_w` |
| `Battery Protection` | `inverter_charge_mode_selfuse` | `battery_max_charge_power_w` | `0` W (protect depleted battery) |
| `Idle` | `inverter_charge_mode_selfuse` | `battery_max_charge_power_w` | `battery_max_discharge_power_w` |

Commands are **idempotent**: the addon only sends a service call when the value
actually changes, to avoid flooding the inverter with repeated writes.

---

## Forecast & Prediction

> **Optional.** If `weather_entity` is left empty, the prediction model falls
> back to a 30-day rolling median load — still useful for the
> `should_grid_charge` recommendation.

| Option | Type | Default | Description |
|--------|------|---------|-------------|
| `weather_entity` | string | *(empty)* | HA weather entity provided by the OpenWeatherMap integration (e.g. `weather.openweathermap`). The addon calls the `weather.get_forecasts` HA action to retrieve up to 7 days of daily forecast data — no API key or coordinates needed. Leave empty to disable weather forecast. |

### How to find your weather entity

1. Make sure the **OpenWeatherMap** integration is installed in HA.
2. Open **Developer Tools → States** and search for `weather.`.
3. The entity is typically `weather.openweathermap` or `weather.home`.

### What forecast data is used

The addon calls `weather.get_forecasts` (daily type) once per EMS cycle and
caches the result for 30 minutes.  From the response it extracts:

| Value | Source | Used for |
|-------|--------|----------|
| `avg_night_temp_c` | `temperature` of tomorrow's forecast slot | Load prediction (temperature matching) |
| `temp_today_c` | `temperature` of today's forecast slot | Dashboard display |
| `temp_tomorrow_c` | `temperature` of tomorrow's forecast slot | Dashboard display |
| `pv_factor` | Average `(1 − cloud_coverage/100)` across all slots | PV yield scaling |
| `daylight_hours` | Computed from HA instance latitude + current month | PV yield scaling |

The addon fetches your HA latitude once from `http://supervisor/core/api/config`
(using `SUPERVISOR_TOKEN`) and caches it for the lifetime of the process.

### How the Prediction Works

1. **Load prediction**: Finds historical days in the last 60 days where the
   forecast night temperature is within ±4 °C of tonight's.
   The **median load** of those days is used as the prediction.
   Falls back to a 30-day rolling median when fewer than 3 matching days exist.

2. **PV yield prediction**: Takes the 75th-percentile peak PV power from the
   last 14 days, scaled by today's cloud cover and day length.

3. **Grid-charge recommendation**:
   `should_grid_charge = (usable_battery_kwh + predicted_pv_kwh) < predicted_load_kwh`
   When the recommendation is `false` and a reliable prediction exists, the
   addon **skips** grid charging even during cheap rates — saving money when
   the battery is already sufficient.
   When `confidence = none` (not enough history yet), the addon always charges
   during cheap rates (safe default).

---

## Config Persistence

Settings survive:

| Event | Mechanism |
|-------|-----------|
| Addon **restart** | `/data/config.json` is read on startup |
| Supervisor **reload** / `options.json` reset | `config.json` fallback wins over changed defaults |
| Addon **update** | Schema migration runs automatically (v1 → … → v5) |

To fully reset to defaults, delete `/data/config.json` via the HA Terminal addon or SSH.
