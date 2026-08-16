---
revision_date: 2026-08-15
---

# Sensor Staleness — Current State and Improvement Proposals

!!! info "Status: assessment + proposals, partly implemented"
    This page describes the current state of staleness detection and five proposals.
    All measurements are from 13–16 Aug 2026 on the production system. Related:
    [Tageswechsel & Energiezählung](../../roadmap/tageswechsel-energiezaehlung.md)
    (German) — see [relation](#relation-to-the-day-rollover-roadmap) at the end of this page.

    | Proposal | State |
    |---|---|
    | 1 — date check for daily forecasts | **implemented in v2.0.4** |
    | 2 — `unavailable` for hardware | already in place, nothing to do |
    | 3 — price margin 6 h + 2 min | **implemented in v2.0.4** |
    | 4 — Solcast data freshness | open |
    | 5 — plausibility check on the curve shape | open |

!!! info "Trigger"
    Observed false alarm on the live system: the dashboard reports
    `solcast_pv_forecast_prognose_heute` as "stale" regardless of how old the value
    really is. Measured on the production system (18:14 UTC): `last_updated = 08:31:25`
    → age **9 h 43 min**, while the configured threshold (`forecast_max_age_sec`, 8 h)
    already classifies that as outdated. The cause is now **proven in the Solcast
    integration's source code** (see below) — this is not a configuration problem but a
    flaw in the checking method itself.

This is the **second** occurrence of this bug class in the project. The first
(`battery_soc_entity`, fixed in v2.0.2) is the precedent for the proposals below.

## Mechanism (in brief)

```
HAWebSocketClient._parse_ts(state)
  → prefers state["last_updated"], falls back to state["last_changed"]
  → stored in _state_ts[entity_id]

get_state_age_sec(entity_id)
  → now(UTC) − _state_ts[entity_id]   (None if never received)

is_stale(entity_id, max_age_sec)
  → age is None  OR  age > max_age_sec
```

Three constants cover **every** staleness check in the project (`const.py`):

| Constant | Value (current) | Intended for |
|---|---|---|
| `SENSOR_MAX_AGE_SEC` | 300 s (5 min) | Power sensors, SoC liveness proxy |
| `FORECAST_MAX_AGE_SEC` | 28,800 s (8 h, since v2.0.1) | Solcast sensors |
| `PRICE_MAX_AGE_SEC` | 21,600 s (6 h, since v2.0.1) | Electricity price |

**Dead feature:** `get_state_value(entity_id, max_age_sec=None)` has an optional
staleness parameter that is never passed a value at **any** call site in the codebase
(verified: `grep -n "get_state_value(" *.py`). Every actual staleness check goes through
the separate, explicit `is_stale()` calls listed below.

## Two obvious shortcuts — and why only one holds up

Before the numbers: two ideas suggest themselves when you see this problem. Both were
checked against the actual integration source on the production system. One does not
hold up at all; the other only halfway.

### Idea A: "Just use `last_reported` instead of `last_updated`"

Home Assistant maintains **three** timestamps per entity:

| Field | Advances when … |
|---|---|
| `last_changed` | the **value** changes |
| `last_updated` | value **or attributes** are written ← what miniEMS uses |
| `last_reported` | the entity **writes at all**, even with an identical value |

`last_reported` is in fact included in the REST JSON (verified live), so switching would
be trivial. **It still gains nothing here.** All three fields only advance when the
integration actually *calls* `async_write_ha_state()`. They measure "when did the
integration last write" — not "when did it last reach the source".

And for the daily forecasts, the Solcast integration deliberately does **not** write
(`custom_components/solcast_solar/sensor.py`, `_handle_coordinator_update`):

```python
if self._update_policy == SensorUpdatePolicy.DEFAULT and not (
        self._coordinator.date_changed or self._coordinator.data_updated):
    return          # ← no async_write_ha_state()
```

`get_sensor_update_policy()` (same file) grants `EVERY_TIME_INTERVAL` to only nine keys —
among them `ENTITY_FORECAST_REMAINING_TODAY` and `ENTITY_POWER_NOW`. Everything else,
including the "today" and "tomorrow" daily totals, gets `DEFAULT`.

Measurement on the production system (18:14 UTC), all sensors from **the same
integration and the same coordinator**:

| Sensor | `last_changed` = `last_updated` = `last_reported` | Age | Policy |
|---|---|---|---|
| `…_aktuelle_leistung` (power now) | 18:10:00 | 4 min | `EVERY_TIME_INTERVAL` |
| `…_prognose_verbleibende_leistung_heute` (remaining today) | 18:10:00 | 4 min | `EVERY_TIME_INTERVAL` |
| `…_prognose_heute` (forecast today) | **08:31:25** | **9 h 43 min** | `DEFAULT` |
| `…_prognose_morgen` (forecast tomorrow) | 08:31:25 | 9 h 43 min | `DEFAULT` |

The integration is demonstrably alive — it writes every five minutes. Only the daily
total is not rewritten, because nothing changed semantically. For `prognose_heute` all
three timestamps are **identical**: `last_reported` would reproduce the false alarm
exactly. The same holds for the price sensor
(`sensor.deye8k_current_electricity_price`: `last_changed` = `last_reported` = 16:00:00).

### Idea B: "Doesn't the sensor just go `unavailable` when the source is gone?"

`available` is a purely opt-in property of each integration. The two integrations
miniEMS depends on decide it in **opposite** ways:

**Solarman/Deye** (`custom_components/solarman/entity.py:38`) — the inverter:

```python
def available(self) -> bool:
    return self.coordinator.last_update_success and self.coordinator.device.state.value > -1
```

→ **Yes.** Modbus connection lost ⇒ `unavailable`. A reliable, immediate signal.

**Solcast** (`custom_components/solcast_solar/sensor.py:690`):

```python
def available(self) -> bool:
    return self._attr_available      # = (self._sensor_data is not None)
```

`_sensor_data` comes from the **forecast cache persisted to disk**, not from an API call.
API down, quota exhausted, internet gone — the cache stays populated, the sensor stays
`available` and serves yesterday's forecast indefinitely. → **No, it never goes
`unavailable`.**

The `unavailable` half is already implemented in miniEMS: `get_state_value()` returns
`None` for `unavailable`/`unknown`/`""` (`ha_ws_client.py:67`), and
`_build_sensor_warnings` turns that into "Sensor unavailable". For the Deye sensors this
is therefore *the* dependable signal — the age check there is essentially redundancy.

### What follows from both ideas

These are **two orthogonal failure modes** that today's age check conflates:

1. **Connection dead** → `unavailable`. Already solved, reliable for all hardware sensors.
2. **Connection alive, data semantically outdated** (Solcast forecast from yesterday) →
   neither `unavailable` nor any timestamp catches this. Only a **semantic** check helps.

## Current state — every call site in the project

| # | Location | Entity checked | Constant | Real update cadence (live + proven in integration source) | Verdict | Solution available |
|---|---|---|---|---|---|---|
| 1 | `ems_controller.py:358` (`_decide`, SoC liveness proxy) | `battery_power_entity` | `sensor_max_age_sec` (300 s) | continuous, ~15 s | ✅ appropriate (v2.0.2 fix — reference case) | ✅ |
| 2 | `ems_controller.py:376-377` (`_decide`, PV surplus precondition) | `pv_power_entity`, `load_power_entity` | `sensor_max_age_sec` (300 s) | continuous, ~15 s | ✅ appropriate | ✅ |
| 3 | `ems_controller.py:446` + warning banner | `electricity_price_entity` | `price_max_age_sec` (**21,720 s / 6 h + 2 min, since v2.0.4**) | time-driven schedule, changes only at tariff-tier boundaries; longest window **exactly 6 h** (06:00–12:00, `activation_rules`) | ✅ fixed — previously an exact tie (threshold == longest window) | ✅ implemented |
| 4 | `ems_controller.py:482` (`_forecast_remaining_kwh`) | `solcast_remaining_today_entity` | `forecast_max_age_sec` (28,800 s / 8 h) | policy `EVERY_TIME_INTERVAL` (5-min write cadence), but the **value** only moves while the curve runs: legitimately flat for **5 h 50 min** overnight (measured on two consecutive nights) | ❌ blind to the real failure mode: the value is carried forward from cache + clock and stays entirely plausible even with a days-dead API | ⚠️ proposal 5 detects integration failure in minutes rather than hours — data freshness stays open |
| 5 | `ems_controller.py:460` (`_should_grid_charge`, tomorrow fallback) | `solcast_tomorrow_entity` | `forecast_max_age_sec` (28,800 s / 8 h) | **only on forecast fetch or date rollover** — policy `DEFAULT` | ✅ fixed — date check instead of `forecast_max_age_sec` (v2.0.4); the v2.0.1 grid-charge fix now applies at night again | ⚠️ integration failure solved, data freshness still open (proposal 4) |
| 6 | `ems_controller.py:564` (warning banner) | `solcast_today_entity` | `forecast_max_age_sec` (28,800 s / 8 h) | **only on forecast fetch or date rollover** — policy `DEFAULT`; confirmed live as the cause of the reported false alarm (9 h 43 min) | ✅ fixed — date check instead of `forecast_max_age_sec` (v2.0.4) | ⚠️ integration failure solved, data freshness still open (proposal 4) |
| 7 | Generic `required` list (warning banner) | `pv_power`, `battery_power`, `grid_power`, `load_power` | `sensor_max_age_sec` (300 s) | continuous | ✅ appropriate | ✅ |

Legend for the last column: ✅ = both failure modes (connection dead / data outdated)
covered · ⚠️ = only one of the two · ❌ = no effective detection at all.

`battery_soc_entity` itself has had **no** age check since v2.0.2 — only a presence check
(`get_state_value(...) is None`). That is the already-solved instance of this bug class.

Worth noting: #4 and #6 come from **the same integration** and share the same constant
even though their cadences have nothing in common — it fits #4 (5 h 50 min measured flat
period against an 8 h threshold) while being too tight for #6. That one and the same
value fits one sensor and produces false alarms on another from the same integration is
demonstration enough that "age" cannot be the load-bearing measure here.

