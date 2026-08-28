---
revision_date: 2026-08-27
---

# Sensor Staleness

How miniEMS detects that a sensor value is too old to serve as a basis for a control
decision — and why a single age threshold is not enough.

## Mechanism

```
HAStateClient._parse_ts(state)
  → prefers state["last_updated"], falls back to state["last_changed"]
  → stored in _state_ts[entity_id]

get_state_age_sec(entity_id)
  → now(UTC) − _state_ts[entity_id]   (None if never received)

is_stale(entity_id, max_age_sec)
  → age is None  OR  age > max_age_sec
```

Three constants cover the three continuous/regular update classes (`const.py`):

| Constant | Value | Intended for |
|---|---|---|
| `SENSOR_MAX_AGE_SEC` | 300 s (5 min) | Power sensors, SoC liveness proxy |
| `FORECAST_MAX_AGE_SEC` | 28,800 s (8 h) | Solcast remaining-today forecast (`solcast_remaining_today_entity`) |
| `PRICE_MAX_AGE_SEC` | 21,720 s (6 h + 2 min) | Electricity price |

Two further constants cover special cases that "age" alone cannot solve (see below):

| Constant | Value | Intended for |
|---|---|---|
| `DAILY_VALUE_GRACE_SEC` | 900 s (15 min) | Grace period after local midnight for once-per-day values |
| `SOLCAST_DATA_MAX_AGE_SEC` | 108,000 s (30 h) | Age of the Solcast **data** itself, not the sensor's HA timestamp |

`get_state_value(entity_id)` returns `None` for `unavailable`/`unknown`/`""` and
otherwise does `float(raw)` — for timestamp-valued sensors (an ISO date as the state)
this always returns `None`, even for a healthy sensor. That is what
`get_state_datetime()` (`ha_state_client.py:73`) exists for — it parses `raw` as an ISO
timestamp instead of a number.

## Two signals that do *not* work as an age/liveness proxy

Before settling on "time since last update" as the general-purpose answer, two obvious
alternatives are worth ruling out — both checked against the actual integration source.

### `last_reported` instead of `last_updated`

Home Assistant maintains three timestamps per entity:

| Field | Advances when … |
|---|---|
| `last_changed` | the **value** changes |
| `last_updated` | value **or** attributes are written ← what miniEMS uses |
| `last_reported` | the entity **writes at all**, even with an identical value |

All three only advance when the integration calls `async_write_ha_state()`. The Solcast
integration deliberately does not do that on every cycle for its daily totals ("today"/
"tomorrow") (`custom_components/solcast_solar/sensor.py`, `_handle_coordinator_update`):

```python
if self._update_policy == SensorUpdatePolicy.DEFAULT and not (
        self._coordinator.date_changed or self._coordinator.data_updated):
    return          # ← no async_write_ha_state()
```

`get_sensor_update_policy()` (same file) grants `EVERY_TIME_INTERVAL` to only a handful
of keys (e.g. `ENTITY_FORECAST_REMAINING_TODAY`, `ENTITY_POWER_NOW`). All daily totals
and timestamp sensors ("today"/"tomorrow", peak-power-time sensors) get
`SensorUpdatePolicy.DEFAULT` — written only on forecast fetch or date rollover. For these
sensors `last_changed`, `last_updated` and `last_reported` are identical at all times;
`last_reported` would not be a better data source than `last_updated`.

### `unavailable` as a liveness proxy

`available` is a purely opt-in property of each integration — the two integrations
miniEMS depends on decide it in opposite ways:

**Solarman/Deye** (`custom_components/solarman/entity.py:38`):

```python
def available(self) -> bool:
    return self.coordinator.last_update_success and self.coordinator.device.state.value > -1
```

→ Modbus connection lost ⇒ `unavailable`. A reliable, immediate signal — that is why a
plain presence/age check is sufficient for the inverter sensors.

**Solcast** (`custom_components/solcast_solar/sensor.py:690`):

```python
def available(self) -> bool:
    return self._attr_available      # = (self._sensor_data is not None)
```

