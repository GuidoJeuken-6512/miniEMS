---
revision_date: 2026-08-15
---

# Calculations

## EMS Mode Decision (`EMSController._decide` / `_commit`)

The controller evaluates five mutually-exclusive modes, in this priority order, on every tick:

```
1. IDLE               — battery_soc_entity missing OR battery_power_entity stale (sensor_max_age_sec)
2. PROTECT_BATTERY     — soc < battery_min_soc
                         OR (previously PROTECT_BATTERY AND soc < battery_min_soc + battery_soc_hysteresis_pct)
3. IDLE                — soc ≥ battery_max_soc ("battery full")
4. EXPORT_SURPLUS       — (pv_w − load_w) > pv_surplus_threshold_w  AND  export hold active (see below)
   PV_CHARGING          — (pv_w − load_w) > pv_surplus_threshold_w  AND  export hold NOT active
5. GRID_CHARGING        — price cheap AND grid-charge conditions met (see below)
6. IDLE                — none of the above ("no action")
```

Every condition is evaluated **fail closed**: if a required sensor value is missing or stale, the controller takes the safe path (no grid charging, no withholding of PV charging) instead of guessing.

!!! note "SoC sensor unavailable"
    If `battery_soc_entity` returns no value, the controller immediately and without delay (`urgent`) switches to **IDLE** and hands control back to the inverter's own self-use logic. This is the central safety measure against uncontrolled charging when the battery state of charge is unknown.

    The staleness check here does **not** run on `battery_soc_entity` itself — it runs on `battery_power_entity` (default `sensor_max_age_sec` = 300 s). Reason: SoC is a coarse percentage that can legitimately stay on the exact same value for hours, or — in winter with grid charging off — for days; a timeout on "time since the SoC last changed" would eventually be wrong for every possible value. `battery_power_entity` comes from the same BMS/inverter connection and fluctuates continuously as long as that connection is alive, so its freshness proves the SoC reading is current, not merely that it happened to change recently.

### PV Surplus

```
surplus_w = pv_power_w − load_power_w
```

Only computed when **both** power sensors report a current value (no older than `sensor_max_age_sec`); otherwise `surplus_w` is treated as unknown and the controller proceeds to step 5 (grid charging). If `surplus_w > pv_surplus_threshold_w` (default 200 W), the export hold (next section) decides whether the mode becomes **PV Charging** or **Export Surplus**.

### Grid-Friendly Export Hold (`_should_hold_pv_charge`, mode `EXPORT_SURPLUS`)

Optional strategy (`pv_export_priority_enabled`, off by default): exports PV surplus to the grid instead of charging the battery immediately, for as long as the Solcast remaining-today forecast stays above what the battery still needs. Every one of the following must hold for the hold (`hold = True`, mode `EXPORT_SURPLUS`) to apply instead of charging — **any** failed condition immediately triggers `PV_CHARGING`:

```
1. pv_export_priority_enabled == true
2. now.hour < pv_charge_backstop_hour              (default 14:00 — always charge after this)
3. bat_soc ≥ pv_export_min_soc_pct                 (default 30 % — always charge below this)
4. bat_kwh_free > 0.05 kWh                          (there's practically still room)
5. solcast_remaining_today_kwh available AND not stale (forecast_max_age_sec)

target    = bat_kwh_free × pv_charge_margin_factor          # default factor 1.2
hyst      = clamp(pv_charge_hysteresis_frac, 0.0, 0.5)       # default 0.10 (±10 %)
threshold = target × (1 − hyst)   if currently EXPORT_SURPLUS   # easier to stay in the hold
          = target × (1 + hyst)   otherwise                     # harder to enter it

hold = solcast_remaining_today_kwh > threshold
```

The threshold is deliberately asymmetric (hysteresis): **entering** the export hold requires the remaining forecast to be clearly above the need; **leaving** it only requires a smaller drop. This keeps the mode from flapping around the trigger point.

While the export hold is active, `InverterController.apply_mode()` sets the charge current to `export_hold_charge_current_a` (default 0 A = charging blocked entirely) and leaves the discharge current at maximum, so a passing cloud is still covered from the battery instead of by importing from the grid.

### Grid-Charge Decision (`_should_grid_charge`, mode `GRID_CHARGING`)