!!! danger "Two corrections to earlier versions of this page"
    **(1)** #4 was initially classified as "✅ appropriate, large margin". That obscures
    the fact that the check cannot see the actual failure mode. #4 is the only Solcast
    value that feeds `_should_grid_charge` directly.

    **(2)** This page then claimed the check "can structurally never fire, because the
    sensor is rewritten every 5 minutes". That was also wrong. The five-minute cadence is
    the *write* cadence; `last_updated` only advances on an actual **value change**.
    Measured across two nights, the value legitimately sits still for **5 h 50 min**
    (22:00→03:50 UTC, identical on both nights) — so the 8 h constant has roughly 2 h of
    margin and is perfectly effective against integration failure, merely very slow. It
    is blind only to outdated *data*.

!!! note "How much does #5 actually matter? — reachability of the tomorrow branch"
    The affected branch in `_should_grid_charge` is only reached when **both** hold: cheap
    tariff **and** (`remaining` missing **or** `remaining ≤ 1.0 kWh`). That combination is
    rarer than it first appears:

    - **Summer, healthy forecast sensor:** `remaining ≤ 1.0 kWh` only occurs from about
      20:15 local onwards (measured), while the cheap windows are 02:00–06:00 and
      12:00–16:00. The conditions **never** overlap — the branch is unreachable and the
      bug is inconsequential.
    - **Winter:** between 02:00 and 06:00, `remaining` holds the *daily* forecast of the
      new day (reset at local midnight). On an overcast December day that can fall below
      1.0 kWh ⇒ branch reached, and 02:00–06:00 lies inside the dark window (21:00–06:00)
      ⇒ it charges. `prognose_morgen` is then guaranteed stale (last fetch the previous
      day, ~17.5 h), so the "tomorrow will refill it" guard is skipped. **This is where the
      bug costs money — precisely when grid charging matters at all.**
    - **Forecast sensor outage:** `remaining is None` satisfies the condition too, at **any**
      time of day. If Solcast fails during a cheap window, the branch becomes reachable in
      summer as well.

    In the 12:00–16:00 cheap window the dark-window test correctly prevents charging,
    regardless of forecast state.