`_sensor_data` comes from the forecast cache persisted to disk, not from an API call.
API down, quota exhausted, internet gone — the cache stays populated, the sensor stays
`available` and serves yesterday's forecast indefinitely. For Solcast, `unavailable` is
therefore **not** a dependable liveness signal; the data-freshness check further below
covers that instead.

This yields two orthogonal failure modes:

1. **Connection dead** → `unavailable`. Covered for all hardware sensors by
   `get_state_value() is None`.
2. **Connection alive, data semantically outdated** (Solcast forecast from yesterday,
   served from cache) → neither `unavailable` nor any timestamp catches this. That needs
   a semantic check (data freshness, see below).

## Sensor classes by update behaviour

| Class | Example | Real cadence | Check |
|---|---|---|---|
| a) continuous | Power sensors, price feed | seconds | `is_stale()` with `SENSOR_MAX_AGE_SEC` |
| b) fixed write cadence, value-driven | Solcast "remaining today", "power now" | 5-min cadence during the day, up to ~6 h flat overnight | `is_stale()` with `FORECAST_MAX_AGE_SEC` |
| c) several times a day, fixed points | Electricity price (ToU tariff) | up to 6 h at a stretch, schedule fixed | `is_stale()` with `PRICE_MAX_AGE_SEC` |
| d) event-driven, ~1×/day | Solcast daily totals and timestamps ("today"/"tomorrow", peak-power-time entities) | only on fetch/date rollover | `is_stale_daily()` — date check, not an age check |
| e) practically never | SoC, battery capacity | hours to days, legitimately | no age check, presence check only |

For class (d) no age limit can be structurally correct: the same forecast value is fresh
at 09:00 and still correct at 23:00 — the timestamp's age says nothing about validity in
either case. Raising the threshold merely postpones the problem, it does not solve it.

## Implementation per class

### a–c: age check (`is_stale`)

`EMSController._is_stale(entity_id, max_age_sec)` (`ems_controller.py:587`) delegates to
`HAStateClient.is_stale()`. Used for power sensors, the Solcast remaining-today
forecast, and the electricity price.

`PRICE_MAX_AGE_SEC = 21,720` rather than a round number: the tariff is purely
time-driven, the schedule is fixed, and the longest window is exactly 6 h (06:00–12:00,
`activation_rules`). At 21,600 s (exactly 6 h) the threshold would be an exact tie with
the longest legitimate window; 120 s of clearance avoids that without meaningfully
degrading detection time.

### d: date check (`is_stale_daily`) instead of an age check

`HAStateClient.is_stale_daily(entity_id, grace_sec)` (`ha_state_client.py:122`) asks
"was it written today?" instead of "how old is it?". For class (d) this is answerable
exactly, because the Solcast integration is guaranteed to write on every date rollover
(`coordinator.py`):

```python
self.tasks[TASK_LISTENERS] = async_track_utc_time_change(
    self.hass, self._update_integration_listeners, minute=range(0, 60, 5), second=0
)
```

`_update_integration_listeners` runs every five minutes and sets
`self._date_changed = current_day != self._last_day` (local timezone). When
`date_changed` is true, the early-return condition from the previous section no longer
applies and **all** `DEFAULT`-policy sensors are rewritten. This yields a hard guarantee:
as long as the Solcast integration is running, every class-(d) sensor carries a timestamp
from the current day within five minutes of local midnight. A timestamp from yesterday
necessarily means the integration has stopped.

Two implementation details:

- **Local timezone.** Solcast evaluates the date rollover in local time
  (`dt.now(self.solcast.options.tz)`), while `_parse_ts()` stores UTC. The comparison in
  `is_stale_daily()` therefore deliberately runs in local time — under CEST (UTC+2)
  local midnight would otherwise sit on the wrong UTC day for two hours.
- **Grace period `DAILY_VALUE_GRACE_SEC` (900 s).** Covers the minutes between 00:00 and
  00:05 local time, in which Solcast has not rewritten the value yet.