```
1. electricity_price_entity available AND not stale (price_max_age_sec)
2. price_eur_kwh < cheap_rate_threshold_eur
3. bat_kwh_free > grid_charge_min_free_kwh          (default 1.0 kWh — otherwise not worth it)

If solcast_remaining_today_kwh is available (not stale) AND > grid_charge_min_free_kwh:
    should_grid_charge = bat_kwh_free > solcast_remaining_today_kwh × pv_charge_margin_factor
                                          + grid_charge_min_free_kwh

Otherwise (today's forecast missing, stale, or simply spent — e.g. in the evening):
    # Since v2.0.1: check tomorrow's forecast before falling back to a
    # blind time-of-day decision — if it alone covers the battery's need,
    # buying grid energy tonight isn't worth it.
    If solcast_tomorrow_kwh is available (not stale):
        If bat_kwh_free ≤ solcast_tomorrow_kwh × pv_charge_margin_factor + grid_charge_min_free_kwh:
            should_grid_charge = False   # tomorrow's sun will refill it

    # Neither today nor tomorrow has a usable value:
    # "No more sun can arrive today, and nothing reliable is known about
    # tomorrow either" — only charge inside the configured dark window
    should_grid_charge = grid_charge_dark_start_hour ≤ now.hour < grid_charge_dark_end_hour
                          (default 21:00–06:00; allowed to wrap past midnight)
```

!!! note "Why this fallback was needed"
    `solcast_remaining_today_kwh` falls back to `None`/0 for two different reasons: **legitimately** in the evening, once no more sun is expected for the rest of the day, and **incorrectly** when Solcast (depending on your plan) goes several hours without a new value and `forecast_max_age_sec` (default 8 h since v2.0.1, previously 3 h) is exceeded — even though the last known value is usually still correct after the midnight rollover. Both cases used to fall straight into the dark window, even when `solcast_tomorrow_entity` already showed a forecast more than large enough for the next day. The check only ever narrows the decision (it can prevent grid charging, never trigger it in addition) and leaves every existing safety gate (SoC floor, `PROTECT_BATTERY`) untouched.

!!! warning "No longer tied to the internal consumption model"
    Earlier miniEMS versions used the internal consumption forecast (`ConsumptionModel`, section below) as a fallback for the grid-charge decision whenever Solcast was unavailable. That is no longer the case since the grid-friendly PV strategy (Phase 7): with no usable Solcast forecast (today *or* tomorrow), the decision relies **exclusively** on the dark window. `ConsumptionModel.predicted_pv_kwh` and `.predicted_load_kwh` currently feed into **no** control decision at all — they are dashboard-display values only (see "Consumption & PV Prediction" further down this page).

### Inverter Write Confirmation (`InverterController`, since v2.0.1)

A service call Home Assistant accepts (HTTP 200) is not proof the inverter applied it — some Deye/Solarman bridges only confirm a written value (charge current, discharge current, grid-charge switch) on their next poll, observed to lag by up to ~25 minutes.

```
per channel (charge current, discharge current, grid-charge switch), independently:
  when the target changes:  mark the channel "unconfirmed"
  while unconfirmed:
      if the real HA state == target:  mark the channel "confirmed"
      else, if ≥ INVERTER_WRITE_CONFIRM_TIMEOUT_SEC (30 s) since the last send:
          resend the service call
```

`write_unconfirmed` (0–3) is a live count of how many of the three channels are currently unconfirmed, and drops back to 0 once the real state catches up — separate from `write_errors`, which only counts outright HTTP/connection failures. Both surface as warnings in the dashboard banner.

### Battery Protection Hysteresis

```
Enter:  soc < battery_min_soc                                     → PROTECT_BATTERY (immediate)
Leave:  only once soc ≥ battery_min_soc + battery_soc_hysteresis_pct   (default 2 %)
```

Without this deadband, a SoC hovering right around `battery_min_soc` would flip the mode back and forth between `PROTECT_BATTERY` and something else on every tick.

### Mode Debouncing (`_commit`, `mode_dwell_sec`)

A newly proposed mode is not applied immediately — it must be requested continuously for a while first, unless the decision is marked **urgent** (SoC protection, a missing/stale SoC sensor, or an export hold released by a safety guard):

