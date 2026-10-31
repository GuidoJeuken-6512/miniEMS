"""Battery state per planning slot, for pricing hot water (docs/roadmap/v3.4-warmwasser.md, Schritt 5).

Pure, no I/O. A deliberately simple forward simulation in the spirit of
bridge_planner.plan_bridge's shortfall():

* outside grid-charge windows the battery takes the PV surplus and covers the
  house load (PV − load), clipped to [0, capacity]; surplus that no longer
  fits is exported ("spill");
* inside a grid-charge window it does not discharge (the inverter blocks it)
  and only takes PV surplus. How much the energy plan buys from the grid
  there is not modelled – conservative: the battery looks emptier afterwards
  than it will be, so hot water is priced at the grid tariff more often.

The refill price of a slot is what a battery kWh used in that slot really
costs:

* if PV later spills at least _SPILL_MIN_KWH while the battery never runs
  empty in between, the kWh is simply not exported later – it costs the
  feed-in tariff (e.g. a sunny day with the battery half full in the morning);
* otherwise it has to be bought back from the grid: the cheapest tariff later
  in the horizon divided by the round-trip efficiency.
"""
from __future__ import annotations

from dataclasses import dataclass

# A tariff within this of the horizon minimum counts as the cheap (grid-charge) window.
_CHEAP_TOLERANCE_EUR = 0.005
# Energy thresholds for "full" / "has energy" (kWh).
_FULL_EPS_KWH = 0.1
_EMPTY_EPS_KWH = 0.2
# Later PV spill that makes a battery kWh used now cost only the feed-in tariff.
# About one hot-water charge (≈ 1 kWh heat ÷ COP) – the most a single charge draws.
_SPILL_MIN_KWH = 0.5


@dataclass(frozen=True)
class BatteryContext:
    stored_kwh: float            # usable above min SoC now
    free_kwh: float              # room up to max SoC now
    eta: float                   # round-trip efficiency 0..1
    grid_charging: bool = False  # miniEMS may grid-charge (battery control on)


@dataclass(frozen=True)
class BatterySlot:
    full: bool
    has_energy: bool
    blocked: bool                # grid-charge window: no discharge
    refill_eur_kwh: float | None
    refilled_by_pv: bool = False  # refill price is the feed-in tariff (later PV spill)


def project_battery(
    rates: list[float | None],
    pv_kwh: list[float],
    load_kwh: list[float],
    ctx: BatteryContext,
    feed_in_eur_kwh: float | None = None,
) -> list[BatterySlot]:
    n = len(rates)
    known = [r for r in rates if r is not None]
    cheap_rate = min(known) if known else None
    eta = ctx.eta if ctx.eta and ctx.eta > 0 else 0.9

    # cheapest tariff after each slot (suffix minimum), else the horizon minimum
    suffix: list[float | None] = [None] * n
    best: float | None = None
    for i in range(n - 1, -1, -1):
        suffix[i] = best if best is not None else cheap_rate
        if rates[i] is not None:
            best = rates[i] if best is None else min(best, rates[i])

    # forward simulation: level at slot start, PV spill in the slot
    capacity = max(0.0, ctx.stored_kwh) + max(0.0, ctx.free_kwh)
    battery = max(0.0, ctx.stored_kwh)
    level: list[float] = []
    spill: list[float] = []
    blocked: list[bool] = []
    for i in range(n):
        b = bool(ctx.grid_charging and rates[i] is not None and cheap_rate is not None
                 and rates[i] <= cheap_rate + _CHEAP_TOLERANCE_EUR)
        blocked.append(b)
        level.append(battery)
        net = pv_kwh[i] - load_kwh[i]
        if b and net < 0:
            spill.append(0.0)
            continue                     # no discharge in the grid-charge window
        spill.append(max(0.0, battery + net - capacity))
        battery = min(capacity, max(0.0, battery + net))

    def refilled_by_pv(i: int) -> bool:
        """Does later PV spill cover a kWh taken in slot i, before the battery runs dry?"""
        if feed_in_eur_kwh is None:
            return False
        acc = 0.0
        for j in range(i, n):
            if j > i and level[j] <= _EMPTY_EPS_KWH:
                return False             # runs empty first – the gap is bought from the grid
            acc += spill[j]
            if acc >= _SPILL_MIN_KWH:
                return True
        return False

    out: list[BatterySlot] = []
    for i in range(n):
        by_pv = refilled_by_pv(i)
        if by_pv:
            refill = feed_in_eur_kwh
        else:
            refill = (suffix[i] / eta) if suffix[i] is not None else None
        out.append(BatterySlot(
            full=capacity - level[i] < _FULL_EPS_KWH,
            has_energy=level[i] > _EMPTY_EPS_KWH,
            blocked=blocked[i],
            refill_eur_kwh=refill,
            refilled_by_pv=by_pv,
        ))
    return out