`EMSController._is_stale_daily(entity_id)` (`ems_controller.py:669`) wraps the call with
the constant. Used in three places:

- `_should_grid_charge()` (`ems_controller.py:543`) — tomorrow fallback: if today's
  remaining forecast is spent or stale, the controller checks whether tomorrow's forecast
  alone would refill the battery before falling back to the dark-window heuristic. Stale
  is treated as "unusable" here, not as an error — the path simply falls through to the
  dark window.
- Warning banner (`_build_sensor_warnings()`, `ems_controller.py:768`) — for
  `solcast_today_entity` and `solcast_tomorrow_entity`.
- Warning banner, second block (`ems_controller.py:784`) — for
  `solcast_peak_time_today_entity` and `solcast_peak_time_tomorrow_entity`. These two
  sensors carry a timestamp as their state (the time of day the peak is expected), not a
  number — the presence check therefore uses `get_state_datetime()`, not
  `get_state_value()` (which would attempt `float(raw)` on an ISO string and always
  return `None`).

### Solcast data freshness — independent of any HA timestamp

Neither the age check nor the date check detects the case where the Solcast **API** is
unreachable (quota exhausted, internet gone, service disrupted) while the disk cache
keeps serving unchanged:

| Signal | Behaviour in this case | Detects the fault? |
|---|---|---|
| `unavailable` | cache populated → sensor stays `available` | ❌ |
| `last_updated` / `last_reported` | advance (values carried forward from cache + clock) | ❌ |
| Date check (`is_stale_daily`) | date rollover writes unconditionally → timestamp is from *today* | ❌ |
| Age check on the remaining-today forecast | curve carries on from cache + clock → age stays in the normal range | ❌ |

The way out: don't check the write timestamp of a derived sensor — check the **value** of
`solcast_last_fetch_entity`
(`sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf`), explicitly its content, not
its own `last_updated`. That value comes from the integration's persisted dataset and is
therefore exactly the age of the forecast data, independent of how often HA rewrites the
sensors (`solcastapi.py`, `SolcastApi.last_updated`).

```python
def _solcast_data_age_sec(self) -> float | None:      # ems_controller.py:644
    fetched = self._ws.get_state_datetime(cfg.solcast_last_fetch_entity)
    return (now_utc - fetched).total_seconds() if fetched else None

def _solcast_data_stale(self) -> bool:                  # ems_controller.py:658
    age = self._solcast_data_age_sec()
    return age is not None and age > SOLCAST_DATA_MAX_AGE_SEC
```

`SOLCAST_DATA_MAX_AGE_SEC = 30 h` is measured, not guessed: on the production system,
6/6/6/5/4 successful fetches per day over five consecutive days, longest legitimate
overnight gap 15.5 h (12:44 UTC → 04:11 UTC). Shorter winter days push that towards an
estimated ~19 h; 30 h leaves margin while still catching a stalled API within about a
day.

Without a configured `solcast_last_fetch_entity`, the check returns `None`/`False` — it
simply does not apply, rather than raising a false alarm.

Two places where it takes effect:

- `_forecast_remaining_kwh()` (`ems_controller.py:571`) returns `None` as soon as the
  Solcast data is stale — so no control decision (`_should_hold_pv_charge`,
  `_should_grid_charge`) rests on days-old forecast data.
- Warning banner: reports the age in hours once the threshold is exceeded.

!!! note "Why not the timestamp of `solcast_last_fetch_entity` itself?"
    Tempting but wrong: that entity's own `last_changed` is exactly as old as the
    forecast sensors it is supposed to vouch for — verified live, identical to the
    timestamp of "forecast today" at the same moment. Only its **value** works as a
    liveness proxy, not its own write timestamp.

### e: presence only, no age check

`battery_soc_entity` has had no age check since v2.0.2 — only a presence check
(`get_state_value(...) is None`). An age timeout would eventually be wrong for a coarse
percentage that can legitimately sit on the same value for hours — in winter with grid
charging disabled, even for days. The age check on `battery_power_entity` (same BMS
connection, fluctuates continuously as long as that connection is alive) implicitly
serves as the liveness proxy instead; see [Calculations](calculations.md).

