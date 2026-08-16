---
revision_date: 2026-08-15
---

# Configuration

All settings are managed through the **Settings** tab in the miniEMS dashboard.
Values are stored in `/data/config.json` and survive restarts, updates, and Supervisor reloads.

!!! info "No HA add-on config UI"
    As of v1.4.0, the HA add-on "Configuration" tab is intentionally empty.
    All configuration is done through the miniEMS Settings page.

!!! info "How values are loaded"
    On every startup miniEMS reads three sources, in this priority order (highest first):

    1. **`/data/options.json`** — Supervisor-managed values that differ from the default (you changed them via the HA UI)
    2. **`/data/config.json`** — the last persisted values (survives an `options.json` reset caused by Supervisor reloads / addon updates)
    3. **Built-in defaults**

    The merged result is written back to `/data/config.json` on every start, so your settings are never lost. Old field names (e.g. from versions before v2.0) are migrated automatically to their current names.

    Values that do **not** appear on the Settings form can be edited directly as JSON via the **config.json** / **options.json** tabs in the dashboard (see the "Raw File Editors" section at the bottom of this page).

---

## Inverter Entities

Find these in **HA → Developer Tools → States** — filter by `deye` to locate your exact entity IDs.

| Setting | Default | Description |
|---|---|---|
| `pv_power_entity` | `sensor.deye_pv_total_power` | Total PV output (W) |
| `battery_soc_entity` | `sensor.deye_battery_soc` | Battery state of charge (%) |
| `battery_power_entity` | `sensor.deye_battery_power` | Battery power (W) — **negative** = charging, **positive** = discharging on the Deye 8K (verified live; an earlier version of this line had the sign backwards) |
| `grid_power_entity` | `sensor.deye_grid_power` | Grid power (W) — positive = import, negative = export |
| `load_power_entity` | `sensor.deye_load_power` | House load (W) |
| `grid_import_energy_entity` | `sensor.deye8k_today_energy_import` | HA entity providing the inverter's daily grid import total (kWh, resets at midnight). When set, replaces the calculated import kWh; grid cost is still accumulated per tick. Leave empty to fall back to calculation from `grid_power_entity`. |
| `feed_in_energy_entity` | `sensor.deye8k_today_energy_export` | HA entity providing the inverter's daily feed-in total (kWh, resets at midnight). When set, replaces the calculated feed-in value. Leave empty to fall back to calculation from `grid_power_entity`. |
| `load_consumption_entity` | `sensor.deye8k_today_load_consumption` | HA entity providing the inverter's daily household consumption total (kWh, resets at midnight). When set, replaces the value extrapolated from `load_power_entity` — making it immune to add-on restarts. Load cost (€) is still accumulated per tick, since that needs the price at each point in time. Leave empty to fall back to calculation. Since v2.0.3. |
| `grid_import_total_entity` | `sensor.deye8k_total_energy_import` | Lifetime grid-import counter (kWh, **never** resets). Preferred over the daily entity above since v2.0.4: the inverter resets its daily counters on its **own** clock — measured 4min54s after local midnight — and still reports yesterday's closing total during that window. miniEMS derives the daily delta itself instead, cutting the day where it cuts cost. The daily entity stays in use as the anchor bootstrap after a mid-day restart. Leave empty to fall back to the daily entity. |
| `feed_in_total_entity` | `sensor.deye8k_total_energy_export` | Lifetime feed-in counter. Behaves like `grid_import_total_entity`. Since v2.0.4. |
| `load_consumption_total_entity` | `sensor.deye8k_total_load_consumption` | Lifetime household-consumption counter. Behaves like `grid_import_total_entity`. Since v2.0.4. |

All five required entities above are checked for **staleness** (`sensor_max_age_sec`, default 300 s): if an entity stops updating for that long, miniEMS treats it as "unavailable" and falls back to the safe state (see the "Grid-Friendly PV Strategy" section further down).

---

## Battery Settings

| Setting | Default | Description |
|---|---|---|
| `battery_capacity_kwh` | `10.0` | Usable battery capacity in kWh. Overridden by `battery_capacity_entity` if set. |
| `battery_min_soc` | `15` | Minimum SoC (%). Below this, miniEMS switches to `Battery Protection` and blocks discharging. |
| `battery_max_soc` | `95` | Maximum SoC (%). Charging stops (`Idle` mode) once reached. |

