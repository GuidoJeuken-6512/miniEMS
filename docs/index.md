# miniEMS – Mini Energy Management System

**miniEMS** is a Home Assistant add-on that manages a Deye solar inverter combined with a dynamic electricity tariff (e.g. Octopus Energy). It reads live sensor data from HA, makes charging decisions, and publishes computed cost/savings metrics back as HA entities.

## Features

| Feature               | Details                                                                  |
| --------------------- | ------------------------------------------------------------------------ |
| Mode control          | Idle / PV Charging / Grid Charging / Battery Protection                  |
| Cost tracking         | Daily & weekly grid cost and PV savings in €                             |
| HA sensors            | Sensors published under the `sensor.miniems_*` prefix                    |
| Web dashboard         | Ingress panel auto-refreshing every 5 s                                  |
| Forecast & prediction | Load and PV yield forecast via `weather.get_forecasts` HA action         |
| i18n                  | Dashboard and settings fully translated (de / en, auto-detected from HA) |
| Config persistence    | Settings survive supervisor reloads via `/data/config.json`              |
| Token fallback        | SUPERVISOR_TOKEN → Long-lived token on 401                               |

## Quick Start

1. Install the add-on from the local repository.
2. Open **Configuration** and set your entity IDs (Deye inverter + Octopus Energy sensors).
3. Start the add-on — it appears as **miniEMS** in the HA sidebar.
4. HA entities `sensor.miniems_*` become available immediately after the first EMS tick.

## Documentation Pages

- [Architecture](architecture.md) — component overview and data flow
- [Calculations](calculations.md) — EMS decision logic and cost formulas
- [Data Storage](data-storage.md) — where state and config are persisted
- [Configuration](configuration.md) — all configuration options explained
- [HA Sensors](sensors.md) — sensors published to Home Assistant
- [Development](development.md) — dev environment, rebuild workflow, debugging

## Source Code

The project is hosted on GitHub:
[github.com/GuidoJeuken-6512/miniEMS](https://github.com/GuidoJeuken-6512/miniEMS)
