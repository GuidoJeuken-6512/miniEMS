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