!!! warning "Automatic safeguard"
    If `battery_min_soc ≥ battery_max_soc`, miniEMS automatically disables `battery_control_enabled` on startup and logs a warning — an inconsistent configuration can't reach the inverter unfiltered.

---

## Authentication

| Setting | Default | Description |
|---|---|---|
| `long_lived_token` | *(empty)* | HA long-lived token — used as fallback if the Supervisor token is rejected with a 401. Usually not needed. |

To create one: **HA → Profile → Long-Lived Access Tokens → Create Token**.

---

## Octopus Energy / Electricity Price

| Setting | Default | Description |
|---|---|---|
| `electricity_price_entity` | `sensor.octopus_energy_electricity_current_rate` | Current spot price sensor (€/kWh) |
| `cheap_rate_threshold_eur` | `0.10` | Grid charging triggers when price is **below** this value — also the **low** tier ceiling |
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

    The current tier is also shown next to the price on the dashboard (green / amber / red). If the price hasn't updated for longer than `price_max_age_sec` (default 21,600 s = 6 h), it's treated as stale and **cannot** trigger grid charging.

---

## EMS Parameters

| Setting | Default | Description |
|---|---|---|
| `pv_surplus_threshold_w` | `200` | Minimum PV surplus (W, `pv_power - load_power`) to trigger PV Charging mode |
| `update_interval_sec` | `30` | How often the EMS loop runs (seconds, 10–300) |
| `event_log_retention_days` | `30` | How many days of event log entries to keep in the database |

---

## Battery Control

!!! warning "Enable Simulation Mode first"
    Before enabling live control, run with `Simulation Mode` on. Verify the log shows the correct commands for your inverter.

!!! info "Amps, not watts"
    The Deye charge/discharge limits are entities in **amps**, not watts — unlike in miniEMS versions before v2.0. The old `*_power_w` field names are migrated automatically to the new `*_current_a` fields on startup; any previously stored watt values are **not** converted, since there is no valid 1:1 conversion. Double-check the migrated values in Settings once after updating from an older version.

| Setting | Default | Description |
|---|---|---|
| `battery_control_enabled` | `false` | Master switch for inverter control |
| `battery_control_simulation` | `true` | Log commands but do not send them |
| `inverter_charge_current_entity` | `number.deye8k_battery_max_charging_current` | Entity for setting the charge current limit (A) |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Switch entity that enables/disables grid charging |
| `battery_discharging_current_entity` | `number.deye8k_battery_max_discharging_current` | Entity for setting the discharge current limit (A) |
| `battery_max_charge_current_a` | `185` | Maximum charge current (A). Automatically clamped to 0–350 A. |
| `battery_max_discharge_current_a` | `185` | Maximum discharge current (A). Automatically clamped to 0–350 A. |

### How inverter control works

Every EMS mode sets **all three** inverter values explicitly, so the resulting state never depends on the mode that preceded it:

| EMS Mode | Grid Charge Switch | Charge Current | Discharge Current |
|---|---|---|---|
| Grid Charging | `switch.turn_on` | `battery_max_charge_current_a` | `0` A (prevents immediately discharging what was just bought) |
| PV Charging | `switch.turn_off` | `battery_max_charge_current_a` | `battery_max_discharge_current_a` |
| Export Surplus *(grid-friendly hold)* | `switch.turn_off` | `export_hold_charge_current_a` | `battery_max_discharge_current_a` |
| Battery Protection | `switch.turn_off` | `battery_max_charge_current_a` | `0` A |
| Idle | `switch.turn_off` | `battery_max_charge_current_a` | `battery_max_discharge_current_a` |

Commands are idempotent — miniEMS only sends a service call when the target value changes. `Export Surplus` only appears when `pv_export_priority_enabled` is on (see below).

!!! info "Write confirmation (since v2.0.1)"
    A service call Home Assistant accepts (HTTP 200) is not proof the inverter applied it — some Deye/Solarman bridges only confirm a written value on their next poll, which can lag several minutes. miniEMS therefore compares the real HA state against the target on **every** tick and automatically resends the command as long as they don't match — independently for charge current, discharge current, and the grid-charge switch. If a value stays unconfirmed, a warning appears in the dashboard banner ("Inverter control: N unconfirmed write(s)").

