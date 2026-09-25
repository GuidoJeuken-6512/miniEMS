"""miniEMS – central constants.

All magic strings, file paths, version numbers, and tunable values live here.
Never duplicate these across modules – always import from const.
"""
from enum import Enum

# ── Add-on version ─────────────────────────────────────────────────────────
# Fallback only – overwritten at startup by main._sync_version_from_supervisor()
# which reads the real version from http://supervisor/addons/self/info.
VERSION = "3.1.0"

# ── Config schema version (used by migration.py) ────────────────────────────
CONFIG_SCHEMA_VERSION = 20

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
# Registry data (device/entity registry, energy-dashboard prefs) has no REST
# endpoint – WebSocket-only. Verified reachable from the add-on container with
# SUPERVISOR_TOKEN, no extra permission beyond the existing homeassistant_api:
# true in config.yaml. Used only for one-shot queries (ha_ws_api.py), never
# for the per-tick state poll – that stays ha_state_client.py's REST job.
HA_WEBSOCKET_URL = "ws://hassio/homeassistant/websocket"

# ── MQTT (mqtt_publisher.py – not yet wired into main.py) ────────────────────
# Supervisor's Services API for the MQTT service add-on (host/port/credentials
# of the broker, if one is installed). Same http://supervisor/... base as
# SUPERVISOR_RESTART_URL above.
SUPERVISOR_MQTT_URL = "http://supervisor/services/mqtt"
# Home Assistant's default MQTT Discovery prefix (matches HA's own default;
# not currently exposed as a config option).
MQTT_DISCOVERY_PREFIX = "homeassistant"
# Namespace for this add-on's own state topics, separate from the discovery
# config topics above.
MQTT_STATE_TOPIC_PREFIX = "miniems"

# ── Polling / timing (seconds) ────────────────────────────────────────────────
HA_POLL_INTERVAL_SEC  = 15   # how often ha_state_client refreshes HA states
HA_RETRY_INTERVAL_SEC = 10   # retry delay after a failed poll / 401
HA_WS_QUERY_TIMEOUT_SEC = 15   # one-shot WS query (ha_ws_api.py) – connect+auth+response

# ── Sensor staleness limits (seconds) ─────────────────────────────────────────
# HA only advances last_updated when a value actually changes, so these must be
# generous enough that a legitimately constant reading is not flagged.
SENSOR_MAX_AGE_SEC   = 300     # power/SoC: move constantly, 5 min frozen = broken
FORECAST_MAX_AGE_SEC = 28800   # Solcast: updates ~every 30 min in daylight, but can
                                # go quiet for ~6h overnight (observed) – 8h gives margin
PRICE_MAX_AGE_SEC    = 21720   # 6 h + 2 min. A time-of-use tariff is written only at
                                # tier boundaries, and the schedule is deterministic:
                                # the longest window measured on this installation is
                                # exactly 6 h (06:00–12:00). At 21600 the threshold
                                # equalled the longest legitimate gap, so a false alarm
                                # was one scheduling jitter away. The 2 min are clearance,
                                # not guesswork – a larger value would only slow detection
                                # without covering any real case.

# Grace period for once-per-day values (Solcast daily forecast totals). The
# source rewrites them within ~5 min of local midnight; until then a timestamp
# from yesterday is legitimate. 15 min gives that margin without letting a truly
# stopped integration hide. Used by HAStateClient.is_stale_daily(), which
# asks "was it written today?" instead of "how old is it?" – see
# docs/roadmap/sensor-staleness.md for why no age threshold can be right here.
DAILY_VALUE_GRACE_SEC = 900

# How old the Solcast *data* may be – measured on the value of the last-fetch
# entity, not on any HA timestamp. Solcast keeps serving its persisted cache when
# the API is unreachable, so the sensors stay `available` and their timestamps
# keep advancing while the numbers quietly age.
#
# Measured on the production system: 4-6 successful fetches per day on five
# consecutive days, with the longest legitimate overnight gap at 15.5 h (last
# fetch 12:44 UTC, first the next day 04:11 UTC). Shorter winter days push that
# towards an estimated ~19 h. 30 h leaves margin over that while still catching a
# stalled API within about a day; the cache holds roughly seven days of forecast
# before the sensors finally go `unavailable` on their own.
SOLCAST_DATA_MAX_AGE_SEC = 108000

