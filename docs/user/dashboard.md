# Dashboard & UI

The miniEMS dashboard is an ingress panel accessible directly from the HA sidebar. It auto-refreshes every 5 seconds and is fully translated (German/English, auto-detected from HA).

## Navigation

Six tabs are available:

| Tab | Path | Purpose |
|---|---|---|
| **Dashboard** | `/` | Live status, costs, battery, Solcast, prediction |
| **Settings** | `/settings` | Configuration form |
| **Log** | `/log` | Unified event log (mode changes + price changes) |
| **config.json** | `/config-json` | Raw editor for `/data/config.json` |
| **options.json** | `/options-json` | Raw editor for `/data/options.json` |
| **Database** | `/database` | Browse all rows in `daily_stats` |

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
- **Full event table** — up to the last 100 events: grid-charge mode changes and electricity price changes

See [Event Log](log.md) for full details on all event types and columns.

---

## config.json Tab

Direct raw editor for `/data/config.json` — the persistent miniEMS settings file. Displays the file as pretty-printed JSON in a monospace textarea. Changes are validated client-side before saving, then the add-on restarts automatically.

A **Reformat JSON** button re-indents the content without saving, useful for reviewing edits.

!!! warning "Advanced use"
    Prefer the **Settings** tab for normal configuration. The raw editor is intended for debugging, migration fixes, or setting values not exposed in the settings form.

---

## options.json Tab

Raw editor for `/data/options.json` — the file written by the HA Supervisor. Identical interface to the config.json tab, with an additional warning banner noting that the Supervisor may overwrite this file when the add-on is reconfigured via the HA UI.

!!! tip "Which file wins?"
    `config.json` takes precedence for any value equal to the dataclass default. For all other values, `options.json` wins on startup. After the first run the merged result is written back to `config.json`, so `config.json` is the durable source of truth.

---

## Database Tab

Browse all rows in the `daily_stats` SQLite table. Displays a summary bar (total days recorded, date range, temperature coverage) followed by a sortable full table.

Click any column header to sort ascending or descending. Rows without temperature data are shown in grey — these days are excluded from the temperature-matched load prediction.

See [Data Storage](../technical/data-storage.md) for the full column reference.

---

## Settings Tab

The settings page provides a form for all configuration options. After editing, click **Save & Restart** — the add-on restarts and picks up the new configuration automatically.

!!! note "Restart time"
    The add-on typically restarts within 2–5 seconds. The browser will redirect back to the dashboard after 6 seconds.
