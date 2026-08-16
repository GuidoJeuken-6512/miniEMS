<!-- https://developers.home-assistant.io/docs/add-ons/presentation#keeping-a-changelog -->

## 2.0.4

### Bug Fixes

- **Every inverter write was reported as failed.** `_call_service` used a 10s
  HTTP timeout, but HA's REST `/api/services/…` blocks until the service has
  *finished* – and a Solarman/Modbus write travels to the inverter and waits
  for its acknowledgement, which regularly takes longer. The resulting
  `asyncio.TimeoutError` was caught as a generic failure, so `write_errors`
  climbed on writes that HA had in fact applied. Measured live on the
  production system: six "write FAILED" entries in six minutes, while the
  entity states matched the written targets exactly. The errors fired 25.2s
  to 25.7s after each preceding tick, i.e. 10.2s to 10.7s into the call – an
  exact fingerprint of the timeout.

  A timeout is now distinguished from a rejection: `_call_service` returns
  `None` for "outcome unknown" instead of `False`, and only an actual non-2xx
  response counts as a write error. The existing state-based confirm/retry
  logic decides the outcome one tick later, which it was already designed to
  do. The timeout itself was raised from 10s to
  `INVERTER_SERVICE_CALL_TIMEOUT_SEC` (15s).
- **`Service call error …:` logged an empty reason.** `str()` on a
  `TimeoutError` is the empty string, so the line ended in a bare colon.
  Exceptions now log `type(exc).__name__` as well, matching the pattern
  already used in `ha_ws_client.py`.

- **The day was cut on the inverter's clock, not ours.** `grid_import_kwh`,
  `feed_in_kwh` and `load_total_kwh` took their daily value straight from the
  inverter's daily counters – but those reset on the *inverter's* clock.
  Measured across all four counters simultaneously on the production system,
  that is **4min54s after local midnight**, and during that window the daily
  sensor still reports yesterday's closing total. The Source-A override wrote
  it into the new day: 45.9 kWh of feed-in, including its revenue, for almost
  five minutes. It self-corrected, but kWh and cost were accumulated over
  windows offset by those minutes, and a clock skew in the *opposite*
  direction would have written a 0 into yesterday and persisted it as that
  day's closing total.

  miniEMS now derives the daily figure from the inverter's monotonic lifetime
  counters (`grid_import_total_entity`, `feed_in_total_entity`,
  `load_consumption_total_entity`), anchored at its own local midnight and
  persisted in `daily_stats`. Verified to carry the same 0.1 kWh resolution as
  the daily counters and to run continuously across midnight. The daily
  sensors stay in use as the anchor bootstrap after a mid-day restart, so no
  energy is lost when the add-on starts up at noon. A lifetime counter that
  runs backwards (firmware update, device swap, Modbus glitch) is re-anchored
  and logged as a warning rather than producing a negative delta.