```
If decision.mode == current mode:
    no change, pending reset

If decision.urgent OR mode_dwell_sec ≤ 0:
    apply immediately

Otherwise:
    If decision.mode ≠ previously proposed mode:
        new proposal, start timer at now
    waited = now − pending_since
    If waited < mode_dwell_sec:
        keep current mode (still waiting)
    Else:
        apply the mode
```

Default `mode_dwell_sec` = 300 s. This prevents rapid switching on short price or PV fluctuations, without delaying real emergencies (empty battery, sensor failure).

---

## Battery State (`BatteryModel`)

```
free_to_charge_kwh = max(0,  (max_soc − soc) / 100  × capacity_kwh)
useable_kwh        = max(0,  (soc − min_soc) / 100  × capacity_kwh)
```

`capacity_kwh` defaults to `battery_capacity_kwh` (fixed config value), but is replaced on every tick by the live sensor `battery_capacity_entity` if configured **and** it reports a plausible value:

```
plausible := 0.5 × battery_capacity_kwh ≤ sensor value ≤ 2.0 × battery_capacity_kwh
```

This plausibility band prevents a mis-scaled sensor (e.g. an Ah reading instead of kWh) from feeding an absurd capacity into the charge/decision logic — outside the band, the fixed config value is used instead.

---

## Cost & Savings — Complete Reference (`CostOptimizer`)

`CostOptimizer.record_tick()` is called once per EMS tick (default 30 s).
All accumulators are keyed by calendar date and flushed to SQLite after every
tick. Values are stored at 6 decimal-place precision. On restart,
today's accumulators are restored from SQLite before the first tick.

### Prerequisite: Spike Filtering

Every power reading is validated by `SensorValidator` before use.
A sample is rejected (replaced by the last accepted value) when:

```
|delta| > 500 W  AND  |delta| / previous_value > 50 %
```

If no prior value exists for a sensor, the first reading is always accepted.

### Interval Duration

```
hours = update_interval_sec / 3600    # default: 30 s → 0.008333 h
```

---

### Grid Import & Cost (`today_grid_import_kwh`, `today_grid_cost_eur`)

#### kWh — Source A (preferred)

When `grid_import_energy_entity` is configured (default: `sensor.deye8k_today_energy_import`),
the inverter's own daily import counter is used directly. The value is **set** each tick:

```
grid_import_kwh = grid_import_energy_entity   ← read directly from HA each tick
```

#### kWh — Source B (calculated fallback)

When `grid_import_energy_entity` is empty or unavailable, kWh is accumulated from
`grid_power_w` **only when `grid_power_w > 0`** (net import):

```
kwh_imported     = (grid_power_w / 1000) × hours
grid_import_kwh += kwh_imported               ← accumulated each tick
```

#### Cost — always accumulated per tick

Grid cost cannot be derived from a daily total sensor because it requires the
spot price at each individual interval. It is always accumulated from ticks:

```
grid_cost_eur += (grid_power_w / 1000) × hours × price_eur_kwh
                 (only when grid_power_w > 0)
```

`price_eur_kwh` is the current dynamic spot price from `electricity_price_entity`.

---

### PV Savings (`today_pv_savings_eur`)

Represents the electricity cost **avoided** by using PV instead of buying
from the grid. Only the portion of PV that directly covers house load is
counted — PV exported to the grid is excluded here (see Feed-in below).

```
pv_to_load_w    = clamp(pv_power_w, 0, load_power_w)
kwh_pv_used     = (pv_to_load_w / 1000) × hours
pv_used_kwh    += kwh_pv_used
pv_savings_eur += kwh_pv_used × price_eur_kwh
```

The valuation uses the **current spot price**, so cheap-rate PV contributes
less savings than peak-rate PV.

---

### Total Load Cost (`today_load_total_kwh`, `today_load_cost_eur`)

Hypothetical cost if the **entire** house load had been purchased from the
grid at the current spot price, regardless of actual source (PV, battery,
grid).

#### kWh – Source A (preferred, since v2.0.3)

If `load_consumption_entity` is configured (default:
`sensor.deye8k_today_load_consumption`), the inverter's own daily counter is
used directly. The value is **set** on every tick:

