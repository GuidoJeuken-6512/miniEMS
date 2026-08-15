---
revision_date: 2026-08-15
---

# Costs & Savings

This page explains every cost and savings value miniEMS exposes on the dashboard and as
a Home Assistant sensor: what it means, how it's calculated, and a worked example. For
the plain entity/unit reference see [HA Sensors](sensors.md); for the full formulas with
code references see the "Costs & Savings" section in [Calculations](../technical/calculations.md).

## How the values come to be

All cost and energy values are accumulated by `CostOptimizer` a little more on **every
EMS tick** (30 seconds by default) — never recalculated once at day's end. Two
consequences worth knowing when reading the values:

- **Daily values grow over the course of the day** and reset to 0 at midnight.
- **Restarting the add-on doesn't lose what's already accumulated:** everything
  accumulated so far is restored from the SQLite database before the first new tick is
  processed. What flowed *during* the downtime itself, a pure tick accumulation
  fundamentally cannot make up for – see the next paragraph.

For three kWh quantities (grid import, feed-in, load), miniEMS prefers the **inverter's
own daily counter** when it's configured — more accurate, because a brief measurement
gap *and* add-on restarts don't affect the inverter-internal counter, while a pure tick
accumulation is vulnerable to both (every restart leaves a gap that's never made up). If
the respective entity isn't set, miniEMS accumulates from instantaneous power instead.
Where that applies is noted at each value.

**Spike filtering:** before any power reading enters a calculation, miniEMS checks it for
implausible jumps (more than 500 W *and* more than 50% change from the last accepted
value). Such an outlier — typically a brief communication glitch to the inverter — is
rejected and does not affect the daily values.

---

## Daily values

### Grid cost today

`sensor.miniems_today_grid_cost_eur` — **what you actually paid for grid power today**
(or would have, on a prepay tariff), at whatever dynamic price was in effect at the time.

On every tick with grid import (not export):

```
cost += (grid_power_W / 1000) × tick_duration_h × current_price_€/kWh
```

**Example:** A 30-second tick (= 0.008333 h) with 1200 W of grid import at €0.2744/kWh
(the cheap tier) contributes `1.2 kW × 0.008333 h × €0.2744/kWh ≈ €0.0027` to the daily
total. Over the whole day, that adds up — depending on how much was drawn at which
tier — to your real daily bill.

### PV savings today

`sensor.miniems_today_pv_savings_eur` — the amount you **saved** because PV power covered
household consumption instead of buying it from the grid at the current price. Only the
share of PV that was **directly** consumed counts — PV exported to the grid is excluded
here (it's compensated separately, see below).

```
pv_to_load_W = min(pv_power_W, load_power_W)     # never more than is actually needed
savings     += (pv_to_load_W / 1000) × tick_duration_h × current_price_€/kWh
```

Valued at the **current spot price** — the same kWh of PV saves more against
expensive-tier load than against cheap-tier load. That's intentional: it reflects the
actual financial benefit of your system, not just the energy produced.

### Total load cost today

`sensor.miniems_today_load_cost_eur` — the **hypothetical** cost if your entire household
consumption today had come from the grid at the current spot price — regardless of
whether it was actually covered by PV, battery, or grid:

```
load_cost += (load_power_W / 1000) × tick_duration_h × current_price_€/kWh
```

This value is always **greater than or equal to** the real grid cost, because PV and the
battery offset part of it. The gap between the two is a direct measure of the combined
value of your PV system and storage.

The cost itself is necessarily tick-based, like grid cost above – it needs the price at
every point in time. The underlying **kWh figure** (`today_load_total_kwh`) can, since
v2.0.3, optionally come from the inverter's own daily counter
(`load_consumption_entity`) instead of being purely extrapolated from ticks – just like
grid import and feed-in. Without that entity, the value was structurally fragile: every
add-on restart left a gap that was never made up, because there was no hardware counter
to anchor it to. Measured live: after a single restart, `today_load_total_kwh` was
already ~1.1 kWh (≈23%) below the inverter's own daily total.

!!! tip "Two different loss sensors"
    For the [inverter efficiency calculation](#advanced-metrics-optional) further down,
    miniEMS reads `today_losses_entity` — by default `sensor.deye8k_today_losses`. Some
    installations **also** have a similarly-named sensor `sensor.deye8k_loss_daily` (a
    separate utility-meter helper) with a slightly different value. Only
    `today_losses_entity` is the one to compare against the efficiency sensor — checking
    against the wrong one produces a visibly different result even though the formula
    itself is correct.

### Feed-in revenue today

`sensor.miniems_today_feed_in_revenue_eur` — your **income** from PV exported to the
grid, at the fixed feed-in tariff (`feed_in_tariff_eur_kwh`, default €0.08) — **not** the
dynamic spot price, since feed-in compensation is contractually fixed.

```
feed_in_kWh = energy exported today (from the inverter's counter or instantaneous power)
revenue     = feed_in_kWh × feed_in_tariff_eur_kwh
```

The underlying kWh figure shows on the dashboard card "Feed-in Today"
(`today_feed_in_kwh`); only the euro amount is exposed as its own HA sensor.

### Grid charging today

`sensor.miniems_today_grid_charge_kwh` (energy) and the cost derived from it show **how
much your battery charged from the grid today** — as opposed to from PV surplus. This is
derived from the power balance independently of the current EMS mode:

```
battery_charge_W = max(0, −battery_power_W)         # negative = charging
pv_surplus_W      = max(0, pv_power_W − load_power_W)
grid_charge_W     = max(0, battery_charge_W − pv_surplus_W)
```

In other words: whatever the battery charges beyond what the PV surplus can supply must
have come from the grid. The cost of that share is accumulated at the current price, just
like grid cost above.

!!! tip "More accurate variant, if configured"
    If the additional inverter sensors are set (see the "Advanced Sensors for
    Balance-Based Cost Calculation" section in [Configuration](configuration.md)),
    miniEMS supplements this power-based value with an **energy-balance-based** variant
    that's more robust against brief measurement gaps — see [Advanced
    Metrics](#advanced-metrics-optional) below.

### Cost without grid charging

`sensor.miniems_today_cost_without_grid_charge` — what your grid bill today would have
been **if the battery had not charged from the grid**:

```
cost without grid charging = max(0, grid_cost_today − grid_charge_cost_today)
```

Immediately shows whether the grid-charging strategy increased or reduced your total cost
today, once you compare it against the real grid cost.

### Cost at fixed price

`sensor.miniems_today_cost_fix_price_tariff` — for comparison: what the same day's load
would have cost on a classic **fixed-price tariff**, instead of your dynamic one:

```
fixed-price cost = total_load_kWh_today × fix_price + daily_base_price_eur
```

`fix_price` (default €0.30/kWh) and the optional daily standing charge
`daily_base_price_eur` (default 0, appears as its own sensor
`sensor.miniems_today_base_price_eur` once set) are configurable in
[Settings](configuration.md). This answers at a glance whether the dynamic tariff pays
off for you.

---

## The four cost scenarios compared

Four sensors together answer the same question from four angles — handy for a shared
dashboard card or an automation comparison:

| Sensor | Scenario | Answers |
|---|---|---|
| `today_grid_cost_eur` | **Actual** — what you really paid today (including grid charging) | Your real daily bill |
| `today_cost_without_grid_charge` | **Without grid charging** — what it would have cost without the battery charging from the grid | Does grid charging raise your cost? |
| `today_load_cost_eur` | **Without the system** — full load at the dynamic price, as if there were neither PV nor battery | What does your system earn you overall? |
| `today_cost_fix_price_tariff` | **Fixed-tariff comparison** — the same load on a classic fixed price | Does the dynamic tariff pay off? |

---

## Weekly, monthly and yearly totals

The same grid cost and PV savings amounts are additionally available rolling and
calendar-aggregated:

| Period | Sensors | Calculation |
|---|---|---|
| **Week** (rolling) | `week_grid_cost_eur`, `week_pv_savings_eur` | Sum of the last 7 calendar days (today + 6 preceding), straight from the in-memory day buckets — no database query needed |
| **Month** (calendar month) | `month_grid_cost_eur`, `month_pv_savings_eur`, `month_load_cost_eur` | `SUM(...) WHERE date is in the current calendar month`, from the `daily_stats` database table |
| **Year** (calendar year) | `year_grid_cost_eur`, `year_pv_savings_eur`, `year_load_cost_eur` | `SUM(...) WHERE year = current year`, also from `daily_stats` |

The week value is **rolling** (always the last 7 days), month and year are **calendar
based** (each starts on the 1st). All three build on the same daily values described
above — nothing is recalculated, only summed.

---

## Advanced metrics (optional)

Three further sensors become available once the additional inverter sensors for
balance-based calculation are configured (see the "Advanced Sensors for Balance-Based
Cost Calculation" section in [Configuration](configuration.md)). They **supplement**, not
replace, the values above.

### Inverter efficiency

`sensor.miniems_today_efficiency_pct` — how much of today's PV production is actually
usable (the rest is lost as conversion loss in the inverter):

```
η = (pv_production_today − losses_today) / pv_production_today × 100
```

**Example:** 32.5 kWh production, 2.6 kWh losses → η = 92.0%. Typical values range from
88% to 95%.

### Grid charging (energy balance)

`sensor.miniems_today_grid_charge_kwh_bilanz` — the same quantity as "grid charging
today" above, but calculated from the inverter's **daily total counters** instead of
instantaneous power — more robust against brief measurement gaps:

```
grid_charge_energy = grid_import_today − household_consumption_today + battery_discharge_today
```

*Why not just take grid import?* Part of it directly covers household consumption. The
formula subtracts that share, leaving only the actual battery-charging portion.

### Grid-charging strategy ROI

`sensor.miniems_today_grid_charge_roi_eur` — the key sensor for judging whether grid
charging **paid off financially** today:

```
usable_energy = grid_charge_kWh_bilanz × η
savings       = usable_energy × avg_discharge_tariff_eur_kwh
roi           = savings − grid_charge_cost_today
```

`avg_discharge_tariff_eur_kwh` is the tariff the charged energy avoided when discharged
(typically evening/night at the high tier) — configurable in Settings, default `0.0` =
sensor disabled.

**Example:** 5.5 kWh grid charging, η = 92%, discharge tariff €0.394/kWh, charging cost €1.51:

```
usable energy = 5.5 × 0.92        = 5.06 kWh
savings        = 5.06 × 0.394      = €1.99
roi            = 1.99 − 1.51       = +€0.48  ✅
```

- **Positive ROI** → grid charging paid off today.
- **Negative ROI** → grid charging cost more than the benefit it later provides.

---

## Where the values show up

- **Dashboard** (`/`) — the key daily values as cards, see
  [Dashboard & UI](dashboard.md).
- **Home Assistant** — every value described here is available as its own sensor, with
  long-term statistics support. Full entity list in [HA Sensors](sensors.md).
- **Database tab** (`/database`) — all daily values searchable historically, see
  [Data Storage](../technical/data-storage.md).
