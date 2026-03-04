# Configuration

All options are set via the add-on **Configuration** page in Home Assistant.
Values are stored in `/data/options.json` and merged into `/data/config.json`
for persistence.

## Options Reference

### Deye Inverter Entities

| Option | Type | Default | Description |
|---|---|---|---|
| `pv_power_entity` | string | `sensor.deye_pv_total_power` | Total PV output power (W) |
| `battery_soc_entity` | string | `sensor.deye_battery_soc` | Battery state of charge (%) |
| `battery_power_entity` | string | `sensor.deye_battery_power` | Battery charge/discharge power (W) |
| `battery_voltage_entity` | string | `sensor.deye_battery_voltage` | Battery voltage (V) |
| `grid_power_entity` | string | `sensor.deye_grid_power` | Grid import/export power (W, positive = import) |
| `load_power_entity` | string | `sensor.deye_load_power` | House load power (W) |
| `battery_capacity_kwh` | float | `10.0` | Usable battery capacity in kWh |
| `battery_min_soc` | int (0–100) | `15` | Minimum SoC before battery protection kicks in (%) |
| `battery_max_soc` | int (0–100) | `95` | Maximum SoC for charging decisions (%) |

### Authentication

| Option | Type | Default | Description |
|---|---|---|---|
| `long_lived_token` | string? | *(empty)* | HA long-lived access token. Used as fallback when the SUPERVISOR_TOKEN is rejected (401). Create one under **Profile → Long-Lived Access Tokens** in HA. |

> If the add-on cannot reach the HA API, check the [Architecture](architecture.md)
> page for the token fallback flow.

### Octopus Energy / Electricity Price

| Option | Type | Default | Description |
|---|---|---|---|
| `electricity_price_entity` | string | `sensor.octopus_energy_electricity_current_rate` | Current electricity spot price (€/kWh) |
| `cheap_rate_threshold_eur` | float | `0.10` | Price below which grid charging is triggered (€/kWh) |

### EMS Parameters

| Option | Type | Default | Description |
|---|---|---|---|
| `pv_surplus_threshold_w` | int | `200` | Minimum PV surplus over load to trigger PV Charging mode (W) |
| `update_interval_sec` | int (10–300) | `30` | How often the EMS loop and cost accounting run (s) |

## Config Persistence

Settings are safe across:
- Add-on **restart** (reads `/data/config.json`)
- Supervisor **reload** (`options.json` reset → `config.json` fallback)
- Add-on **update** (migration system upgrades schema)

To fully reset to defaults, delete `/data/config.json` via SSH or the
HA terminal add-on.