```
load_total_kwh = load_consumption_entity   ← read straight from HA every tick
```

#### kWh – Source B (calculated fallback)

If `load_consumption_entity` is empty or unavailable:

```
kwh_load        = (load_power_w / 1000) × hours
load_total_kwh += kwh_load                 ← accumulated per tick
```

#### Cost – always accumulated per tick

```
load_kwh      = (load_power_w / 1000) × hours
load_cost_eur += load_kwh × price_eur_kwh
```

Always ≥ `today_grid_cost_eur` because PV and battery reduce actual grid
purchases.

!!! note "Why Source A only exists here since v2.0.3"
    Before v2.0.3, `today_load_total_kwh` — unlike grid import and feed-in — had no
    hardware counter to fall back on and was purely tick-based. Every add-on restart left
    a gap that was never made up. Measured live: after a single restart, the value was
    already ~1.1 kWh (≈23%) below the inverter's own daily total
    (`sensor.deye8k_today_load_consumption`).

---

### Grid-to-Battery Charging Cost (`today_grid_charge_cost_eur`)

Derived from the power balance — mode-independent, no EMS state needed.
The Deye sign convention is: `battery_power_w > 0` = discharging,
`battery_power_w < 0` = charging.

```
battery_charge_w = max(0, −battery_power_w)       # positive when charging
pv_surplus_w     = max(0, pv_power_w − load_power_w)
grid_charge_w    = max(0, battery_charge_w − pv_surplus_w)

kwh_gc              = (grid_charge_w / 1000) × hours
grid_charge_kwh    += kwh_gc
grid_charge_cost_eur += kwh_gc × price_eur_kwh
```

`grid_charge_w` is the portion of battery charging power that cannot be
covered by excess PV — therefore it must have come from the grid. This
power-based estimate is the basis for `today_grid_charge_cost_eur`; it is
independent of the balance-based Scenario 2 calculation further down.

---

### Feed-in Revenue (`today_feed_in_revenue_eur`)

#### Source A — HA sensor (preferred)

When `feed_in_energy_entity` is configured (default: `sensor.deye8k_today_energy_export`),
the inverter's own daily export counter is used directly. The sensor resets at midnight
and provides a cumulative kWh total. On each tick the value is **set**, not accumulated:

```
feed_in_kwh     = feed_in_energy_entity   ← read directly from HA each tick
feed_in_revenue = feed_in_kwh × feed_in_tariff_eur_kwh
```

#### Source B — calculated fallback

When `feed_in_energy_entity` is empty or the entity is unavailable, feed-in is
derived from `grid_power_w` **only when `grid_power_w < 0`** (net export):

```
feed_in_w        = max(0, −grid_power_w)
kwh_exported     = (feed_in_w / 1000) × hours
feed_in_kwh     += kwh_exported                   ← accumulated each tick
feed_in_revenue += kwh_exported × feed_in_tariff_eur_kwh
```

Both sources use the **fixed feed-in tariff** (`feed_in_tariff_eur_kwh`,
default 0.08 €/kWh), not the spot price.

---

### Balance-Based Grid Charging, Efficiency & ROI (Scenario 2, optional)

Only active when the corresponding advanced sensors are configured — see the "Advanced Sensors for Balance-Based Cost Calculation" section in [Configuration](../user/configuration.md). Complements — does not replace — the power-based calculation above; all values are `None`/absent as long as the required inputs are missing.

#### Balance-Based Grid Charge Amount (`today_grid_charge_kwh_bilanz`)

Computed from the inverter's **daily total sensors** instead of instantaneous power — more robust against short measurement gaps:

```
grid_charge_energy = today_energy_import − today_load_consumption + today_battery_discharge
today_grid_charge_kwh_bilanz = max(0, grid_charge_energy)
```

Only computed when `battery_discharge_entity` **and** `grid_import_energy_entity` are available (`today_load_consumption` comes from the internally tracked `load_total_kwh` accumulator).

`today_grid_charge_cost_bilanz_eur` applies the power-based accumulator's average price per kWh to this amount:

```
today_grid_charge_cost_bilanz_eur = today_grid_charge_kwh_bilanz
                                     × (today_grid_charge_cost_eur / today_grid_charge_kwh)
                                     (0 when today_grid_charge_kwh == 0)
```

