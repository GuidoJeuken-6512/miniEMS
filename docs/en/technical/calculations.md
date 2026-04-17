---
revision_date: 2026-04-07
---

# Calculations

## EMS Mode Decision (`EMSController._determine_mode`)

The controller evaluates four mutually-exclusive modes in priority order:

```
1. BATTERY_PROTECTION  — battery_soc < battery_min_soc
2. PV_CHARGING         — (pv_w − load_w) > pv_surplus_threshold_w  AND  soc known AND soc < max_soc
3. GRID_CHARGING       — price < cheap_rate_threshold_eur
                         AND  soc known AND soc < max_soc
                         AND  bat_kwh_free > solcast_remaining_today_kwh
                              (falls back to consumption model if Solcast unavailable)
4. IDLE                — none of the above, OR SoC sensor unavailable
```

!!! note "SoC sensor unavailable"
    If `battery_soc_entity` returns no value, both PV Charging and Grid Charging
    are disabled and the system stays in **IDLE** mode. This is a safety measure
    to prevent uncontrolled charging when the battery state of charge is unknown.

### PV Surplus

```
surplus_w = pv_power_w - load_power_w
```

If `surplus_w > pv_surplus_threshold_w` (default 200 W) and the battery is not
full (`soc < battery_max_soc`), the system enters **PV Charging** mode.

### Grid-Charge Decision

```python
bat_kwh_free = (max_soc - soc) / 100 * capacity_kwh

# Primary path: Solcast available
should_grid_charge = bat_kwh_free > solcast_remaining_today_kwh

# Fallback: no Solcast
should_grid_charge = (predicted_load > 0) AND (useable_kwh + predicted_pv < predicted_load)
```

The Solcast path is preferred because it accounts for actual solar irradiance
forecasts. The fallback uses the temperature-based consumption model.

---

## Battery State (`BatteryModel`)

```
free_to_charge_kwh = max(0,  (max_soc − soc) / 100  × capacity_kwh)
useable_kwh        = max(0,  (soc − min_soc) / 100  × capacity_kwh)
```

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

If no prior value exists for a sensor, 0 W is used as the fallback.

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

### Total Load Cost (`today_load_cost_eur`)

Hypothetical cost if the **entire** house load had been purchased from the
grid at the current spot price, regardless of actual source (PV, battery,
grid):

```
load_kwh       = (load_power_w / 1000) × hours
load_total_kwh += load_kwh
load_cost_eur  += load_kwh × price_eur_kwh
```

Always ≥ `today_grid_cost_eur` because PV and battery reduce actual grid
purchases.

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
covered by excess PV — therefore it must have come from the grid.

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

### Derived Metrics (computed in `ems_controller.py`)

These are calculated once per tick from the accumulated values above and
added to the status dict.

| Entity                           | Formula                                        | Meaning                                                                                |
| -------------------------------- | ---------------------------------------------- | -------------------------------------------------------------------------------------- |
| `today_cost_without_grid_charge` | `max(0, grid_cost_eur − grid_charge_cost_eur)` | What the grid bill would have been if the battery had never been charged from the grid |
| `today_cost_fix_price_tariff`    | `load_total_kwh × fix_price`                   | What today's load would cost at the fixed reference tariff (default 0.30 €/kWh)        |

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
| -------------------------------------------------------------------- | -------------------------- |
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

### Predicted Load (`predicted_load_kwh`)

```
If weather_entity configured AND forecast available:
  target_temp   = tomorrow's max temperature (from HA forecast)
  similar_days  = days in last 60 d with |avg_temp − target| ≤ 4 °C
  If len(similar_days) ≥ 3:
    predicted_load = median(load_total_kwh of similar_days)  → source: "historical"
  Else:
    → temperature fallback rules (see below)                 → source: "fallback"
Else:
  → temperature fallback rules                               → source: "fallback"
No history:
  predicted_load = 0.0 kWh                                   → source: "fallback"
```

#### Temperature Fallback Rules

| Condition                              | Predicted Load |
| -------------------------------------- | -------------- |
| Night temp < 0 °C and day temp < 0 °C  | 30 kWh         |
| Night temp < 0 °C and day temp < 10 °C | 20 kWh         |
| Night temp > 0 °C and day temp < 15 °C | 10 kWh         |
| Otherwise                              | 5 kWh          |

### Predicted PV Yield (`predicted_pv_kwh`)

Internal estimate used only when Solcast is unavailable.

```
peaks    = [peak_pv_w | last 14 days, peak_pv_w > 100 W]
p75      = 75th percentile(peaks)   (conservative estimate)

With forecast:
  clear_frac  = avg(1 − cloud_coverage / 100) across forecast slots
  daylight_h  = astronomical day length for HA latitude + current month
  pv_factor   = clear_frac × min(1.0, daylight_h / 12.0)

Without forecast:
  pv_factor   = 0.5          (neutral assumption)
  daylight_h  = approximation for 51°N + current month

predicted_pv = max(0, (p75 / 1000) × pv_factor × daylight_h)
```

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