---

## Grid-Friendly PV Strategy (Phase 7)

Optional strategy that **exports** PV surplus to the grid instead of storing it, for as long as the Solcast remaining-today forecast stays above roughly what the battery still needs. Goal: fill the battery late in the day when there's still enough sun coming — sparing the grid an unnecessary midday export spike while still leaving enough reserve for the evening.

!!! warning "Disabled by default"
    With `pv_export_priority_enabled = false`, miniEMS behaves exactly as before: any PV surplus charges the battery immediately. The strategy also has **no effect** while `battery_control_enabled = false` — miniEMS logs a warning in that case.

| Setting | Default | Description |
|---|---|---|
| `pv_export_priority_enabled` | `false` | Master switch for the strategy |
| `pv_charge_margin_factor` | `1.2` | Safety factor applied to the battery's need when comparing it to the remaining forecast. `>1` charges earlier (buffer against an over-optimistic Solcast forecast). Automatically clamped to 0.5–3.0. |
| `pv_charge_hysteresis_frac` | `0.10` | Deadband around the trigger point (0.10 = ±10 %) so the mode cannot flap. Automatically clamped to 0.0–0.5. |
| `pv_export_min_soc_pct` | `30` | Below this SoC the export hold is **never** applied — the battery always charges. Must exceed `battery_min_soc`; otherwise it's automatically raised to `battery_min_soc + 10`. |
| `pv_charge_backstop_hour` | `14` | Local hour (0–23) from which the battery always charges regardless of the forecast — prevents an overly optimistic afternoon from leaving the battery empty. |
| `export_hold_charge_current_a` | `0` | Charge current (A) while holding the export. `0` blocks charging entirely. Automatically clamped to 0–350 A. |
| `mode_dwell_sec` | `300` | A new mode must be requested continuously for this long before it actually applies (anti-flapping). Urgent transitions (SoC protection, sensor failure) bypass this delay. Automatically clamped to 0–3600 s. |
| `battery_soc_hysteresis_pct` | `2` | `Battery Protection` is only left once the SoC has risen above `battery_min_soc + battery_soc_hysteresis_pct` — prevents flapping right at the boundary. |
| `grid_charge_min_free_kwh` | `1.0` | Minimum free battery capacity (kWh) below which grid charging is no longer considered worthwhile. |
| `grid_charge_dark_start_hour` / `grid_charge_dark_end_hour` | `21` / `6` | Time window (local hours) in which grid charging is allowed when neither today's nor tomorrow's Solcast forecast can decide it ("no more sun today, and nothing reliable known about tomorrow either"). Both automatically clamped to 0–23. |
| `sensor_max_age_sec` | `300` | Power/SoC sensors are considered stale after this long without an update — 5 minutes frozen on a live value means a broken connection. |
| `forecast_max_age_sec` | `28800` | Staleness limit for the Solcast forecast (typically updates every ~30 min in daylight, but can legitimately go quiet for 6+ hours overnight depending on your Solcast plan — 8 h of margin avoids false alarms). |
| `price_max_age_sec` | `21600` | Staleness limit for the dynamic tariff price (some providers hold the same value for several hours). |

!!! tip "How the decision works"
    - The hold (`Export Surplus`) only starts when **all** conditions are met: strategy enabled, before the backstop hour, SoC above `pv_export_min_soc_pct`, free capacity available, and the Solcast remaining-today forecast (`solcast_remaining_today_entity`) above the threshold computed from `pv_charge_margin_factor`/`pv_charge_hysteresis_frac`.
    - **Any** missing or stale input (SoC, forecast, time window) immediately releases the hold and lets the battery charge normally — the logic always fails toward "fill the battery," never toward "leave it empty."
    - When today's forecast is missing, stale, or simply spent (e.g. in the evening, once no more sun is coming), grid charging now checks **tomorrow's** Solcast forecast (`solcast_tomorrow_entity`) first, added in v2.0.1: if it's enough to refill the battery on its own, miniEMS will **not** buy grid energy. Only when tomorrow's forecast is also missing/insufficient/stale does it fall back to the plain dark window (`grid_charge_dark_start_hour`–`grid_charge_dark_end_hour`), same as before.

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

