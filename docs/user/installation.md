# Installation

## Add the local repository

miniEMS is distributed as a local Home Assistant add-on.

1. Copy the `miniems/` folder into your HA add-on directory (typically via SSH or Samba):
   ```
   /addons/local/miniems/
   ```
2. In HA go to **Settings → Add-ons → Add-on Store → ⋮ → Check for updates**.
3. The **miniEMS** add-on will appear under **Local add-ons**.
4. Click **Install**.

## First start

1. Click **Start**. The add-on will launch on port 8080 (ingress).
2. Open the add-on panel in the HA sidebar — you will see **miniEMS Dashboard**.
3. The dashboard will show `Idle` mode with no sensor data yet (warnings are expected at this point).
4. Go to the **Settings** tab and enter your configuration (see [Configuration](configuration.md)).
5. Click **Save & Restart**. The add-on restarts and begins monitoring immediately.

## Updates

When a new version is available:

1. Replace the add-on files in `/addons/local/miniems/`.
2. In HA: **Settings → Add-ons → miniEMS → Update** (or restart the add-on).
3. The configuration is automatically migrated — no manual steps required.

!!! note "Config persistence"
    All settings are stored in `/data/config.json` on the HA host. They survive add-on updates, restarts, and Supervisor reloads.

## Uninstall

1. Stop and remove the add-on via HA.
2. To also remove stored data: delete `/data/miniems.db` and `/data/config.json` via the HA Terminal add-on.