#### Inverter Efficiency (`today_efficiency_pct`)

```
η = (today_production_entity − today_losses_entity) / today_production_entity
    (None when today_production_entity ≤ 0 or either entity is missing)
```

#### Grid-Charge ROI (`today_grid_charge_roi_eur`)

```
grid_charge_profit = (grid_charge_energy × η × avg_discharge_tariff) − grid_charge_cost
```

```
usable_kwh = today_grid_charge_kwh_bilanz × η
saving_eur = usable_kwh × avg_discharge_tariff_eur_kwh
roi_eur    = saving_eur − today_grid_charge_cost_eur
```

Only computed when `today_grid_charge_kwh_bilanz > 0`, `η > 0`, **and** `avg_discharge_tariff_eur_kwh > 0` is configured (default `0.0` = disabled). `avg_discharge_tariff_eur_kwh` is the assumed price the discharged energy would otherwise have cost — comparing the cost of grid charging against the value of the later, otherwise more expensive, grid draw it displaces.

#### Effect on `today_cost_without_grid_charge`

When the balance-based cost estimate is available, it is preferred over the power-based one for this metric:

```
gc_cost_for_subtraction = today_grid_charge_cost_bilanz_eur if available,
                          else today_grid_charge_cost_eur
today_cost_without_grid_charge = max(0, today_grid_cost_eur − gc_cost_for_subtraction)
```

---

### Derived Metrics (computed in `ems_controller.py`)

These are calculated once per tick from the accumulated values above and
added to the status dict.

| Entity                           | Formula                                                | Meaning                                                                                          |
| --------------------------------- | ------------------------------------------------------- | -------------------------------------------------------------------------------------------------- |
| `today_cost_without_grid_charge` | `max(0, grid_cost_eur − gc_cost_for_subtraction)`      | What the grid bill would have been if the battery had never been charged from the grid (see Scenario 2 precedence above) |
| `today_cost_fix_price_tariff`    | `load_total_kwh × fix_price + daily_base_price_eur`    | What today's load would cost at the fixed reference tariff plus base fee (default 0.30 €/kWh, `daily_base_price_eur` default 0) |

---

### Weekly Aggregation (in-memory)

Computed in `CostOptimizer` from the in-memory daily buckets without a DB
query:

```
week_grid_cost_eur  = Σ grid_cost_eur[d]   for all d where (today − d).days < 7
week_pv_savings_eur = Σ pv_savings_eur[d]  for all d where (today − d).days < 7
```

The rolling window is exactly 7 calendar days (today + 6 prior days).

---

### Monthly / Yearly Aggregation (SQLite)

`CostOptimizer.summary_with_db()` queries the `daily_stats` table:

```sql
-- Month
SELECT SUM(grid_cost_eur), SUM(pv_savings_eur), SUM(load_cost_eur)
FROM daily_stats WHERE date LIKE 'YYYY-MM-%'

-- Year
SELECT SUM(grid_cost_eur), SUM(pv_savings_eur), SUM(load_cost_eur)
FROM daily_stats WHERE strftime('%Y', date) = 'YYYY'
```

| Entities                                                             | Source                     |
| ---------------------------------------------------------------------- | ----------------------------- |
| `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur` | Calendar-month SUM from DB |
| `year_grid_cost_eur`, `year_pv_savings_eur`, `year_load_cost_eur`    | Calendar-year SUM from DB  |

---

## Price Tier Consumption

Each tick, `load_kwh` is allocated to **exactly one** of three rate buckets
based on the current spot price. The three buckets always sum to
`today_load_total_kwh` for the day.

### Tier Assignment

```
if price_eur_kwh < cheap_rate_threshold_eur:
    kwh_low_rate    += load_kwh          # cheap
elif price_eur_kwh < medium_rate_threshold_eur:
    kwh_medium_rate += load_kwh          # medium
else:
    kwh_high_rate   += load_kwh          # high
```

### Tier Boundaries

