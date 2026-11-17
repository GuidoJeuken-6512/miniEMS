---
revision_date: 2026-08-28
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
| `solcast_last_fetch_entity` | `sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` | Timestamp of the last **successful** Solcast fetch. What is evaluated is this entity's **value**, not its timestamp — the value is exactly the age of the forecast data. Needed because Solcast keeps serving its disk cache when the API is unreachable: the sensors stay available, their timestamps keep advancing, and nothing reveals that the numbers are days old. Beyond 30 h of data age the forecast is discarded for decisions and reported in the banner. Leave empty to disable the check. Since v2.0.4. |
| `grid_charge_min_margin_eur_kwh` | `0.02` | Minimum economic margin for grid charging (€/kWh). Charging happens only when `efficiency × discharge_tariff − purchase price` exceeds this. The discharge tariff is derived from history (discharge-weighted price over the last 7 days); `avg_discharge_tariff_eur_kwh` overrides it when set. While efficiency or discharge tariff are unknown the check does not apply. Since v2.0.4. |

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

!!! tip "Quick switches: “Control Mode” section"
    At the top of the Settings tab, four switches take effect **immediately, without a restart**:
    **Simulation** (battery and heat pump together), **battery control on/off**,
    **heat pump control on/off** and **fill missing history** (import from HA statistics, see
    [Forecast & Prediction](#forecast-prediction); switching it on runs the import at once).
    They are saved to `config.json` and survive restarts; their values in `options.json` are ignored.

!!! info "Amps, not watts"
    The Deye charge/discharge limits are entities in **amps**, not watts — unlike in miniEMS versions before v2.0. The old `*_power_w` field names are migrated automatically to the new `*_current_a` fields on startup; any previously stored watt values are **not** converted, since there is no valid 1:1 conversion. Double-check the migrated values in Settings once after updating from an older version.

| Setting | Default | Description |
|---|---|---|
| `battery_control_enabled` | `false` | Master switch for inverter control |
| `battery_control_simulation` | `true` | Log commands but do not send them |
| `inverter_charge_current_entity` | `number.deye8k_battery_max_charging_current` | Entity for setting the charge current limit (A) |
| `inverter_grid_charge_current_entity` | `number.deye8k_battery_grid_charging_current` | The Deye's own limit for charging from the grid (A). While grid charging the smaller of it and the charge current applies, so in *Grid Charging* mode miniEMS writes the same value to both (at most `battery_max_grid_charge_current_a`) and confirms it. Empty = not written. |
| `battery_max_grid_charge_current_a` | `185` | Value of the grid-charge limit in every other mode, on shutdown and in the failsafe, and the ceiling while grid charging. The limit is never left at 0 A. |
| `grid_charge_switch_entity` | `switch.deye8k_battery_grid_charging` | Switch entity that enables/disables grid charging |
| `battery_discharging_current_entity` | `number.deye8k_battery_max_discharging_current` | Entity for setting the discharge current limit (A) |
| `battery_max_charge_current_a` | `185` | Maximum charge current (A). Automatically clamped to 0–350 A. |
| `battery_max_discharge_current_a` | `185` | Maximum discharge current (A). Automatically clamped to 0–350 A. |
| `inverter_write_stuck_threshold_sec` | `1800` | Once a channel has been continuously unconfirmed for longer than this, `sensor.miniems_inverter_write_status` reports "error" instead of "warning" (since v2.0.7). **Provisional value** — see the note below. |

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

    Since v2.0.7 this status is also available as `sensor.miniems_inverter_write_status` (`ok`/`warning`/`error`, raw numbers as attributes) — see [Calculations](../technical/calculations.md). `inverter_write_stuck_threshold_sec` decides when "warning" becomes "error"; the default is explicitly provisional until enough real confirmation durations have been collected.

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

Tomorrow's load forecast looks for days in the last 60 days whose outdoor
**daily mean** temperature is within ±4 °C of tomorrow's forecast daily mean and
takes their median load (at least 3 days). With fewer, the median of the last
14 days is used. Only without any measured day do fixed rules apply, based on
the daily forecast:

| Condition | Predicted Load |
|---|---|
| Low < 0 °C and high < 0 °C | 30 kWh |
| Low < 0 °C and high < 10 °C | 20 kWh |
| Low > 0 °C and high < 15 °C | 10 kWh |

Set `outdoor_temp_entity` (section below) for the temperature match – otherwise
the daily mean is built from the hourly forecast.

!!! tip "Empty history after a reinstall"
    With the **fill missing history** switch (top, under *Control Mode*,
    `history_backfill_enabled`) miniEMS takes missing days of the last 60 days
    from Home Assistant's long-term statistics – daily load from the lifetime
    counter, outdoor daily mean temperature. Measured days are never
    overwritten. Details: [Calculations](../technical/calculations.md).

---

## Hourly Load Forecast & Bridging Plan (since v3.3.0)

Replaces the grid-charge rule of thumb "remaining PV forecast vs. free capacity" with a calculation in 30-minute slots: **how much must be charged in the current cheap tariff window to bridge the next expensive one – and does it pay?** It uses the hourly household load (incl. heat pump, driven by outdoor temperature), Solcast's 30-minute forecast and the tariff. Details: [grid charging decision](../../technical/netzladen-entscheidung.md) (German).

The hourly history is **always** recorded, even with the switch off, so history is already there when you enable it.

| Setting | Default | Description |
|---|---|---|
| `load_forecast_enabled` | `false` | Master switch. Off = grid charging decides as before. |
| `load_forecast_min_days` | `14` | Days of hourly history before the plan decides. Until then it is computed and shown, the old logic decides. 1–60. |
| `heat_pump_power_entity` | empty | Heat pump power in W, optional. Several comma-separated entities are summed (e.g. the three phases of a Shelly 3EM). Empty = temperature dependence learned from the total load. |
| `outdoor_temp_entity` | empty | Outdoor temperature in °C, optional. Empty = current hour from `weather_entity`'s hourly forecast. |
| `history_backfill_enabled` | `false` | Switch at the top of Settings (“Control Mode”), no restart needed: switching it on runs the import at once. At every start, fill missing days of the last 60 days (daily load from `load_consumption_total_entity`, outdoor daily mean from `outdoor_temp_entity`) from HA's long-term statistics. Gaps only – measured days and today are never overwritten, a rerun changes nothing. |
| `heating_limit_temp_c` | `15.0` | Heating limit: above it the heat pump does not heat. Hot water is learned as a flat share. 5–25. |
| `pv_forecast_scenario` | `blend` | Solcast scenario: `estimate`, `estimate10` (pessimistic) or `blend`. |
| `pv_forecast_blend_weight` | `0.7` | For `blend`: weight of `estimate`, rest `estimate10`. Since v4.2.2 per slot by Solcast's certainty: when `estimate10 ÷ estimate90` is below 0.5, the weight of `estimate` shrinks in the same proportion (at 0.18 from 70 % to 25 %). |
| `tariff_source` | `auto` | Tariff calendar: `auto` (dated `rates[]` if present, else `timeslots[]`), `timeslots` or `rates`. |

---

## Decision Logic v4 (since v4.0.0)

Phase A of the [overall plan](../../roadmap/gesamtplan.md) (German). **Every building block has its own switch, all are off**: with the defaults miniEMS decides exactly like v3.7.2. Switch them on one at a time, watch with `battery_control_simulation` first, then go live. What a block decides is in the log and in `/api/status` (`price_classes`, `energy_value`, `battery_plan`, `learning`).

### Prices and battery

| Setting | Default | Description |
|---|---|---|
| `price_classes_enabled` | `false` | **A1** – "cheap" relative to the day instead of a fixed threshold, for grid charging, `price_tier` and the hot-water planner's low-price window. Cost statistics by tier keep the fixed thresholds. |
| `price_cheap_hours` | `6` | This many cheapest hours of the calendar day are "low". With Octopus (8 h NIEDRIG) 6 gives exactly NIEDRIG. 1–20. |
| `price_hysteresis_min_eur` / `_max_eur` | `0.005` / `0.02` | Bounds of the price hysteresis (15 % of the day's spread); used as the band for day-ahead prices. |
| `price_flat_spread_eur` | `0.03` | A day whose prices lie closer together has nothing "low" – no optimisation. |
| `load_profile_enabled` | `false` | **A2** – remaining load today and tomorrow from the hourly model instead of the day median. V2 defers a charge only when the battery is not needed before the later window. |
| `soc_aware_gate_enabled` | `false` | **A3** – grid-charge only when the bought kWh replaces a dearer hour the battery could not cover otherwise, and only that amount. Replaces the historical discharge tariff. |
| `battery_wear_cost_eur_kwh` | `0.0` | Wear per discharged kWh: battery price ÷ (cycles × usable kWh). |
| `discharge_hold_enabled` | `false` | **A4** – mode *Hold for Peak*: no discharge while the energy is needed for dearer hours later. PV keeps charging. Never replaces a grid charge. |
| `discharge_hold_min_spread_eur` | `0.03` | Hold only for hours at least this much dearer than now. |
| `battery_plan_enabled` | `false` | **A6** – the battery day plan decides (slot 0). Off = it is still computed and shown (shadow mode); differences to the real decision are logged. |
| `battery_plan_style` | `careful` | `careful` (PV × 0.85, load × 1.1), `balanced`, `bold`. |
| `battery_plan_switch_cost_eur` | `0.01` | Cost per change of action in the plan – keeps it calm. |
| `battery_learned_values_enabled` | `false` | **A7a** – use the learned battery efficiencies. Always learned (`learning.battery`). |
| `pv_correction_enabled` | `false` | **A7b** – correct the PV forecast per hour of day (0.3–2.0). |
| `plan_feedback_enabled` | `false` | **A7c** – correct load (0.8–1.25) and PV (0.7–1.2) by the systematic deviation. |
| `day_ahead_enabled` | `false` | **A9** – for exchange prices: a band instead of equality, short cheap outliers do not end the bridging, V1 over the whole cheap run, mean price per slot. No effect on time-of-use tariffs. |
| `price_unit` | `auto` | Unit of a dated price list: `auto` (decided once for the whole list), `eur`, `ct`, `eur_mwh`. An explicit value also applies to the price sensor. |
| `battery_full_charge_days` | `0` | **A11** – charge to `battery_max_soc` at least every n days (cell balancing, SoC calibration). 0 = off, suggested 14. |

### Negative prices and feed-in (A5)

| Setting | Default | Description |
|---|---|---|
| `negative_price_enabled` | `false` | At a price ≤ 0 or unpaid feed-in: store PV instead of exporting, grid-charge without the gate (leaving room for the remaining PV), hot water up to `hw_max_temp_c` with a full battery, optionally export limited to 0 W. |
| `feed_in_price_entity` | empty | Sensor with the current feed-in price; ≤ 0 means exporting earns nothing. |
| `feed_in_limit_w` | `0` | Feed-in limit at the grid connection in W. Above it there is no PV hold. 0 = none. |
| `grid_export_limit_entity` | empty | `number` entity of the inverter's export limit (W). Only together with `grid_export_limit_normal_w`. |
| `grid_export_limit_normal_w` | `0` | Value the limit is reset to. |
| `price_is_spot` | `false` | The price sensor delivers raw exchange prices: effective = (spot + `price_fee_eur_kwh`) × (1 + `price_vat_pct` %). |
| `price_fee_eur_kwh` | `0.0` | Grid fees and levies in €/kWh. |
| `price_vat_pct` | `0.0` | VAT in %. |

### Safety (A10)

| Setting | Default | Description |
|---|---|---|
| `failsafe_timeout_min` | `15` | When the add-on has not reported for this long, the **HA integration** switches grid charging off, sets charge/discharge current to maximum, the boiler setpoint to idle and the export limit back, fires `miniems_alert` and shows a repair notice. Only while a control is live. 0 = off. |
| `write_budget_per_hour` | `0` | At most this many changes of the stretched charge current (V1) per hour. Mode changes are not limited. 0 = unlimited. |

### Hot water (A8, A11)

| Setting | Default | Description |
|---|---|---|
| `hw_start_cost_enabled` | `false` | **A8** – add the learned heat lost per start to every planned charge. |
| `hw_thermal_energy_entity` | empty | The heat pump's lifetime hot-water heat counter in kWh. Nothing is learned without it. |
| `hw_hygiene_enabled` | `false` | **A11** – hygiene charge, in the cheapest slot of the next 24 h. |
| `hw_hygiene_temp_c` | `60.0` | Target of the hygiene charge. 50–70. |
| `hw_hygiene_interval_days` | `7` | Due when the boiler has not reached the temperature for this long. |

---

## Several Consumers (since v4.1.0)

Phase B of the [overall plan](../../roadmap/gesamtplan.md) (German): main fuse, EV, heating.
**Every switch is off**: with the defaults miniEMS decides exactly like v4.0.0. EV
and heating each have a control switch and a simulation switch, like battery and
hot water. The "Simulation" switch at the top of the settings page sets all four.
What a block decides is in the log and in `/api/status` (`fuse`, `ev`,
`surplus_share`, `heating`, `best_start`), and in the sensors
`sensor.miniems_fuse_headroom`, `sensor.miniems_ev_mode`,
`sensor.miniems_ev_plan_kwh`, `sensor.miniems_heating_offset` and
`sensor.miniems_best_start_<device>`.

!!! warning "Order of going live"
    No EV live without the fuse guard (B1) and a measured rated current. Check the
    phase assignment of the current sensors by measurement first: switch on a known
    single-phase load and see which phase rises.

### Main fuse and §14a (B1)

| Setting | Default | Description |
|---|---|---|
| `fuse_guard_enabled` | `false` | Throttles grid charging and the wallbox when a phase gets close to the fuse. The guard only reduces, it never starts anything. |
| `fuse_rated_a` | `0` | Rated current of the main fuse in A. 0 = unknown, the guard stays off (warning). |
| `fuse_work_limit_pct` | `90` | Working limit in % of the rated current. Throttles as soon as the thermally filtered current (I², τ = 60 s) exceeds it. A single reading above 1.2 × rated throttles at once. No increase for 20 s after a throttle. |
| `phase_current_entities` | Deye grid CTs | Three current sensors L1, L2, L3 in A, comma-separated. Defaults to `sensor.deye8k_external_ct1..3_current`. |
| `phase_power_entities` | Deye grid power per phase | Three power sensors in W (+ = import). Recognises exporting phases, which never cause a throttle. Optional. |
| `phase_voltage_v` | `230` | Phase voltage for A ↔ W. |
| `fuse_stale_sec` | `60` | Older phase readings count as missing. The guard then assumes the learned base current of the phase plus 6 A, never 0 A. |
| `fuse_poll_sec` | `10` | Check interval while grid charging or the wallbox runs. |
| `grid_dimming_entity` | empty | `binary_sensor` of the §14a EnWG control box (on = dimmed). |
| `grid_dimming_limit_w` | `4200` | Guaranteed minimum power for all controllable loads together while dimmed. With several devices, enter the value from the Federal Network Agency's ruling or your grid operator. |

When grid charging and the EV compete for the reserve, the task with the tightest
deadline comes first. A deficit is taken from the load with the latest deadline.

### Several consumers (B2)

| Setting | Default | Description |
|---|---|---|
| `charge_priority` | `battery` | Who gets the PV surplus first: `battery` (battery up to `battery_priority_soc_pct`, then EV, hot water, rest of the battery, export) or `ev`. |
| `battery_priority_soc_pct` | `50` | Below this SoC the battery charges before the EV. Above it, it yields its PV charge to the car. |
| `ev_battery_mode` | `block` | While the car charges: `block` = the battery does not discharge (it would feed the car, as the Deye covers every load behind its grid CT). Mode *Hold for Peak*, reason "ev charging". `allow` = no coupling. Only with `ev_control_enabled`. |

The EV's planned charge power is load in the battery day plan (A6) and the
bridging plan.

### EV and wallbox (B3–B5)

The wallbox is addressed through HA entities, not a fixed vendor. miniEMS reads as
soon as `ev_charge_power_entity` or `ev_plugged_entity` is set. With
`ev_control_enabled` it decides; in simulation it only logs.

| Setting | Default | Description |
|---|---|---|
| `ev_control_enabled` | `false` | Control the EV. Off = read and plan only. |
| `ev_control_simulation` | `true` | Log only (`[SIM]`), write nothing. |
| `ev_charge_power_entity` | empty | Charge power (W or kW). |
| `ev_plugged_entity` | empty | Plugged in (`binary_sensor` or text such as `connected`, `charging`). Without it, "plugged" means power flows. |
| `ev_status_entity` | empty | Wallbox status. If it shows a value from `ev_status_waiting_states` for more than 2 min although charging is enabled, miniEMS tries `switch.turn_on` once every 10 min ("charge hangs"). |
| `ev_status_waiting_states` | `waiting,waiting_for_vehicle,ready` | Texts meaning "waiting for the car". |
| `ev_enable_entity` | empty | Charge enable switch. |
| `ev_current_entity` | empty | Charge current limit (`number`, A). |
| `ev_phases_entity` | empty | Phase switch (`select` with options `1`/`3`, or `number`). Empty = no switching. |
| `ev_soc_entity` | empty | The car's SoC. Without it, planning assumes 20 % and subtracts the energy charged in this session. |
| `ev_mode_entity` | empty | Override as `input_select` with the options **Auto / Jetzt / Nur PV / Stopp** (English words work too). Empty = Auto. |
| `ev_target_soc_entity` | empty | Target SoC as `input_number`. |
| `ev_departure_entity` | empty | Departure as `input_datetime` (date and time, or time only). |
| `ev_target_soc_pct` | `80` | Target SoC without a helper. |
| `ev_departure_time` | `07:00` | Departure without a helper. With a 100 % target miniEMS plans one hour earlier. |
| `ev_morning_soc_pct` | `50` | Minimum SoC by the morning time (sub-target before the rest). 0 = none. |
| `ev_morning_time` | `06:00` | Time of the sub-target. |
| `ev_capacity_kwh` | `50` | The car's battery capacity. Need = (target − now) × capacity × 1.12. |
| `ev_max_charge_kw` | `11` | The car's charge power at full current. W per A is derived from it, so a car with a two-phase on-board charger is planned correctly. |
| `ev_max_current_a` / `ev_min_current_a` | `16` / `6` | Current range of the wallbox. |
| `ev_phases_max` | `3` | 1 or 3. |
| `ev_pause_mode` | `zero_current` | Pause at 0 A (the session stays) or `switch` (enable off) if the wallbox does not accept 0 A. |

**Modes:**

- **Auto:** planning (B4) puts the need into the cheapest slots before the
  deadline, within the power the fuse leaves after the forecast base load. Low
  currents get a surcharge (€0.10 × (P_max ÷ P − 1)) because they charge less
  efficiently. If time runs short, "target not reachable" shows in the warning
  banner. When the PV surplus allows more, the car charges from PV.
- **Jetzt (now):** full current, limited only by the fuse and §14a.
- **Nur PV (PV only):** PV surplus in real time (B5). Starts single-phase at the
  minimum current, three-phase from a computed 18 A, back below 5.5 A, then a
  15 min lock. Readjusts every 5 min, every minute while the car buys expensive
  grid power. Runs at least 30 min, then pauses.
- **Stopp (stop):** enable off.

On shutdown, when leaving LIVE and via the failsafe (A10) the wallbox is enabled
at full current: that may cost money but never leaves the car uncharged.

### Heating (B6, B7)

Only the heat pump's **flow-line offset** is controlled (Lambda: HC register 50,
−10 … +10 K). miniEMS sets it to 0 K at start, at shutdown and at handover.

| Setting | Default | Description |
|---|---|---|
| `heating_control_enabled` | `false` | Control the heating. |
| `heating_control_simulation` | `true` | Log only. |
| `heating_offset_entity` | Lambda HC1 | `number` of the flow-line offset. |
| `heating_offset_up_k` / `heating_offset_down_k` | `2` / `2` | Raise in cheap, lower in expensive hours (price classes from A1, with hysteresis). Before an expensive hour within 6 h at least +1 K (pre-load). |
| `heating_cop_drop_pct_per_k` | `2.5` | COP loss per K higher flow temperature. Raised only when `price now ÷ COP_raised < peak price ÷ COP_normal`. |
| `heating_sun_kwh` | `10` | From this PV forecast over the next 12 h, pre-loading is halved. |
| `heating_compressor_max_w` | `0` | Electrical power of the compressor alone. If the heat pump draws more than 1.3 × for 10 min, the heating rod probably runs: warning, lowering halved for the day. Needs `heat_pump_power_entity`. 0 = off. |
| `heating_surplus_entity` | empty | Optional: report the PV surplus to the heat pump ourselves (`number`, W; Lambda register 102). **Only with the Lambda's own `pv_surplus` option switched off**, otherwise two regulators fight over one heat pump. |
| `heating_room_temp_entity` | Lambda HC1 room temperature | Indoor temperature. |
| `heating_comfort_min_c` | `0` | Lower bound of the room temperature. Close to it (0.3 K) no lowering; 30 min in a row below it: raise. 0 = no room rules. |
| `heating_room_setpoint_c` | `21` | Room temperature the heating curve holds (for the planner). |
| `heating_room_control_enabled` | `false` | **B7** – the pre-heat planner decides instead of the price class. Only with a room model that passes the look-back (below). |

**Protection rules, always on:** no offset above the heating limit
(`heating_limit_temp_c`). At most 3 h lowered in a row, then 1 h normal. Below
−5 °C outdoors at most 1 K lowering, below −15 °C none. After every change a lock
of 60 min (raise) or 45 min (lower); the lock survives a restart.

**Room model (B7):** recorded per hour: room temperature, share of heating and
offset (`hourly_stats`). Learned: cooling rate `a · (T_in − T_out) − b · PV_3h` and
heating rate `c + d · T_out`, from 48 cooling and 24 heating hours. Before use,
miniEMS predicts every other hour (look-back); the model is ready only below
0.3 K/h mean error. Status and error are in `/api/status` under `heating.model`.

### Start advice (B8)

| Setting | Default | Description |
|---|---|---|
| `flex_devices` | empty | Appliances as `Name:kWh:hours`, comma-separated, e.g. `Waschmaschine:1.2:2, Spülmaschine:1.0:3`. Decimal point, no comma. |

Each appliance gets `sensor.miniems_best_start_<name>` (timestamp) with end, cost,
cost if started now and saving as attributes. PV surplus counts at the feed-in
tariff. The sensor steers nothing.

### Hot-water setpoint as an entity (D1)

| Setting | Default | Description |
|---|---|---|
| `hw_setpoint_entity` | empty | Boiler setpoint as a `number` entity instead of `hw_write_service`/`hw_write_data`, for heat pumps whose integration offers the setpoint directly. Empty = service template as before (Lambda). |

---

## Solcast PV Forecast

**Required.** [Solcast](https://solcast.com/) provides rooftop PV forecasts. Install the Solcast HA integration, enable the **detailed forecast** option (attribute `detailedForecast` with 30-minute values on the "forecast today/tomorrow" sensors) and configure the entities here.

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

## Device Detection (Preview)

The **Devices** tab in the dashboard shows which devices and entities Home Assistant knows
about (device registry and energy dashboard) and how miniEMS *would* bind each role (PV
power, battery SoC, grid-charge switch, …) from that data. This resolution does not yet
feed the actual control path — the 30 entity fields above remain the only source that is
really used. Details on the planned full resolution:
[Roadmap "Device Profiles"](../../technical/geraeteprofile.md).

Two fields prepare for that, also only editable via **config.json**:

| Setting | Default | Description |
|---|---|---|
| `entity_overrides` | `{}` | Manually binds a single role to an entity, `"<class>.<role>": "entity_id"` (e.g. `{"inverter.pv_power": "sensor.my_pv"}`). Beats any automatically detected binding in the Devices resolution preview — including one derived from the 30 entity fields above. |
| `device_detection_enabled` | `false` on an update, `true` on a genuinely new install | Doesn't switch anything in the control path yet — preparation for a later version where the resolution actually drives runtime config. |

---

## Raw File Editors (config.json / options.json)

The **config.json** and **options.json** tabs in the dashboard let you edit and save the entire configuration directly as JSON — useful for fields that don't (yet) have their own form field on the **Settings** page (see the two sections above).

| Tab | File | Purpose |
|---|---|---|
| **config.json** | `/data/config.json` | Persistent values written by miniEMS itself. Takes precedence over `options.json` whenever a value differs from the built-in default. Recommended for durable changes. |
| **options.json** | `/data/options.json` | Managed by the HA Supervisor. Can be overwritten by an addon reconfiguration or schema update — changes made here are not guaranteed to persist. |

Both editors validate the JSON before saving (use the "Reformat JSON" button to check/pretty-print) and restart the addon automatically after saving so the new configuration takes effect.