# ── Inverter write confirmation (seconds) ──────────────────────────────────────
# A service call accepted by HA (HTTP 200) is not proof the inverter applied it –
# some Deye/Solarman bridges only confirm a written number/switch on their next
# poll, which can lag by many minutes. Re-check every tick and keep resending
# until the real state matches; this equals the EMS tick interval, i.e. retry
# on every cycle rather than waiting out a long grace period.
INVERTER_WRITE_CONFIRM_TIMEOUT_SEC = 30

# HTTP timeout for a single HA service call to the inverter.
# HA's REST /api/services/... blocks until the service has *finished*, and a
# Solarman/Modbus write goes all the way to the inverter and waits for its
# acknowledgement – measured on the production system, that regularly exceeds
# 10s. A timeout here is therefore NOT evidence that the write failed: HA has
# usually applied it already and only the response is late. The write is
# confirmed against the real entity state one tick later either way, so this
# value only needs to be generous enough to catch the common case without
# stalling the EMS loop – it is deliberately below the tick interval budget.
INVERTER_SERVICE_CALL_TIMEOUT_SEC = 15

# Window over which InverterController.write_errors counts real (HTTP-rejected)
# write failures for the dashboard/warning banner. A plain lifetime total never
# clears once any failure has happened since the last add-on restart – 28
# failures from days ago would look identical to 28 happening right now.
# Pruned to this window on every read instead.
INVERTER_WRITE_ERROR_WINDOW_SEC = 3600   # 1 h

# How long a single channel (charge/discharge current, grid-charge switch)
# must stay continuously unconfirmed before InverterController.longest_pending_sec
# counts it as a real, consequential problem (sensor.miniems_inverter_write_status
# = "error") rather than a normal, still-plausibly-resolving confirmation cycle
# ("warning"). Deliberately conservative and explicitly PROVISIONAL: the only
# figure on record for how long a legitimate confirmation can take (~25 min,
# CHANGELOG v2.0.1) is an unsourced historical observation, not a vendor spec
# and not backed by retained raw data. The write-confirm events persisted to
# event_log (entry_type="write_confirm") exist specifically to replace this
# guess with real, multi-day latency data – revisit once that exists.
INVERTER_WRITE_STUCK_THRESHOLD_SEC = 1800   # 30 min – provisional, see above

# ── Gelernte Ladeleistung (battery_capability.py) ─────────────────────────────
# SoC-bucketed history of GRID_CHARGING throughput, replacing
# battery_max_charge_current_a x voltage with an observed value once enough
# history exists – see docs/roadmap/energiefahrplan.md, "Gelernte
# Ladeleistung statt Konfigurationswert".
#
# Provisional, like INVERTER_WRITE_STUCK_THRESHOLD_SEC above: not yet derived
# from real GRID_CHARGING frequency on a production installation (open
# question in the roadmap doc) – revisit once event_log/write-confirm data
# gives a real distribution to size these against.
BATTERY_CAPABILITY_MIN_SAMPLES_PER_DAY = 6   # ~3 min of GRID_CHARGING at a 30s tick
BATTERY_CAPABILITY_MIN_DAYS = 3              # qualifying days needed before trusting history
BATTERY_CAPABILITY_LOOKBACK_DAYS = 14

# ── Energiefahrplan (energy_plan.py) ──────────────────────────────────────────
# Recompute interval for the proactive tomorrow-deficit plan. Hourly, not
# every 30s tick: display-only, and price/PV forecasts don't change on a
# tick's timescale – see docs/roadmap/energiefahrplan.md, "Trigger".
ENERGY_PLAN_RECOMPUTE_SEC = 3600

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
