# Dashboard & UI

The miniEMS dashboard is an ingress panel accessible directly from the HA sidebar. It auto-refreshes every 5 seconds and is fully translated (German/English, auto-detected from HA).

## Navigation

Three tabs are available:

| Tab | Path | Purpose |
|---|---|---|
| **Dashboard** | `/` | Live status, costs, battery, Solcast, prediction |
| **Settings** | `/settings` | Configuration form |
| **Log** | `/log` | Full mode-change event log |

---

## Dashboard Tab

### Warnings Banner

If any required sensor is unavailable or a config field is missing, a yellow banner appears at the top listing each issue. Fix the issues in Settings — the banner disappears automatically on the next refresh.

A data-gap warning also appears here if miniEMS detects that the add-on was down for longer than two update intervals (energy accounting has a gap).

### Mode Badge

Shows the current EMS operating mode:

| Badge | Meaning |
|---|---|
| `Idle` (grey) | Normal operation |
| `PV Charging` (green) | Battery charging from PV surplus |
| `Grid Charging (Cheap Rate)` (blue) | Battery charging from cheap grid power |
| `Battery Protection (Min SoC)` (red) | SoC below minimum — discharging blocked |

A **SIM** badge appears when Simulation Mode is active.

### Live Power Grid

Six cards showing real-time values: PV Power, Load Power, Grid Power, Battery SoC, Battery Power, and Electricity Price. The price card is highlighted green when the cheap-rate threshold is met.

### Cost & Savings

Today's and this week's accumulated values:

- **Saved Today (PV)** — money saved by using solar instead of buying from grid
- **Grid Cost Today** — actual cost of grid electricity imported
- **PV Used Today** — kWh of PV energy consumed by the house
- **Grid Import Today** — total grid import kWh

### Cost Details

| Card | Formula |
|---|---|
| Cost Without Grid Charge | `grid_cost_today − grid_charge_cost_today` |
| Cost at Fix Price | `load_total_kwh × fix_price` |
| Feed-in Today | kWh exported to the grid |
| Feed-in Revenue | `feed_in_kwh × feed_in_tariff` |
| Grid Charge Today | kWh charged from the grid (not from PV) |

### Battery State

Free to Charge and Useable kWh, computed from SoC and your configured capacity and SoC limits.

### Solcast PV Forecast

Shows remaining PV expected for today, today's total, and tomorrow's total from Solcast. Only visible when Solcast entities are configured.

### Forecast & Prediction

Internal consumption model output. Shows the predicted load and fallback PV yield estimate. The badge indicates the data source:

- **historically estimated** — based on temperature-matched historical days
- **fallback estimation** — based on temperature rules (not enough history yet)

### Mode Change Log (compact)

Shows the most recent mode change with a link to the full Log page.

---

## Log Tab

The dedicated log page shows:

- **Summary bar** — current mode, SoC, free-to-charge kWh, Solcast remaining, current price
- **Full log table** — up to the last 100 grid-charge mode-change events

Each log entry records:

| Column | Description |
|---|---|
| State | ▲ ON (grid charging started) or ▼ OFF (grid charging stopped) |
| Time | ISO 8601 timestamp |
| Free (kWh) | Battery free-to-charge at the time of the event |
| Useable (kWh) | Battery useable kWh at the time of the event |
| Pred. Load (kWh) | Predicted daily load at the time of the event |

---

## Settings Tab

The settings page provides a form for all configuration options. After editing, click **Save & Restart** — the add-on restarts and picks up the new configuration automatically.

!!! note "Restart time"
    The add-on typically restarts within 2–5 seconds. The browser will redirect back to the dashboard after 6 seconds.
