"""miniEMS – central constants.

All magic strings, file paths, version numbers, and tunable values live here.
Never duplicate these across modules – always import from const.
"""
from enum import Enum

# ── Add-on version ─────────────────────────────────────────────────────────
# Increment on every change; keep in sync with config.yaml → version.
VERSION = "1.4.0"

# ── Config schema version (used by migration.py) ────────────────────────────
CONFIG_SCHEMA_VERSION = 6

# ── File paths ───────────────────────────────────────────────────────────────
OPTIONS_FILE = "/data/options.json"   # written by HA Supervisor (UI)
CONFIG_FILE  = "/data/config.json"    # written by miniEMS (persisted)
DB_FILE      = "/data/miniems.db"     # SQLite energy statistics

# ── Home Assistant API ────────────────────────────────────────────────────────
HA_API_BASE         = "http://hassio/homeassistant/api"
HA_STATES_URL       = f"{HA_API_BASE}/states"
HA_SERVICES_URL     = f"{HA_API_BASE}/services"
SUPERVISOR_MQTT_URL    = "http://supervisor/services/mqtt"
SUPERVISOR_RESTART_URL = "http://supervisor/addons/self/restart"

# ── Polling / timing (seconds) ────────────────────────────────────────────────
HA_POLL_INTERVAL_SEC  = 15   # how often ha_ws_client refreshes HA states
HA_RETRY_INTERVAL_SEC = 10   # retry delay after a failed poll / 401

# ── MQTT Discovery ────────────────────────────────────────────────────────────
MQTT_DISCOVERY_PREFIX   = "homeassistant"
MQTT_STATE_TOPIC_PREFIX = "miniems"

# ── EMS operating modes ───────────────────────────────────────────────────────
class EMSMode(str, Enum):
    IDLE            = "Idle"
    PV_CHARGING     = "PV Charging"
    GRID_CHARGING   = "Grid Charging (Cheap Rate)"
    PROTECT_BATTERY = "Battery Protection (Min SoC)"
