---
revision_date: 2026-04-06
---

# Installation

miniEMS consists of two parts that must both be installed:

| Part | What it is | Where it runs |
|---|---|---|
| **miniEMS Add-on** | The EMS engine — reads sensors, makes decisions, hosts the dashboard | HA add-on (Docker container) |
| **miniEMS Integration** | Custom HA integration — creates `sensor.miniems_*` entities in HA | Home Assistant core (`custom_components`) |

---

## 1. Install the Add-on

miniEMS is distributed as a local Home Assistant add-on.

1. Copy the `miniems/` folder into your HA add-on directory via SSH or Samba:
   ```
   /addons/local/miniems/
   ```
2. In HA go to **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**.
3. The **miniEMS** add-on will appear under **Local add-ons**.
4. Click **Install**.
5. Click **Start**. The add-on will launch on port 8080 (ingress).
6. Open the sidebar panel — you should see the **miniEMS Dashboard** with `Idle` mode. Warnings are expected at this stage.

---

## 2. Install the Custom Integration

The integration creates all `sensor.miniems_*` entities in Home Assistant by polling the add-on's `/api/status` endpoint.

### Copy the integration files

Copy the `integration/` folder from the add-on source into HA's `custom_components` directory:

```
/config/custom_components/miniems/
```

The folder must contain these files:

```
custom_components/miniems/
├── __init__.py
├── config_flow.py
├── coordinator.py
├── manifest.json
├── sensor.py
└── strings.json
```

!!! tip "Where to find the source files"
    The integration source is included in the add-on package at:
    `miniems/rootfs/usr/bin/integration/`

### Restart Home Assistant

A **full HA restart** is required to load the new custom component — restarting only the add-on is not enough.

**Settings → System → Restart Home Assistant**

### Add the integration

1. Go to **Settings → Integrations → + Add integration**.
2. Search for **miniEMS** and select it.
3. Enter the **Base URL** of the add-on API:

   | Setup | URL |
   |---|---|
   | Default (same host) | `http://homeassistant:8080` |
   | Custom / external | `http://<your-ha-ip>:8080` |

4. Click **Submit**. The integration tests the connection to `/api/status`. If it succeeds, the **miniEMS** device and all 30 sensors are created immediately.

### Options (optional)

After setup you can adjust the poll interval:

**Settings → Integrations → miniEMS → Configure**

| Option | Default | Range | Description |
|---|---|---|---|
| Poll interval | 30 s | 10 – 300 s | How often the integration fetches `/api/status` |

---

## 3. Configure the Add-on

1. Open the miniEMS sidebar panel and go to the **Settings** tab.
2. Enter your entity IDs (inverter, price sensor, etc.) and thresholds.
3. Click **Save & Restart**.

See [Configuration](configuration.md) for the full settings reference.

---

## Updates

### Add-on update

1. Replace the add-on files in `/addons/local/miniems/`.
2. In HA: **Settings → Add-ons → miniEMS → Update** (or restart the add-on).
3. Configuration is migrated automatically — no manual steps required.

### Integration update

1. Replace the files in `/config/custom_components/miniems/` with the new version.
2. Restart Home Assistant to reload the custom component.

!!! note "Config persistence"
    All settings are stored in `/data/config.json` on the HA host. They survive add-on updates, restarts, and Supervisor reloads.

---

## Uninstall

1. Remove the integration: **Settings → Integrations → miniEMS → Delete**.
2. Stop and remove the add-on via **Settings → Add-ons → miniEMS → Uninstall**.
3. Delete the integration files: `/config/custom_components/miniems/`.
4. To also remove stored data: delete `/data/miniems.db` and `/data/config.json` via the HA Terminal add-on.
