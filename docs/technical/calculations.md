# Calculations

## EMS Mode Decision (`EMSController._determine_mode`)

The controller evaluates four mutually-exclusive modes in priority order:

```
1. BATTERY_PROTECTION  — battery_soc < battery_min_soc
2. PV_CHARGING         — (pv_w − load_w) > pv_surplus_threshold_w  AND  soc < max_soc
3. GRID_CHARGING       — price < cheap_rate_threshold_eur
                         AND  soc < max_soc
                         AND  bat_kwh_free > solcast_remaining_today_kwh
                              (falls back to consumption model if Solcast unavailable)
4. IDLE                — none of the above
```

### PV Surplus

```
surplus_w = pv_power_w - load_power_w
```

If `surplus_w > pv_surplus_threshold_w` (default 200 W) and the battery is not
full (`soc < battery_max_soc`), the system enters **PV Charging** mode.

### Grid-Charge Decision (v1.4.0)

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

## Cost & Energy Accounting (`CostOptimizer.record_tick`)

Called every EMS tick (default 30 s). Accumulators are per calendar day and
flush to SQLite at midnight. All values are stored at 6 decimal-place precision.

### Interval Duration

```
hours = update_interval_sec / 3600
```

### Grid Import

```
kwh_imported      = (grid_power_w / 1000) × hours      # only when grid_power_w > 0
grid_cost_eur    += kwh_imported × price_eur_kwh
grid_import_kwh  += kwh_imported
```

### PV Self-Consumption

```
pv_to_load_w  = clamp(pv_power_w, 0, load_power_w)   # PV portion covering load
kwh_pv_used   = (pv_to_load_w / 1000) × hours
pv_used_kwh  += kwh_pv_used
pv_saved_eur += kwh_pv_used × price_eur_kwh
```

`pv_saved_eur` is the cost that **would** have been paid if the same energy had
been bought from the grid at the current spot price.

### Grid-to-Battery Flow (v1.4.0)

```
battery_charge_w  = max(0, −battery_power_w)   # Deye: positive = discharging
pv_surplus_w      = max(0, pv_power_w − load_power_w)
grid_charge_w     = max(0, battery_charge_w − pv_surplus_w)

kwh_grid_charge      = (grid_charge_w / 1000) × hours
grid_charge_kwh     += kwh_grid_charge
grid_charge_cost_eur += kwh_grid_charge × price_eur_kwh
```

This derivation is mode-independent — it uses only the power balance.

### Feed-in (v1.4.0)

```
feed_in_w         = max(0, −grid_power_w)       # negative grid = export
kwh_feed_in       = (feed_in_w / 1000) × hours
feed_in_kwh      += kwh_feed_in
feed_in_revenue  += kwh_feed_in × feed_in_tariff_eur_kwh
```

### Derived Metrics

| Metric | Formula |
|---|---|
| `today_cost_without_grid_charge` | `grid_cost_eur − grid_charge_cost_eur` |
| `today_cost_fix_price_tarif` | `load_total_kwh × fix_price` |
| `load_total_kwh` | `Σ (load_power_w / 1000) × hours` for today |

---

## Weekly / Monthly / Yearly Aggregation

The `store.py` module aggregates from the `daily_stats` SQLite table:

| Metric | Aggregation |
|---|---|
| `week_*` | Rolling 7-day SUM |
| `month_*` | Calendar month SUM |
| `year_*` | Calendar year SUM |

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

#### Temperature Fallback Rules (v1.4.0)

| Condition | Predicted Load |
|---|---|
| Night temp < 0 °C and day temp < 0 °C | 30 kWh |
| Night temp < 0 °C and day temp < 10 °C | 20 kWh |
| Night temp > 0 °C and day temp < 15 °C | 10 kWh |
| Otherwise | 5 kWh |

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