!!! tip "Related finding outside the staleness logic"
    `_should_grid_charge` has **no hysteresis** — the return in `ems_controller.py:475` is
    a bare threshold comparison (`bat_kwh_free > remaining × 1.2 + 1.0`). Its only damping
    is the `mode_dwell_sec` dwell time (300 s) in `_commit()`. The PV path, by contrast,
    has an asymmetric hysteresis via `pv_charge_hysteresis_frac` that fully absorbs the
    measured forecast steps (+0.35 / +0.13 kWh, see proposal 5). Since `remaining` moves by
    up to 0.47 kWh per 5-minute interval during the day, a battery sitting near the
    threshold can oscillate in the grid-charge path. Not a staleness problem, but the same
    root cause — jumpy forecast data — and noted here for that reason.

## Root cause

Three constants effectively cover **five** distinct update-cadence classes:

| Class | Example | Real cadence | Matching constant? |
|---|---|---|---|
| a) continuous | power sensors | seconds | `SENSOR_MAX_AGE_SEC` — yes |
| b) fixed write cadence, value-driven | Solcast "remaining", "power now" | 5 min during the day, up to 5 h 50 min without a value change overnight | `FORECAST_MAX_AGE_SEC` — yes, ~2 h margin |
| c) several times a day, fixed points | electricity price (ToU tariff) | up to exactly 6 h, schedule fixed | `PRICE_MAX_AGE_SEC` — right magnitude, just no margin |
| d) event-driven, ~1×/day | Solcast "today"/"tomorrow" (totals) | only on fetch/date rollover | **no constant of its own** — shares (b), both wrong |
| e) practically never | SoC, battery capacity | hours to days, legitimately | no age check needed (already fixed) |