- **The daily solar forecasts were flagged stale every single day.**
  `solcast_today_entity` and `solcast_tomorrow_entity` were checked against
  `forecast_max_age_sec` (8h) — a threshold tuned for the fast-moving
  "remaining today" sensor. But the daily totals are written only on a
  forecast fetch or at the date rollover, so they crossed 8h on any day
  Solcast fetched early. Measured live: 9h43min old at 18:14 UTC.

  Worse than the false banner, `solcast_tomorrow_entity` gates the v2.0.1
  grid-charge fallback ("tomorrow's sun will refill the battery, don't buy
  grid energy tonight"). Counted as stale, that fallback silently switched
  itself off for the entire dark charging window, every night.

  Both now use `HAWebSocketClient.is_stale_daily()`, which asks *was it
  written today?* instead of *how old is it?* — exact for a value the source
  rewrites at every date rollover, where no age threshold can be right (the
  same forecast is fresh at 09:00 and still correct at 23:00). The comparison
  runs in local time, since the source rolls the day over in the
  installation's timezone while timestamps are stored as UTC.
  `DAILY_VALUE_GRACE_SEC` (15 min) covers the minutes after midnight before
  the source has rewritten.

### Improvements

- **The tick log now shows a pending mode change.** A debounced transition
  waits out `mode_dwell_sec` before it is applied, but `_mode_reason` keeps
  the *old* reason while it waits – so a correct, merely-debounced transition
  was indistinguishable from a stuck EMS. The tick line gains a
  `→ Export Surplus pending 120/300s` suffix while a change is in flight.

## 2.0.3

### New Features

- **`load_consumption_entity`** – optional inverter daily-total sensor
  (default `sensor.deye8k_today_load_consumption`) as a "Source A" for
  `today_load_total_kwh`, matching the existing pattern for grid import and
  feed-in. Migration v13 enables it by default for upgrading installs.

### Bug Fixes

- **`today_load_total_kwh` had no hardware-counter anchor.** Unlike grid
  import and feed-in, it was purely tick-accumulated and therefore
  permanently under-counted across every add-on restart – measured live:
  ~1.1 kWh (≈23%) low after a single restart. Fixed by the new
  `load_consumption_entity` above; `today_load_cost_eur` still accumulates
  per tick as before, since it needs the price at each interval.
- **Migration v11→v12 baked in stale staleness defaults.** It hardcoded
  `forecast_max_age_sec`/`price_max_age_sec` at the pre-v2.0.1 values
  (10800s/3h each), so any config that passed through it before v2.0.1
  raised those defaults kept the old, too-tight numbers permanently in
  `config.json`, immune to the later default change. Migration v13 corrects
  both if they still exactly match the old hardcoded default; an explicitly
  customized value is left alone.
- **The custom integration's reload call has never worked.** `GET
  .../config/config_entries` is not a real Home Assistant REST endpoint
  (config-entry listing/reload is WebSocket-only) – it always returned 404,
  so the integration was silently never reloaded after an add-on update.
  This is also why a renamed sensor `key` (e.g. a past typo fix) could
  leave a permanently orphaned, "unavailable" entity behind that later
  collided with the freshly-registered one and forced a "_2" suffix onto
  it, instead of the clean name. Fixed by calling the
  `homeassistant.reload_config_entry` *service* instead (a real REST
  endpoint), targeted at a miniEMS entity resolved via a Jinja template
  rather than a hardcoded name – even a seemingly stable `key` has turned
  out to have older, pre-repository renames of its own on a real
  installation.
- **Orphaned same-config-entry entities were never cleaned up.** The
  existing registry cleanup only removed entities left behind by a fully
  deleted-and-re-added config entry; it did nothing for a renamed sensor
  `key` within the *same* config entry, which is the actual cause of the
  observed "_2" sensors. `async_setup_entry` now also removes any
  registered miniEMS entity whose `unique_id` is no longer produced by the
  current sensor descriptions.

### Documentation

- Corrected `battery_power_entity`'s sign convention in the configuration
  reference (was documented backwards: **negative** = charging, **positive**
  = discharging, verified live against the inverter).
- New page **Sensor Staleness** (`roadmap/sensor-staleness.md`): every
  `is_stale()` call site in the project, its real-world update cadence, and
  a verdict, with a "solution available" column. Root cause proven in the
  Solcast integration's source: `_handle_coordinator_update` returns early
  for `DEFAULT`-policy sensors, so the daily forecast totals are never
  rewritten and *no* HA timestamp – `last_updated`, `last_changed` or
  `last_reported` – can advance. Live-reproduced: `solcast_today_entity`
  was 9h43min old against an 8h threshold. Also documents why
  `unavailable` cannot substitute for the check (Solarman reports it on
  connection loss, Solcast never does – it serves its disk cache instead).
  Five proposals, none implemented; the recommended one is a date check
  ("is the timestamp from today?") rather than any age threshold.
- New page **Tageswechsel & Energiezählung**
  (`roadmap/tageswechsel-energiezaehlung.md`): the inverter resets its
  daily counters 4min54s *after* local midnight (measured across all four
  counters simultaneously), so for those minutes the Source-A override
  writes yesterday's total into today's bucket. Proposes switching to the
  lifetime `total_*` counters with a self-computed daily delta – verified
  to carry the same 0.1 kWh resolution and to be continuous across
  midnight. Not implemented.
- New page **Costs & Savings** (`user/costs.md`): every cost/savings value
  explained with a worked example, including the two-loss-sensor pitfall
  (`today_losses_entity` vs. the differently-named `loss_daily` helper).

## 2.0.2

### Bug Fixes

- **Write confirmation falsely fired in simulation mode.** The confirm/retry
  logic introduced in v2.0.1 compared the (never-written) target against the
  *real* HA state even with `battery_control_simulation` on, so it could
  never match – every write sat permanently "unconfirmed" and was re-logged
  every tick. Simulated writes are now confirmed immediately after logging,
  matching pre-v2.0.1 behaviour (log once per target change, nothing to
  confirm).
- **SoC "unavailable" fallback used the wrong staleness signal.** The core
  safety check ("no usable SoC → hand control back to the inverter") tested
  `battery_soc_entity`'s own last-updated timestamp against
  `sensor_max_age_sec` (5 min) — but SoC is a coarse percentage that can
  legitimately hold the exact same value for hours, or for days in winter
  with grid charging off, so no fixed timeout on "time since it last changed"
  can ever be correct. The check now uses `battery_power_entity`'s freshness
  instead: it comes from the same BMS/inverter connection and fluctuates
  continuously whenever that connection is alive, so it actually distinguishes
  a dead link from a battery that is simply, legitimately, not changing.

## 2.0.1

### Bug Fixes

- **Grid charging ignored tomorrow's PV forecast.** When today's Solcast
  remaining-forecast was missing, stale, or simply spent (evening — no more
  sun coming), `_should_grid_charge` fell straight back to a blind
  time-of-day check, even when tomorrow's forecast (`solcast_tomorrow_entity`)
  was already known and more than large enough to refill the battery for
  free. The decision now checks `solcast_tomorrow_kwh` first and skips grid
  charging if it alone covers the battery's need; the dark-window fallback
  only applies when neither day has a usable value. Purely restrictive — can
  only prevent a charge, never trigger one, and leaves every existing safety
  gate (SoC floor, `PROTECT_BATTERY`) untouched.
- **Solcast/price staleness thresholds were tighter than reality.**
  `forecast_max_age_sec` (3 h → 8 h) and `price_max_age_sec` (3 h → 6 h) were
  short enough that a Solcast forecast or tariff price that simply hadn't
  needed to change yet (e.g. overnight, when Solcast may not poll again for
  several hours) was misread as "stale" and fell back to less accurate
  decision paths, or produced false dashboard warnings.
- **Inverter writes were assumed applied on HTTP 200.** A service call HA
  accepts is not proof the inverter (or the Deye/Solarman bridge in between)
  actually applied it — confirmed writes lagging by up to ~25 minutes, and
  in one case a grid-charge switch toggle that never landed at all despite
  being logged as sent. `InverterController` now re-checks the real HA state
  against the target on every tick and keeps resending until it matches,
  independently for charge current, discharge current and the grid-charge
  switch. A new `write_unconfirmed` counter (0–3, live) surfaces this as a
  dashboard warning, separate from `write_errors` (outright HTTP failures).

## 2.0.0

### Breaking

- **Battery limits are now current (A), not power (W).** The Deye inverter has
  no charge/discharge *power* entity — it exposes current limits
  (`number.deye8k_battery_max_charging_current`, 0–350 A). The old watt-based
  settings wrote to entities that do not exist, so those writes silently did
  nothing. Config migration **v11** renames the fields, repoints the entities and
  resets the limits to the inverter's rated 185 A (old watt values cannot be
  converted without the battery voltage).
  - `inverter_charge_power_entity` → `inverter_charge_current_entity`
  - `battery_discharging_power_entity` → `battery_discharging_current_entity`
  - `battery_max_charge_power_w` → `battery_max_charge_current_a`
  - `battery_max_discharge_power_w` → `battery_max_discharge_current_a`
  - `default_discharge_power_w` removed (its 185 was the ampere limit, never watts)
- New operating mode `Export Surplus`. Automations matching
  `sensor.miniems_mode == "Idle"` will now see this value during the day.

### New Features

- **Grid-friendly PV charging** (config migration **v12**, off by default via
  `pv_export_priority_enabled`). On PV surplus the energy is exported first;
  the battery only starts charging once the remaining PV forecast has fallen to
  roughly what the battery still needs (`pv_charge_margin_factor`, default 1.2).
  This shaves the midday feed-in peak. Honours `battery_control_simulation`.
  - Guards, all failing *closed* so the battery can never be stranded empty:
    SoC floor (`pv_export_min_soc_pct`), hard time backstop
    (`pv_charge_backstop_hour`), and missing/stale forecast → charge normally.
  - Anti-flapping: asymmetric hysteresis (`pv_charge_hysteresis_frac`) plus a
    dwell time (`mode_dwell_sec`); safety transitions bypass the dwell.
- **Sensor staleness detection.** A frozen sensor (e.g. Solcast after its API
  quota is exhausted) no longer counts as a live reading; it is surfaced as a
  dashboard warning and excluded from control decisions.

### Bug Fixes

- Failed inverter writes were recorded as successful, which suppressed all
  retries and reported the intended value as if it were live. Service calls are
  now checked; `*_target_a` (intended) and `*_limit_a` (confirmed) are separate,
  and failures are counted and surfaced as a warning.
- The grid-charge switch was written on **every** tick (~2880 service calls/day).
  Commands are now deduplicated against the confirmed state.
- Grid charging no longer happens just because the forecast is missing or the
  prediction is unconfident (previously fail-open). Without a forecast, grid
  charging is limited to the configurable dark window.
- The battery could not discharge in normal operation: `Idle` wrote a 185 limit
  to a non-existent entity. `Idle` and `PV Charging` now set the full limit.
- Dashboard showed `–` for *Saved Today*, *Saved This Week* and *Cost at Fix
  Price* — the template read three key names the backend never emitted.
- An unavailable load sensor was read as 0 W, producing phantom PV surplus and
  spurious charging. Control now distinguishes "no reading" from zero.
- `daily_base_price_eur` and `avg_discharge_tariff_eur_kwh` were saved as
  strings by the settings page, raising `TypeError` on first use.
- A non-numeric `_version` in `config.json` crashed startup in a loop.
- The inverter is reset to safe defaults on shutdown, so a restricted charge
  limit is never left behind.

## 1.3.0

### New Features
- **Settings page** in the miniEMS dashboard: all config options are editable
  in the browser; *Save & Restart* writes `/data/config.json` and triggers an
  addon restart via the Supervisor API.
- **Forecast & Prediction** (Phase 3):
  - `weather_client.py`: fetches 24 h OpenWeatherMap forecast (8 × 3 h slots),
    derives average night temperature, PV yield factor and day length.
    Cache TTL: 3 hours.
  - `consumption_model.py`: predicts today's load (temperature-matched
    historical days → 30-day median fallback) and PV yield (75th-percentile
    peak PV × cloud factor × daylight hours).
  - Smart grid-charge gating: `GRID_CHARGING` mode is only triggered when the
    model recommends it (`battery + predicted_pv < predicted_load`).
    Falls back to always-charge during cheap rates when not enough history
    exists (`confidence = none`).
  - SQLite: new `peak_pv_w` and `avg_outdoor_temp_c` columns in `daily_stats`.
- **2 new HA sensors**: `sensor.miniems_predicted_load_kwh`,
  `sensor.miniems_predicted_pv_kwh` (MQTT + REST fallback).
- **Confidence badge** on dashboard: `high` / `low` / `none`.
- **SIM badge** on dashboard mode indicator when battery control is in
  simulation mode.

### Config additions
- `outdoor_temp_entity` – optional HA temperature sensor for historical matching
- `openweathermap_api_key` – OWM API key (leave empty to disable forecast)
- `openweathermap_lat` / `openweathermap_lon` – location for forecast queries

### Schema migration
Config schema v2 → v3 (adds 4 new forecast fields with safe defaults).

---

## 1.2.0

### New Features
- **Battery control** (Phase 2): addon actively controls the Deye inverter via
  HA service calls based on EMS mode.
  - Sets inverter work mode, max charge power, and max discharge power.
  - **Simulation mode** (`battery_control_simulation: true`, default): all
    commands are logged as `[SIM]` but not executed. Safe for testing.
  - **Idempotent**: service calls only sent when value actually changes.
- **4 new config fields**: `battery_control_enabled`,
  `battery_control_simulation`, `battery_max_charge_power_w`,
  `battery_max_discharge_power_w`.
- **5 new entity config fields**: `inverter_work_mode_entity`,
  `inverter_charge_power_entity`, `inverter_discharge_power_entity`,
  `inverter_charge_mode_charge`, `inverter_charge_mode_selfuse`.
- **2 new HA sensors**: `sensor.miniems_charge_power_limit_w`,
  `sensor.miniems_discharge_power_limit_w` (MQTT + REST).

### Schema migration
Config schema v1 → v2 (adds battery control fields with safe defaults).

---

## 1.1.0

### New Features
- **MQTT Discovery**: sensors published via MQTT with `unique_id`, device
  grouping and long-term statistics support. Falls back to REST when Mosquitto
  is not installed.
- **SQLite persistence** (`/data/miniems.db`): daily stats survive restarts.
  Running totals restored from DB on startup — values no longer reset to 0.
- **7 new HA sensors**: `today_load_total_kwh`, `today_load_cost_eur`,
  `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur`,
  `year_grid_cost_eur`, `year_pv_savings_eur`.
- VS Code task "Update and Start Addon" for fast code-only redeployment.

### Bugfixes
- Entity IDs were doubling the device name prefix
  (`sensor.miniems_miniems_…`) — fixed by using short sensor names in MQTT
  Discovery and relying on HA's device-name prepending.

---

## 1.0.1

### Bugfixes
- Rebuild / Update VS Code tasks now patch `homeassistant_api` and
  `services: mqtt:need` into the supervisor in-memory cache to work around
  a supervisor bug in dev builds.

---

## 1.0.0

- Initial release of miniEMS
- WebSocket client for live HA entity states (no polling)
- EMS decision logic: Idle / PV Charging / Grid Charging / Battery Protection
- Cost accounting: grid import cost, PV savings, grid import kWh, PV used kWh
- Weekly aggregated cost/savings sensors
- FastAPI ingress dashboard with auto-refresh
- Config persistence with schema migration (`options.json` → `config.json`)