## Solcast PV Forecast

[Solcast](https://solcast.com/) provides highly accurate rooftop PV forecasts. Install the Solcast HA integration and configure the entities here.

| Setting | Default | Description |
|---|---|---|
| `solcast_remaining_today_entity` | `sensor.solcast_pv_forecast_prognose_verbleibende_leistung_heute` | Expected PV remaining for today (kWh) — used in both the grid-charge and export-hold decisions |
| `solcast_today_entity` | `sensor.solcast_pv_forecast_prognose_heute` | Total expected PV for today (kWh) — dashboard display |
| `solcast_tomorrow_entity` | `sensor.solcast_pv_forecast_prognose_morgen` | Expected PV for tomorrow (kWh) — dashboard display **and**, since v2.0.1, a fallback for the grid-charge decision (see below) |

!!! tip "Why Solcast remaining matters"
    Both the grid-charge decision and the grid-friendly PV strategy compare the battery's free capacity against `solcast_remaining_today_kwh`.
    If Solcast is not configured, stale for longer than `forecast_max_age_sec`, or today's sun is simply spent (legitimately ~0, e.g. in the evening), the day's forecast is treated as "unavailable". For the grid-charge decision, miniEMS then also checks `solcast_tomorrow_kwh` before falling back to the more conservative dark-window rules above — the grid-friendly PV strategy (export hold) still relies on the today forecast only.

---

## Advanced Sensors for Balance-Based Cost Calculation

These fields are **optional** and not yet available as form fields on the Settings page. Set them via the **config.json** tab (see the "Raw File Editors" section at the bottom of this page). When set, miniEMS uses the inverter's actual daily totals instead of the values extrapolated from instantaneous power — see [Sensors](sensors.md) for calculation details.

| Setting | Default | Description |
|---|---|---|
| `battery_charge_entity` | `sensor.deye8k_today_battery_charge` | Daily battery charge total (kWh) from the inverter |
| `battery_discharge_entity` | `sensor.deye8k_today_battery_discharge` | Daily battery discharge total (kWh) from the inverter |
| `battery_capacity_entity` | `sensor.deye8k_battery_capacity` | Battery capacity read directly from the inverter — overrides `battery_capacity_kwh` when set |
| `battery_state_entity` | `sensor.deye8k_battery_state` | Battery state enum (charging / discharging / idle) |
| `today_production_entity` | `sensor.deye8k_today_production` | Daily gross PV production (kWh) — used for the inverter efficiency calculation |
| `today_losses_entity` | `sensor.deye8k_today_losses` | Daily inverter losses (kWh) |
| `power_losses_entity` | `sensor.deye8k_power_losses` | Real-time power losses (W) — for the live efficiency display |

---

## Advanced Cost Parameters

Also editable only via **config.json**:

| Setting | Default | Description |
|---|---|---|
| `daily_base_price_eur` | `0.0` | Fixed daily base/standing charge (€/day) added on top of energy costs — e.g. your contract's monthly base fee, spread over the day |
| `avg_discharge_tariff_eur_kwh` | `0.0` | Average discharge tariff used for the battery discharge ROI calculation (€/kWh). `0` = auto-derive from the three price tiers |

---

## Raw File Editors (config.json / options.json)

The **config.json** and **options.json** tabs in the dashboard let you edit and save the entire configuration directly as JSON — useful for fields that don't (yet) have their own form field on the **Settings** page (see the two sections above).

| Tab | File | Purpose |
|---|---|---|
| **config.json** | `/data/config.json` | Persistent values written by miniEMS itself. Takes precedence over `options.json` whenever a value differs from the built-in default. Recommended for durable changes. |
| **options.json** | `/data/options.json` | Managed by the HA Supervisor. Can be overwritten by an addon reconfiguration or schema update — changes made here are not guaranteed to persist. |

Both editors validate the JSON before saving (use the "Reformat JSON" button to check/pretty-print) and restart the addon automatically after saving so the new configuration takes effect.
