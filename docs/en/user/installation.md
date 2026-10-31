---
revision_date: 2026-10-31
---

# Installation

miniEMS consists of two parts that must both be installed:

| Part | What it is | Where it runs |
|---|---|---|
| **miniEMS Add-on** | The EMS engine — reads sensors, makes decisions, hosts the dashboard | HA add-on (Docker container) |
| **miniEMS Integration** | Custom HA integration — creates `sensor.miniems_*` entities in HA | Home Assistant core (`custom_components`) |

---

## 1. Install the Add-on

miniEMS is distributed as a Home Assistant add-on repository. Nothing needs to be copied.

1. Use the **"Add repository"** button, which opens your HA instance directly:
   [![Add repository](https://my.home-assistant.io/badges/supervisor_add_addon_repository.svg)](https://my.home-assistant.io/redirect/supervisor_add_addon_repository/?repository_url=https%3A%2F%2Fgithub.com%2FGuidoJeuken-6512%2FminiEMS)

   Or manually: **Settings → Add-ons → Add-on Store → ⋮ → Repositories** and add
   `https://github.com/GuidoJeuken-6512/miniEMS`.
2. **miniEMS** appears in the add-on store. Open it and click **Install**.
   HA downloads a ready-made image; nothing is built on your device.
3. Click **Start**. The add-on will launch on port 8080 (ingress).
4. Open the sidebar panel — you should see the **miniEMS Dashboard** with `Idle` mode. Warnings are expected at this stage.

Supported architectures: **aarch64** (Raspberry Pi 4/5, HA Green/Yellow) and **amd64**
(Intel/AMD PCs, VMs). 32-bit systems are not supported.

Updates appear under **Settings → Add-ons → miniEMS** once a new version is released.

---

## 2. Install the Custom Integration

The integration creates all `sensor.miniems_*` entities in Home Assistant by polling the add-on's `/api/status` endpoint.

!!! info "Automatic installation"
    The add-on **automatically** copies the integration files to
    `/config/custom_components/miniems/` on every startup. No manual file copying is needed.
    Make sure the add-on is running (step 1) before restarting HA.

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

4. Click **Submit**. The integration tests the connection to `/api/status`. If it succeeds, the **miniEMS** device and all 28 sensors are created immediately.

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

The add-on **automatically** updates the integration files on startup when a new version is detected.

1. Update the add-on (see above) and restart it.
2. Restart Home Assistant to load the new custom component.

!!! note "Config persistence"
    All settings are stored in `/data/config.json` on the HA host. They survive add-on updates, restarts, and Supervisor reloads.

---

## Uninstall

1. Remove the integration: **Settings → Integrations → miniEMS → Delete**.
2. Stop and remove the add-on via **Settings → Add-ons → miniEMS → Uninstall**.
3. Delete the integration files: `/config/custom_components/miniems/`.
4. To also remove stored data: delete `/data/miniems.db` and `/data/config.json` via the HA Terminal add-on.