The deeper cause, however, is not the constants. For class (d) **no** age limit can be
structurally correct: the same forecast value is fresh at 09:00 and still correct at
23:00 — the timestamp's age says nothing about validity in either case. Raising the
threshold merely postpones the problem.

## Improvement proposals

### 1. Daily forecasts: check the date, not the age ✅

!!! success "Implemented in v2.0.4"
    `HAWebSocketClient.is_stale_daily(entity_id, grace_sec)` asks "was it written
    today?" instead of "how old is it?". Used at both sites: the tomorrow fallback
    in `_should_grid_charge` (#5) and the warning banner (#6, which now also covers
    `solcast_tomorrow_entity`). The grace period `DAILY_VALUE_GRACE_SEC = 900`
    covers the minutes after midnight in which Solcast has not rewritten yet.

    The comparison runs in **local** time on purpose: the source rolls the day over
    in the installation's timezone while `_parse_ts` stores UTC. Under CEST a UTC
    comparison would be wrong every night between 22:00 and 00:00 UTC.


Instead of asking "*how old* is the timestamp?", ask the semantically correct question:
**"Is the timestamp from today?"**

For class (d) this is answerable exactly, because the Solcast integration is guaranteed
to write on every date rollover (`coordinator.py`):

```python
self.tasks[TASK_LISTENERS] = async_track_utc_time_change(
    self.hass, self._update_integration_listeners, minute=range(0, 60, 5), second=0
)
```

`_update_integration_listeners` therefore runs **every five minutes** and sets:

```python
current_day = dt.now(self.solcast.options.tz).day
self._date_changed = current_day != self._last_day
```

When `date_changed` is true, the early-return condition in `_handle_coordinator_update`
no longer applies and **all** `DEFAULT`-policy sensors are rewritten. This yields a hard
guarantee:

> As long as the Solcast integration is running, `prognose_heute` and `prognose_morgen`
> carry a timestamp from the **current day** within five minutes of local midnight. A
> timestamp from yesterday necessarily means the integration has stopped.

This makes the check self-calibrating: no new constant, no new config field, no
additional entity, and it stays correct across year boundaries and DST changes.

Two implementation notes:

- **Use the local timezone.** Solcast evaluates the date rollover in local time
  (`dt.now(self.solcast.options.tz)`), whereas `_parse_ts()` returns UTC. Under CEST
  (UTC+2), local midnight is 22:00 UTC the previous day — a date comparison in UTC would
  be wrong for two hours. The comparison must happen in the HA installation's local
  timezone.
- **Small grace period.** Between 00:00 and 00:05 local time the timestamp is still
  legitimately from yesterday. A tolerance of ~15 minutes after midnight avoids a daily
  false alarm inside that five-minute window.

Affects #5 and #6. Resolves the reported false alarm and closes the gap in the v2.0.1
grid-charge fallback.

!!! warning "Scope of this proposal"
    The date check answers "**is the integration running today?**" — not "**how old is
    the forecast data?**". It does not cover the second case; see proposal 4.

### 2. Hardware sensors: `unavailable` is already the right signal

For #1, #2 and #7 the Solarman integration reports `unavailable` on connection loss —
faster and more reliable than any age check, and already evaluated in
`get_state_value()`. The 300-second check there is not wrong, but largely redundant; it
can remain as a second line of defence.

### 3. Electricity price: no semantic equivalent — a small margin suffices ✅

!!! success "Implemented in v2.0.4"
    `PRICE_MAX_AGE_SEC = 21720` in `const.py`, plus a `_v13_to_v14()` migration that
    raises existing configurations sitting on **exactly** the old default of 21600. A
    differing value — i.e. one deliberately set — is left alone. Verified live on the
    devcontainer:
    `Migration v13→v14: price_max_age_sec was exactly the longest tariff window
    (21600s/6h) – raised to 21720s for clearance`.


For #3 there is neither a date reference nor a liveness proxy: the price sensor is
written only on tier changes (`last_changed` = `last_reported`, confirmed live).

The upper bound is, however, **known exactly**: the tariff is purely time-driven, the
schedule is fixed, and the longest window is exactly 6 h (06:00–12:00). No market
dynamics can change that. So no generous buffer is needed — only enough clearance to
avoid the "threshold == longest window" exact tie:

```python
PRICE_MAX_AGE_SEC = 21720   # 6 h + 2 min – longest tariff window is exactly 6 h
```

A higher value would only degrade detection time without covering any real case.

The cleaner solution would be a plausibility check against the entity's
`activation_rules` schedule — that schedule is present and describes the tariff profile
completely. It requires access to entity attributes, which miniEMS currently has nowhere;
this is a touchpoint with the
[grid-friendly charging roadmap](../../roadmap/netzdienliches-laden.md), which proposes
exactly that access as its first step.

### 4. Unsolved: Solcast data freshness (#4, #5, #6)

For the three Solcast checks, only the "integration dead" failure mode is addressed so
far. There is a **second** one that none of the signals above detects:

> The Solcast **API** is unreachable (quota exhausted, internet gone, service disrupted)
> **while the disk cache keeps serving unchanged.**

In that state everything looks healthy:

| Signal | Behaviour in this case | Detects the fault? |
|---|---|---|
| `unavailable` | cache populated → sensor stays `available` | ❌ |
| `last_updated` / `last_reported` | advance | ❌ |
| Date check (proposal 1) | date rollover writes unconditionally → timestamp is from *today* | ❌ |
| Age check on #4 | curve carries on from cache + clock → age stays in the normal range | ❌ |
| Plausibility check (proposal 5) | curve shape remains entirely plausible | ❌ |

