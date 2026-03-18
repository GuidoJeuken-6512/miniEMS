# Database Viewer

The **Database** tab (`/database`) gives a direct view of the `daily_stats` SQLite table — the same data that drives the consumption prediction model and the cost & savings dashboard cards.

---

## Summary Bar

Four cards at the top provide an at-a-glance overview:

| Card | Description |
|---|---|
| Days recorded | Total number of rows in `daily_stats` |
| Date range | Oldest → newest date in the table |
| Days with temperature | Rows that have a valid `avg_outdoor_temp_c` value |
| Days without temperature | Rows missing temperature — shown in **amber** if non-zero |

Days without temperature cannot be used by the temperature-matched load prediction. If many rows are missing temperature, check that the `weather_entity` is correctly configured and reachable.

---

## Data Table

All rows from `daily_stats` are shown, sorted newest first by default. Click any column header to toggle sort direction.

| Column | Unit | Description |
|---|---|---|
| Date | — | ISO date `YYYY-MM-DD` |
| Temp (°C) | °C | Average outdoor temperature — colour-coded: blue (< 0), cyan (0–10), green (10–20), amber (> 20). Grey if missing. |
| Load (kWh) | kWh | Total household energy consumption |
| PV Used (kWh) | kWh | PV energy consumed directly by the house |
| Grid Import (kWh) | kWh | Total energy drawn from the grid |
| Grid Charge (kWh) | kWh | Energy drawn from grid specifically for battery charging |
| Feed-in (kWh) | kWh | Energy exported to the grid |
| Peak PV (W) | W | Highest instantaneous PV power recorded during the day |
| Grid Cost (€) | € | Cost of all grid imports |
| PV Savings (€) | € | Cost avoided by using PV instead of grid power |
| Avg Price (€/kWh) | €/kWh | Tick-weighted average electricity price |
| Ticks | — | Number of EMS update cycles recorded (data quality indicator — a full day at 30 s intervals = 2880 ticks) |
| High Rate (kWh) | kWh | Load consumed at high price tier (`price ≥ medium_rate_threshold_eur`) |
| Medium Rate (kWh) | kWh | Load consumed at medium price tier |
| Low Rate (kWh) | kWh | Load consumed at low price tier (`price < cheap_rate_threshold_eur`) |

Rows shown in **grey** are missing temperature data and are excluded from the temperature-matched prediction.

---

## How the data is collected

Values accumulate in-memory throughout the day inside `CostOptimizer`. At midnight (detected when `date.today()` changes) the complete day's row is flushed to SQLite. The `last_flush_ts` column records when the last write occurred.

The database is never modified by the UI — this page is read-only.

---

## Relationship to consumption prediction

The temperature-based load prediction (`consumption_model.py`) queries this table for days with a similar `avg_outdoor_temp_c` (±4 °C, last 60 days). If at least 3 such days exist, the median `load_total_kwh` of those days is used as the predicted load. Days without temperature are skipped by this query, which is why the grey rows matter.

See [Data Storage](../technical/data-storage.md) for the full `daily_stats` schema reference.