## Every call site in the project

| # | Location | Entity checked | Constant / mechanism |
|---|---|---|---|
| 1 | `ems_controller.py:399` (`_decide`, SoC liveness proxy) | `battery_power_entity` | `is_stale()`, `sensor_max_age_sec` |
| 2 | `ems_controller.py:399` (`_decide`, PV surplus precondition) | `pv_power_entity`, `load_power_entity` | `is_stale()`, `sensor_max_age_sec` |
| 3 | `ems_controller.py:515` (`_should_grid_charge`) + warning banner | `electricity_price_entity` | `is_stale()`, `price_max_age_sec` (21,720 s) |
| 4 | `ems_controller.py:571` (`_forecast_remaining_kwh`) | `solcast_remaining_today_entity` | `is_stale()`, `forecast_max_age_sec` **+** `_solcast_data_stale()` |
| 5 | `ems_controller.py:543` (`_should_grid_charge`, tomorrow fallback) | `solcast_tomorrow_entity` | `is_stale_daily()` |
| 6 | `ems_controller.py:768` (warning banner) | `solcast_today_entity`, `solcast_tomorrow_entity` | `is_stale_daily()` |
| 7 | Generic `required` list (warning banner) | `pv_power`, `battery_power`, `grid_power`, `load_power` | `is_stale()`, `sensor_max_age_sec` |
| 8 | `ems_controller.py:784` (warning banner) | `solcast_peak_time_today_entity`, `solcast_peak_time_tomorrow_entity` | `is_stale_daily()`, presence via `get_state_datetime()` |
| 9 | Warning banner, global | `solcast_last_fetch_entity` (value, not timestamp) | `_solcast_data_stale()`, `SOLCAST_DATA_MAX_AGE_SEC` (30 h) |

Rows 8/9 feed no EMS decision at present — the two peak-power-time entities are polled
and checked for freshness in the warning banner, but (as of this page) are prepared for a
feature not yet implemented.

## Deliberately not implemented: plausibility check on the curve shape

An alternative to the date check would have been to check
`solcast_remaining_today_kwh` against its expected daily shape (reset at midnight, night
plateau at the daily maximum, then monotonically falling, exactly zero in the evening)
and treat a break in that pattern as a frozen/faulty sensor. Measured across two full
days (HA recorder, 356 data points), that pattern held, with three refinements over the
obvious formulation "zero at night, otherwise falling": the reset sits on local midnight
(not the morning), only the *evening* dark window is zero (the morning window sits at the
daily maximum until sunrise), and monotonicity only holds between forecast fetches.

Dropped because, once Solcast data freshness (`_solcast_data_stale()` above) exists, the
check no longer closes a gap: the curve shape is produced by the cached spline plus the
clock — with a days-dead API, the reset, night plateau, monotonic decay and evening zero
all remain intact, every criterion of the check would still pass. Its only benefit would
be pure detection speed (minutes instead of hours) for a frozen sensor — a rare failure
mode — against three additional false-alarm sources (midnight reset instead of morning,
the night-plateau exception, tolerance for intraday revisions). The ratio does not work
out.

## Related pages

- [Calculations](calculations.md) — where the staleness checks feed into the mode
  decision (fail-closed on a missing or stale sensor).
- [Grid-friendly charging roadmap](../../roadmap/energiefahrplan.md) (German) — the
  planned access to entity attributes (`activation_rules` of the price sensor) would
  allow a plausibility check against the actual tariff schedule, which does not exist
  today for lack of attribute access.
- [Tageswechsel & Energiezählung](../../roadmap/tageswechsel-energiezaehlung.md)
  (German) — the date check (`is_stale_daily`) relies on miniEMS's own day boundary
  reliably sitting on local midnight; measured and confirmed there
  (`TZ=Europe/Berlin`, cut observed live at `21:59:38 → 22:00:08 UTC`).