The most serious case is **#4**, because that value feeds `_should_grid_charge` directly:
the grid-charge decision can run on a days-old forecast without any warning appearing.
Only once the cache no longer holds data for the current day — after roughly seven days —
does `_sensor_data` become `None` and the sensor flip to `unavailable`, at which point
the existing presence check applies. The days before that are blind.

**Candidate for the gap:** the **value** of
`sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` — explicitly its content, not its
timestamp (see the box below). Proven in the source (`solcastapi.py:561`):

```python
@property
def last_updated(self) -> dt | None:
    """When the data was last updated.

    Returns:
        dt | None: The last successful forecast fetch.
    """
    return self.data[LAST_UPDATED].astimezone(self.tz) if self.data.get(LAST_UPDATED) is not None else None
```

The value comes from the persisted dataset and is therefore exactly the age of the
forecast data, independent of how often HA rewrites the sensors. Verified live:
`2026-08-15T08:31:25` at a measurement time of 18:14 UTC ⇒ forecast data **9 h 43 min**
old.

Implementation obstacle: this is a datetime sensor. `get_state_value()` does `float(raw)`
and returns `None` for an ISO timestamp (`ha_ws_client.py:69-72`) — it would need a
datetime path in the client plus a config field for the entity, analogous to
`load_consumption_entity` in v2.0.3. A sensible threshold should be derived from the
actual fetch rhythm rather than guessed; that measurement, across several days, is still
missing.

### 5. Plausibility check on the curve shape (#4)

Instead of asking *when* the value last arrived, check whether it **behaves the way it
must**. `remaining_today` has a mandatory daily shape: reset once, then decay to zero.

Measured across two full days (HA recorder, 356 data points, 13–15 Aug 2026):

| Phase | Observation (UTC / local = UTC+2) | Duration |
|---|---|---|
| Reset | 22:00 / **00:00 local**: 0 → 44.37 and 0 → 33.68 respectively | once daily |
| Night plateau | 22:00 → 03:50, value constant at the daily maximum | **5 h 50 min** (identical both nights) |
| Daytime decay | 03:50 → 19:00, monotonically falling, smooth | ~15 h |
| Evening zero | 19:00 / 21:00 local: exactly 0, constant thereafter | 3 h until reset |
| Upward steps | 14 Aug 04:19 (+0.3466) and 08:30 (+0.1271) — both at forecast fetch moments, off the 5-minute grid | 2× on one day, 0× on the other |

That yields three refinements to the obvious formulation "zero at night, otherwise
falling, one rise in the morning":

1. **The rise sits on local midnight, not in the morning.** It is the date rollover, not
   sunrise.
2. **Only the *evening* dark window is zero.** In the pre-dawn hours the value sits at its
   daily *maximum*, constant for 5 h 50 min — a rule "dark ⇒ 0" would raise a false alarm
   every night between 00:00 and 05:50 local.
3. **Monotonicity only holds between forecast fetches.** Solcast revises intraday; the
   measured steps were small at +0.35 and +0.13 kWh, but they are systematic and should be
   expected to be considerably larger on a changeable day. A strict monotonicity rule
   produces false alarms; it needs a tolerance, or an exemption at fetch moments (known
   from the value in proposal 4).

**What the check delivers:** it detects a frozen, implausible or miswired sensor during
daylight within **minutes** instead of eight hours — over the course of the day the value
moves every 5 minutes by amounts far above the noise floor (up to 0.47 kWh per interval
measured). That is a genuine improvement in detection latency over the pure age check, and
it covers faults none of the other measures can see.