| Tier     | Condition                                        | Config key                  | Default    |
| -------- | ------------------------------------------------ | --------------------------- | ---------- |
| `low`    | `price < cheap_rate_threshold_eur`               | `cheap_rate_threshold_eur`  | 0.10 €/kWh |
| `medium` | `cheap_rate ≤ price < medium_rate_threshold_eur` | `medium_rate_threshold_eur` | 0.20 €/kWh |
| `high`   | `price ≥ medium_rate_threshold_eur`              | `medium_rate_threshold_eur` | 0.20 €/kWh |

Both thresholds are configurable on the Settings page.

### Daily Entities

| Entity                  | Accumulator                            | Resets   |
| ----------------------- | -------------------------------------- | -------- |
| `today_kwh_low_rate`    | in-memory, restored from DB on restart | midnight |
| `today_kwh_medium_rate` | in-memory, restored from DB on restart | midnight |
| `today_kwh_high_rate`   | in-memory, restored from DB on restart | midnight |

### Monthly Aggregation

`month_kwh_high_rate`, `month_kwh_medium_rate`, `month_kwh_low_rate` are
SQLite SUMs of the daily `kwh_high_rate`, `kwh_medium_rate`, `kwh_low_rate`
columns, queried via `store.query_month()`.

---

## Consumption & PV Prediction (`ConsumptionModel`)

Computed once per EMS tick in `consumption_model.py`.
Data source: SQLite daily history (`store.py`) + optional HA weather forecast.

!!! info "Dashboard display only, no control effect"
    Both prediction values (`predicted_load_kwh`, `predicted_pv_kwh`) are computed
    regardless of whether Solcast is configured, and are used purely for the
    dashboard display. The actual grid-charge and export decisions rely
    exclusively on the Solcast remaining-today forecast (see the "Grid-Charge
    Decision" section further up this page) — this model no longer feeds into
    that logic at all.

### Predicted Load (`predicted_load_kwh`)

```
If weather_entity configured AND forecast available:
  target_temp   = tomorrow's forecast value the model keeps as "night temp"
                  (actually: tomorrow's daytime high from the HA forecast — the
                  naming is historical, see the source comment in consumption_model.py)
  similar_days  = days in last 60 d with |avg_temp − target| ≤ 4 °C
  If len(similar_days) ≥ 3:
    predicted_load = median(load_total_kwh of similar_days)  → source: "historical"
  Else:
    → temperature fallback rules (see below)                 → source: "fallback"
Else:
  → temperature fallback rules                               → source: "fallback"
```

#### Temperature Fallback Rules

| Condition                                                | Predicted Load                                    |
| --------------------------------------------------------- | ----------------------------------------------------- |
| Night temp < 0 °C and day temp < 0 °C                    | 30 kWh                                             |
| Night temp < 0 °C and day temp < 10 °C                   | 20 kWh                                             |
| Night temp > 0 °C and day temp < 15 °C                   | 10 kWh                                             |
| Otherwise (e.g. mild/warm days outside the three rules)  | Median of available historical days, else `0.0 kWh` |

### Predicted PV Yield (`predicted_pv_kwh`)

```
peaks    = [peak_pv_w | last 14 days, peak_pv_w > 100 W]
p75      = 75th percentile(peaks), nearest-rank: sorted list, index ⌊n × 0.75⌋

With forecast:
  clear_frac  = avg(1 − cloud_coverage / 100) across all forecast slots
  daylight_h  = astronomical day length for HA latitude + current month
  pv_factor   = clear_frac × min(1.0, daylight_h / 12.0)

Without forecast:
  pv_factor   = 0.5          (neutral assumption)
  daylight_h  = approximation for 51°N + current month

predicted_pv = max(0, (p75 / 1000) × pv_factor × daylight_h)
```

Returns `0.0 kWh` if no peak PV value above 100 W was recorded in the last 14 days (e.g. right after installation).

**Day-length formula** (`daylight_hours_approx` in `weather_client.py`):

```
day_of_year = (month − 1) × 30 + 15
decl        = 23.45° × sin(360° × (284 + day_of_year) / 365)
cos_ha      = −tan(lat) × tan(decl)   [clamped to −1 … 1]
daylight_h  = 2 × arccos(cos_ha) / 15
```

### Weather Data Cache

`WeatherClient` caches the result of `weather.get_forecasts` for **30 minutes**.
The HA latitude is read once from `http://supervisor/core/api/config` and cached.
