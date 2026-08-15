"""miniEMS – central constants.

All magic strings, file paths, version numbers, and tunable values live here.
Never duplicate these across modules – always import from const.
"""
from enum import Enum

# ── Add-on version ─────────────────────────────────────────────────────────
# Fallback only – overwritten at startup by main._sync_version_from_supervisor()
# which reads the real version from http://supervisor/addons/self/info.
VERSION = "2.0.3"

# ── Config schema version (used by migration.py) ────────────────────────────
CONFIG_SCHEMA_VERSION = 13

# ── Battery current limits ────────────────────────────────────────────────────
# The Deye inverter exposes battery limits as CURRENT in amperes
# (number.deye8k_battery_max_charging_current), not as power in watts.
# Range of those entities is 0–350 A; values outside are rejected by HA.
BATTERY_MAX_CURRENT_A = 350

# ── File paths ───────────────────────────────────────────────────────────────
OPTIONS_FILE = "/data/options.json"   # written by HA Supervisor (UI)
CONFIG_FILE  = "/data/config.json"    # written by miniEMS (persisted)
DB_FILE      = "/data/miniems.db"     # SQLite energy statistics

# ── Home Assistant API ────────────────────────────────────────────────────────
HA_API_BASE         = "http://hassio/homeassistant/api"
HA_STATES_URL       = f"{HA_API_BASE}/states"
HA_SERVICES_URL     = f"{HA_API_BASE}/services"
SUPERVISOR_RESTART_URL = "http://supervisor/addons/self/restart"

# ── Polling / timing (seconds) ────────────────────────────────────────────────
HA_POLL_INTERVAL_SEC  = 15   # how often ha_ws_client refreshes HA states
HA_RETRY_INTERVAL_SEC = 10   # retry delay after a failed poll / 401

# ── Sensor staleness limits (seconds) ─────────────────────────────────────────
# HA only advances last_updated when a value actually changes, so these must be
# generous enough that a legitimately constant reading is not flagged.
SENSOR_MAX_AGE_SEC   = 300     # power/SoC: move constantly, 5 min frozen = broken
FORECAST_MAX_AGE_SEC = 28800   # Solcast: updates ~every 30 min in daylight, but can
                                # go quiet for ~6h overnight (observed) – 8h gives margin
PRICE_MAX_AGE_SEC    = 21600   # dynamic tariff: may hold one value for several hours

# ── Inverter write confirmation (seconds) ──────────────────────────────────────
# A service call accepted by HA (HTTP 200) is not proof the inverter applied it –
# some Deye/Solarman bridges only confirm a written number/switch on their next
# poll, which can lag by many minutes. Re-check every tick and keep resending
# until the real state matches; this equals the EMS tick interval, i.e. retry
# on every cycle rather than waiting out a long grace period.
INVERTER_WRITE_CONFIRM_TIMEOUT_SEC = 30

# ── Custom integration installer ──────────────────────────────────────────────
from pathlib import Path
INTEGRATION_SOURCE_DIR = Path("/usr/bin/integration")
INTEGRATION_TARGET_DIR = Path("/config/custom_components/miniems")

# ── EMS operating modes ───────────────────────────────────────────────────────
class EMSMode(str, Enum):
    IDLE            = "Idle"
    PV_CHARGING     = "PV Charging"
    # Grid-friendly hold: PV surplus is exported instead of stored, until the
    # remaining forecast has fallen to roughly what the battery still needs.
    # The label deliberately contains neither "PV" nor "Grid" – the dashboard
    # classifies badges by substring.
    EXPORT_SURPLUS  = "Export Surplus"
    GRID_CHARGING   = "Grid Charging (Cheap Rate)"
    PROTECT_BATTERY = "Battery Protection (Min SoC)"
