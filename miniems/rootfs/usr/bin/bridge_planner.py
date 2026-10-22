"""Überbrückungsplanung: how much grid energy to buy now (v3.3.0).

docs/technical/netzladen-entscheidung.md, "Überbrückungsrechnung".

Question answered: *"Ich bin gerade in einem günstigen Tarif-Fenster. Wie viel
muss ich jetzt in den Speicher laden, damit ich bis zum nächsten ebenso
günstigen Fenster nichts zum teuren Tarif kaufen muss – und lohnt sich das?"*

Pure function over a list of planning slots (30 min each):

1. The *current cheap window* is the run of slots from now whose rate is no
   higher than the current one. While grid charging, the battery does not
   discharge there (InverterController blocks discharge in GRID_CHARGING).
2. The *bridge period* runs from the end of that window to the next slot that
   is again at most as cheap – after that, energy can be bought cheaply again.
3. A slot in the bridge period is *worth bridging* when a stored kWh pays:
   `η × rate_slot − rate_now > min_margin` (same economics as
   EMSController._grid_charge_pays, per slot).
4. The battery is simulated slot by slot: PV surplus charges it (up to full),
   a deficit discharges it (down to min SoC). Whatever deficit the battery
   cannot cover in a worth-bridging slot is the *shortfall*.
5. `need_kwh` is the smallest extra charge that brings that shortfall to ~0,
   capped by free capacity and by what the charger can deliver before the
   cheap window ends.

Every slot carries its own load and PV, so a sunny afternoon automatically
reduces the need, and a cold night (heat pump) raises it.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

# Shortfall below this is treated as covered (forecast noise).
_SHORTFALL_EPS_KWH = 0.05
# Rates within this of each other count as equally cheap (€/kWh).
_RATE_TOLERANCE = 0.0005
_BISECT_ROUNDS = 25


@dataclass(frozen=True)
class PlanSlot:
    start: datetime
    end: datetime
    rate_eur_kwh: float | None
    load_kwh: float
    pv_kwh: float

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass
class BridgePlan:
    need_kwh: float                    # energy to add during the current cheap window
    shortfall_kwh: float               # expensive purchase if nothing is charged
    cheap_until: datetime | None       # end of the current cheap window
    bridge_until: datetime | None      # start of the next equally-cheap window
    worth_slots: int                   # slots in which a stored kWh pays
    reason: str
    bridge_load_kwh: float = 0.0
    bridge_pv_kwh: float = 0.0
    capped_by: str | None = None       # "free_capacity" | "charge_time" | None
    slots: list[PlanSlot] = field(default_factory=list, repr=False)

    def as_dict(self) -> dict:
        return {
            "need_kwh": round(self.need_kwh, 3),
            "shortfall_kwh": round(self.shortfall_kwh, 3),
            "cheap_until": self.cheap_until.isoformat() if self.cheap_until else None,
            "bridge_until": self.bridge_until.isoformat() if self.bridge_until else None,
            "worth_slots": self.worth_slots,
            "reason": self.reason,
            "bridge_load_kwh": round(self.bridge_load_kwh, 3),
            "bridge_pv_kwh": round(self.bridge_pv_kwh, 3),
            "capped_by": self.capped_by,
        }


def _no_need(reason: str, **kw) -> BridgePlan:
    return BridgePlan(need_kwh=0.0, shortfall_kwh=0.0, cheap_until=kw.get("cheap_until"),
                      bridge_until=kw.get("bridge_until"), worth_slots=kw.get("worth_slots", 0),
                      reason=reason)


def plan_bridge(
    slots: list[PlanSlot],
    stored_kwh: float,
    free_kwh: float,
    eta: float,
    min_margin_eur_kwh: float,
    charge_kw: float | None,
) -> BridgePlan:
    """Plan the grid charge for the current cheap window. See module docstring.

    `slots[0]` must contain "now" and carry the current rate. `stored_kwh` is
    the energy usable above min SoC, `free_kwh` the room up to max SoC.
    """
    if not slots or slots[0].rate_eur_kwh is None:
        return _no_need("no current rate")
    now_rate = slots[0].rate_eur_kwh

    def is_cheap(s: PlanSlot) -> bool:
        return s.rate_eur_kwh is not None and s.rate_eur_kwh <= now_rate + _RATE_TOLERANCE

    # 1. current cheap window
    i = 0
    while i < len(slots) and is_cheap(slots[i]):
        i += 1
    cheap = slots[:i]
    cheap_until = cheap[-1].end

    # 2. bridge period – ends at the next equally-cheap slot or an unknown rate
    j = i
    while j < len(slots) and slots[j].rate_eur_kwh is not None and not is_cheap(slots[j]):
        j += 1
    bridge = slots[i:j]
    bridge_until = slots[j].start if j < len(slots) else (bridge[-1].end if bridge else None)
    if not bridge:
        return _no_need("no expensive period ahead", cheap_until=cheap_until,
                        bridge_until=bridge_until)

    # 3. worth-bridging slots
    worth = [eta * s.rate_eur_kwh - now_rate > min_margin_eur_kwh  # type: ignore[operator]
             for s in bridge]
    n_worth = sum(worth)
    if n_worth == 0:
        return _no_need("expensive period does not pay for storage", cheap_until=cheap_until,
                        bridge_until=bridge_until)

    capacity = max(0.0, stored_kwh) + max(0.0, free_kwh)

    # 4. simulation
    def shortfall(extra: float) -> float:
        battery = max(0.0, stored_kwh)
        # current cheap window: no discharge, PV surplus still charges
        for s in cheap:
            battery = min(capacity, battery + max(0.0, s.pv_kwh - s.load_kwh))
        battery = min(capacity, battery + extra)
        missing = 0.0
        for s, w in zip(bridge, worth):
            net = s.pv_kwh - s.load_kwh
            if net >= 0:
                battery = min(capacity, battery + net)
                continue
            draw = min(battery, -net)
            battery -= draw
            if w:
                missing += -net - draw
        return missing

    base_shortfall = shortfall(0.0)
    plan_kw = dict(cheap_until=cheap_until, bridge_until=bridge_until, worth_slots=n_worth)
    load_sum = sum(s.load_kwh for s in bridge)
    pv_sum = sum(s.pv_kwh for s in bridge)
    if base_shortfall <= _SHORTFALL_EPS_KWH:
        plan = _no_need("battery + PV cover the expensive period", **plan_kw)
        plan.bridge_load_kwh, plan.bridge_pv_kwh = load_sum, pv_sum
        plan.slots = slots
        return plan

    # 5. smallest extra charge that closes the gap (monotone → bisection)
    room = max(0.0, free_kwh)
    hi = room
    if shortfall(hi) > _SHORTFALL_EPS_KWH:
        need = hi                       # even a full battery leaves a gap
    else:
        lo = 0.0
        for _ in range(_BISECT_ROUNDS):
            mid = (lo + hi) / 2.0
            if shortfall(mid) > _SHORTFALL_EPS_KWH:
                lo = mid
            else:
                hi = mid
        need = hi

    capped_by = "free_capacity" if need >= room - 1e-6 else None
    reason = "charge to bridge expensive period"

    # A strictly cheaper stretch later in the same cheap run (e.g. now
    # STANDARD, NIEDRIG from 02:00) should carry the charge. Buy now only
    # what that cheaper stretch cannot deliver in time.
    cheaper_h = sum(s.hours for s in cheap
                    if s.rate_eur_kwh is not None
                    and s.rate_eur_kwh < now_rate - _RATE_TOLERANCE)
    if cheaper_h > 0:
        if charge_kw is None or charge_kw <= 0:
            need = 0.0
        else:
            need = max(0.0, need - charge_kw * cheaper_h)
        reason = "cheaper window ahead"
        capped_by = None

    if charge_kw is not None and charge_kw > 0:
        deliverable = charge_kw * sum(s.hours for s in cheap)
        if need > deliverable:
            need = deliverable
            capped_by = "charge_time"

    return BridgePlan(
        need_kwh=need,
        shortfall_kwh=base_shortfall,
        cheap_until=cheap_until,
        bridge_until=bridge_until,
        worth_slots=n_worth,
        reason=reason,
        bridge_load_kwh=load_sum,
        bridge_pv_kwh=pv_sum,
        capped_by=capped_by,
        slots=slots,
    )
