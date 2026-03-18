# Getting Started

**miniEMS** is a Home Assistant add-on that automatically manages the charging and discharging of a solar battery using real-time electricity prices, Solcast PV forecasts, and your own historical consumption data.

## What it does

| Feature | Description |
|---|---|
| **Smart grid charging** | Charges the battery from the grid only during cheap-rate windows when Solcast confirms the sun won't do it instead |
| **PV surplus charging** | Detects PV surplus and switches the inverter to charge the battery from solar |
| **Battery protection** | Blocks discharging when SoC falls below your configured minimum |
| **Cost tracking** | Tracks daily grid cost, PV savings, feed-in revenue, and grid-charge cost |
| **HA sensors** | Publishes 30+ sensors under `sensor.miniems_*` — compatible with the Energy Dashboard |
| **Live dashboard** | Ingress panel with auto-refresh, warnings banner, and mode-change log |
| **Solcast integration** | Reads your Solcast PV forecast directly from HA entities |
| **Prediction fallback** | Temperature-rule fallback when Solcast or historical data is unavailable |

## Requirements

| Requirement | Notes |
|---|---|
| Home Assistant OS or Supervised | The add-on uses the Supervisor API |
| Deye inverter + ha-solarman integration | Provides PV, battery, grid, and load power sensors |
| Dynamic electricity price sensor | e.g. Octopus Energy integration |
| (Optional) Solcast integration | For accurate PV forecast data |
| (Optional) OpenWeatherMap integration | For temperature-based load prediction |

## Quick Start

1. **Install** the add-on from your local repository (see [Installation](installation.md)).
2. **Start** the add-on — it will run but not control anything yet.
3. **Open the dashboard** via the HA sidebar → miniEMS.
4. **Go to Settings** and configure your entity IDs and thresholds.
5. **Enable Battery Control** — use **Simulation Mode** first to verify the logic is correct.
6. **Disable Simulation Mode** when you are satisfied with the behaviour.

!!! tip "Simulation Mode"
    With Simulation Mode enabled, miniEMS logs all inverter commands with a `[SIM]` prefix but never sends them. A **SIM** badge appears in the dashboard. Always test with simulation first.