**What it does not deliver:** it does **not** close the gap from proposal 4. The curve
shape is produced by the cached spline plus the clock — with a days-dead API, the reset,
night plateau, monotonic decay and evening zero all remain intact. Every single criterion
of this check would pass. For data freshness, the value of
`zeitpunkt_letzter_api_abruf` remains the only dependable signal.

!!! warning "Corrected proposal"
    An earlier version of this page recommended
    `sensor.solcast_pv_forecast_zeitpunkt_letzter_api_abruf` as a liveness-proxy sensor
    for Solcast — analogous to the `battery_power_entity` trick used for SoC. **That was
    wrong.** Checked live, this entity reads `2026-08-15T08:31:25`, and its own
    `last_changed` is likewise `08:31:25` — exactly as old as `prognose_heute` itself.
    Useless as a proxy. (Side finding: `verwendete_api_abrufe` last incremented at 10:36,
    so the fetch timestamp even contradicts the integration's own counter.)

    The entity is not worthless, though — only its role was misidentified: its
    **timestamp** is useless as a liveness proxy, whereas its **value** is precisely the
    data age that proposal 4 is missing. Confusing timestamp with content is the actual
    essence of this bug class — and it caught me out here too.

## Not part of this page

This page describes the **current state and proposals** only — none of the points above
are implemented. The actual implementation (date check for daily forecasts, price
margin) is a separate step.

Explicitly **still without a finished solution** is proposal 4 (Solcast data freshness,
#4/#5/#6): the signal source is identified and proven in the integration source, but the
client extension, the config field and an empirically grounded threshold are all missing.

Proposal 5 (plausibility check) and proposal 1 (date check) each improve detection of
**integration failure** — proposal 5 markedly so in speed. Neither covers **data
freshness**; that remains the one open point of this page, and it affects #4 of all
things, the only Solcast value with direct influence on a control decision.

## Relation to the day-rollover roadmap

While reviewing this page a second defect surfaced, covered in
[Tageswechsel & Energiezählung](../../roadmap/tageswechsel-energiezaehlung.md) (German):
miniEMS cuts the day 4 min 54 s ahead of the inverter. The two pages are more closely
related than the topics suggest.

### What the day-rollover rework does **not** fix here

The delimitation first, so no false expectation arises: switching to total counters with a
self-computed delta fixes **not one** row of the table above. The Solcast sensors have no
total counterpart, and the price and power sensors are untouched by energy accounting. The
two efforts are independently implementable.

### What it contributes nonetheless

**1. Proposal 1 rests on a precondition proven there.** The date check only works if
miniEMS's own day boundary reliably sits on **local** midnight — otherwise the Solcast date
would be compared against a shifted boundary. That is exactly what the day-rollover page
measures and confirms (`TZ=Europe/Berlin` in the container, cut observed live at
`21:59:38 → 22:00:08 UTC`). The precondition therefore need not be re-derived here — but if
anything changes about the day boundary there, proposal 1 must be re-checked.

**2. The same figure of thought, twice.** Both pages arrive independently at the same rule:

> Do not trust the derived artifact — move one level closer to the raw source and compute
> the quantity you need yourself.

There: don't inherit the foreign day cut, read the monotonic lifetime counter and compute
the daily delta yourself. Here: don't check the write timestamp of the derived sensor, read
the source's own fetch time (proposal 4). Understand one and you understand the other.

**3. The monotonicity safeguard generalises.** The day-rollover page requires of the total
counter: *a monotonically increasing value must never run backwards — if it does, that is a
reportable event, not a computation case.* The same discipline applies to
`zeitpunkt_letzter_api_abruf` once proposal 4 is implemented: its value is monotonically
increasing too, and a backwards jump would be an integration defect, not a number to
interpret.

**4. Coordination during implementation.** Both efforts need new config fields and thus a
schema migration (`migration.py`, `CONFIG_SCHEMA_VERSION`). If they land in the same
release, they should share **one** schema version rather than two consecutive migration
steps. Both would additionally touch `config_loader.py` and `templates/settings.html`.
