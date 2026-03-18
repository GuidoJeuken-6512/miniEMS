# Calculations

## EMS Mode Decision (`EMSController._determine_mode`)

The controller evaluates four mutually-exclusive modes in priority order:

```
1. PROTECT_BATTERY  — battery_soc < battery_min_soc
2. PV_CHARGING      — (pv_w − load_w) > pv_surplus_threshold_w  AND  soc < max
3. GRID_CHARGING    — price < cheap_rate_threshold_eur            AND  soc < max
4. IDLE             — none of the above
```

### PV Surplus

```
surplus_w = pv_power_w - load_power_w
```

If `surplus_w > pv_surplus_threshold_w` (default 200 W) and battery is not
full (`soc < battery_max_soc`), the system enters **PV Charging** mode.

### Cheap Rate

```python
is_cheap_rate = (price_eur_kwh is not None) and (price_eur_kwh < cheap_rate_threshold_eur)
```

Default threshold: **0.10 €/kWh**.

---

## Cost & Energy Accounting (`CostOptimizer.record_tick`)

Called every EMS tick (default 30 s). All values accumulate per calendar day
and reset automatically at midnight (Python `date.today()`).

### Interval Duration

```
hours = update_interval_sec / 3600
```

### Grid Import

```
kwh_imported = (grid_power_w / 1000) * hours   # only when grid_power_w > 0
grid_cost_eur += kwh_imported * price_eur_kwh
grid_import_kwh += kwh_imported
```

Grid **export** (negative `grid_power_w`) is not charged and not tracked.

### PV Self-Consumption

```
pv_to_load_w = clamp(pv_power_w, 0, load_power_w)   # portion of PV covering load
kwh_pv_used  = (pv_to_load_w / 1000) * hours
pv_used_kwh  += kwh_pv_used
pv_saved_eur += kwh_pv_used * price_eur_kwh
```

`pv_saved_eur` represents the cost that **would** have been paid if the same
energy had been bought from the grid at the current spot price.

---

## Aggregated Metrics

| Metric | Formula |
|---|---|
| `today_grid_cost_eur` | Σ `kwh_imported × price` for today |
| `today_pv_saved_eur` | Σ `kwh_pv_used × price` for today |
| `today_grid_import_kwh` | Σ `kwh_imported` for today |
| `today_pv_used_kwh` | Σ `kwh_pv_used` for today |
| `week_grid_cost_eur` | Σ `today_grid_cost_eur` for last 7 days |
| `week_pv_saved_eur` | Σ `today_pv_saved_eur` for last 7 days |

> **Note**: Accumulated values are in-memory only. They reset on add-on restart.
> For long-term persistence, use HA's built-in statistics on the published
> `total_increasing` sensors.

---

## Prognose & Vorhersage (`ConsumptionModel`)

Berechnet einmal pro EMS-Tick (30 s) in `consumption_model.py`.
Datenquelle: SQLite-Tageshistorie (`store.py`) + optionaler HA-Wetterforecast.

### Prognostizierter Verbrauch (`predicted_load_kwh`)

```
Wenn weather_entity konfiguriert UND Forecast verfügbar:
  target_temp   = Tageshöchstwert von morgen (forecast.temp_tomorrow_c)
  similar_days  = Tage der letzten 60 Tage mit |avg_night_temp − target| ≤ 4 °C
  Falls |similar_days| ≥ 3:
    predicted_load = Median(load_total_kwh aller similar_days)   → Confidence "high"
  Sonst:
    predicted_load = Median(load_total_kwh der letzten 30 Tage)  → Confidence "low"
Sonst (kein Wetter):
  predicted_load = Median(load_total_kwh der letzten 30 Tage)    → Confidence "low"
Keine Historie:
  predicted_load = 0,0 kWh                                       → Confidence "none"
```

### Prognostizierter PV-Ertrag (`predicted_pv_kwh`)

```
peaks     = [peak_pv_w | letzte 14 Tage, peak_pv_w > 100 W]
p75       = 75. Perzentil(peaks)   (konservative Schätzung)

Mit Forecast:
  clear_frac  = Ø(1 − cloud_coverage / 100) über alle Forecast-Slots
  daylight_h  = astronomische Tageslänge für HA-Breitengrad + aktuellen Monat
  pv_factor   = clear_frac × min(1,0 ; daylight_h / 12,0)

Ohne Forecast:
  pv_factor   = 0,5          (neutrale Annahme)
  daylight_h  = Näherung für 51 °N + aktuellen Monat

predicted_pv = max(0 ; (p75 / 1000) × pv_factor × daylight_h)
```

**Tageslängenformel** (`daylight_hours_approx` in `weather_client.py`):

```
day_of_year = (month − 1) × 30 + 15
decl        = 23,45° × sin(360° × (284 + day_of_year) / 365)
cos_ha      = −tan(lat) × tan(decl)   [geclampt auf −1 … 1]
daylight_h  = 2 × arccos(cos_ha) / 15
```

### Grid-Charge-Empfehlung (`should_grid_charge`)

```
usable_kwh         = battery_capacity_kwh × max(0 ; soc − battery_min_soc) / 100
should_grid_charge = (predicted_load > 0)
                     AND (usable_kwh + predicted_pv) < predicted_load
```

Wenn Batterie + erwarteter PV-Ertrag den prognostizierten Verbrauch nicht
decken, empfiehlt miniEMS das Nachladen aus dem Netz.

### Wetterdaten-Cache

`WeatherClient` cached das Ergebnis von `weather.get_forecasts` für **30 Minuten**.
Der HA-Breitengrad wird einmalig von `http://supervisor/core/api/config` gelesen.
