"""What is a stored or a bought kWh worth? (A3, A4)

docs/roadmap/gesamtplan.md, "Wert einer zusätzlich gekauften kWh". One pure
function shared by the grid-charge gate (A3) and the discharge hold (A4), so
the two can never disagree about which hours the battery already covers.

Inputs are the planning slots from now on (`PlanSlot`: rate, load, PV per 30
min). The span that matters ends where the price is as low as now again –
from there energy can be bought cheaply again:

    cheap run  = slots from now that are as cheap as now
    later      = the slots after it, up to the next slot as cheap as now
    deficit_w  = (load_w − pv_w)⁺
    pool       = usable energy × η_discharge, plus PV surplus on the way
    cheap run  = served by the grid: a grid charge there blocks discharging
                 (GRID_CHARGING), and so does the hold (A4)
    covered    = the battery serves the later deficits in the order it
                 really will: by the clock without a hold (it discharges into
                 every load as it comes), dearest first with a hold (A4)
    pays_w     = η × rate_w − rate_now − wear > min_margin
    buy_kwh    = Σ uncovered deficit of the later slots that pay

The first bought kWh displaces the dearest uncovered hour, every further one
a cheaper one: the value of stored energy is concave. A single historical
"discharge tariff" either hoards or drains – that is why the gate looks at
the hours themselves.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from bridge_planner import PlanSlot

# Rates within this count as "as cheap as now" (€/kWh).
RATE_TOLERANCE = 0.0005
_EPS_KWH = 0.05


@dataclass(frozen=True)
class EnergyValue:
    """Result of assess_energy() – see module docstring."""

    rate_now: float
    cheap_slots: int            # length of the current cheap run
    later_slots: int            # slots until the price is as low as now again
    dearer_need_kwh: float      # deficit of the later slots
    held_for_kwh: float         # deficit of later slots at least min_spread dearer
    pool_kwh: float             # usable battery energy × η_discharge
    pv_surplus_kwh: float       # PV surplus in the cheap run and the later slots
    uncovered_kwh: float        # deficit the battery cannot serve
    buy_kwh: float              # uncovered deficit in slots where a bought kWh pays
    first_value_eur_kwh: float | None   # rate of the dearest uncovered slot that pays

    @property
    def pays(self) -> bool:
        return self.buy_kwh > _EPS_KWH

    @property
    def available_kwh(self) -> float:
        """Battery energy plus the PV surplus still to come within the span."""
        return self.pool_kwh + self.pv_surplus_kwh

    def as_dict(self) -> dict:
        return {
            "rate_now": round(self.rate_now, 5),
            "later_slots": self.later_slots,
            "dearer_need_kwh": round(self.dearer_need_kwh, 3),
            "held_for_kwh": round(self.held_for_kwh, 3),
            "available_kwh": round(self.available_kwh, 3),
            "uncovered_kwh": round(self.uncovered_kwh, 3),
            "buy_kwh": round(self.buy_kwh, 3),
            "first_value_eur_kwh": (round(self.first_value_eur_kwh, 5)
                                    if self.first_value_eur_kwh is not None else None),
        }


def assess_energy(
    slots: "list[PlanSlot]",
    usable_kwh: float,
    free_kwh: float,
    eta: float,
    eta_discharge: float = 1.0,
    wear_eur_kwh: float = 0.0,
    min_margin_eur_kwh: float = 0.0,
    min_spread_eur_kwh: float = 0.0,
    hold: bool = False,
    tolerance_eur: float = RATE_TOLERANCE,
) -> EnergyValue | None:
    """Assess the slots from now on. None when slot 0 has no rate.

    `usable_kwh` is the stored energy above min SoC, `free_kwh` the room up
    to max SoC, `eta` the round-trip efficiency a bought kWh suffers.
    `hold` = the battery serves the dearest later hours first (A4); without
    it, it serves them as they come. `tolerance_eur` is the band within which a
    later rate counts as "as cheap as now" (A9 widens it for day-ahead).
    """
    if not slots or slots[0].rate_eur_kwh is None:
        return None
    rate_now = slots[0].rate_eur_kwh

    def as_cheap(s: "PlanSlot") -> bool:
        return s.rate_eur_kwh is not None and s.rate_eur_kwh <= rate_now + tolerance_eur

    i = 0
    while i < len(slots) and as_cheap(slots[i]):
        i += 1
    j = i
    while j < len(slots) and slots[j].rate_eur_kwh is not None and not as_cheap(slots[j]):
        j += 1
    cheap, later = slots[:i], slots[i:j]

    capacity = max(0.0, usable_kwh) + max(0.0, free_kwh)
    pool0 = max(0.0, usable_kwh) * eta_discharge
    deficits = [max(0.0, s.load_kwh - s.pv_kwh) for s in later]
    surplus_cheap = sum(max(0.0, s.pv_kwh - s.load_kwh) for s in cheap)
    surplus_later = sum(max(0.0, s.pv_kwh - s.load_kwh) for s in later)
    held_for = sum(d for s, d in zip(later, deficits)
                   if s.rate_eur_kwh - rate_now >= min_spread_eur_kwh - 1e-9)  # type: ignore[operator]

    uncovered_by_slot: dict[int, float] = {}
    if hold:
        # PV is added up front, the battery serves the dearest hours first.
        pool = min(capacity, pool0 + surplus_cheap + surplus_later)
        for k in sorted(range(len(later)), key=lambda k: -(later[k].rate_eur_kwh or 0.0)):
            take = min(pool, deficits[k])
            pool -= take
            if deficits[k] - take > 0:
                uncovered_by_slot[k] = deficits[k] - take
    else:
        pool = min(capacity, pool0 + surplus_cheap)
        for k, s in enumerate(later):
            net = s.pv_kwh - s.load_kwh
            if net >= 0:
                pool = min(capacity, pool + net)
                continue
            take = min(pool, -net)
            pool -= take
            if -net - take > 0:
                uncovered_by_slot[k] = -net - take

    buy = 0.0
    first_value: float | None = None
    for k, miss in uncovered_by_slot.items():
        rate = later[k].rate_eur_kwh
        if eta * rate - rate_now - wear_eur_kwh > min_margin_eur_kwh:   # type: ignore[operator]
            buy += miss
            first_value = rate if first_value is None else max(first_value, rate)  # type: ignore[type-var]

    return EnergyValue(
        rate_now=rate_now,
        cheap_slots=len(cheap),
        later_slots=len(later),
        dearer_need_kwh=sum(deficits),
        held_for_kwh=held_for,
        pool_kwh=pool0,
        pv_surplus_kwh=surplus_cheap + surplus_later,
        uncovered_kwh=sum(uncovered_by_slot.values()),
        buy_kwh=min(buy, max(0.0, free_kwh)),
        first_value_eur_kwh=first_value,
    )


def should_hold(
    slots: "list[PlanSlot]",
    usable_kwh: float,
    free_kwh: float,
    eta_discharge: float,
    min_spread_eur_kwh: float,
    holding: bool,
    tolerance_eur: float = RATE_TOLERANCE,
) -> tuple[bool, EnergyValue | None]:
    """A4: keep the battery for dearer hours?

    Hold when the stored energy plus the PV still to come cannot cover the
    deficit of the later hours that are at least `min_spread_eur_kwh`
    dearer than now: every kWh discharged now would then be missing in an
    hour that costs more. Kept energy is already stored, there is no second
    round trip, so no efficiency enters the spread – only the threshold δ.

    Hysteresis: entering needs available < need, leaving needs available >
    need × 1.1 + 0.2 kWh, so a forecast moving by a few hundred Wh cannot
    flap it.
    """
    value = assess_energy(
        slots, usable_kwh, free_kwh, eta=1.0, eta_discharge=eta_discharge,
        min_spread_eur_kwh=min_spread_eur_kwh, hold=True, tolerance_eur=tolerance_eur,
    )
    if value is None or value.held_for_kwh <= _EPS_KWH:
        return False, value
    if holding:
        return value.available_kwh <= value.held_for_kwh * 1.1 + 0.2, value
    return value.available_kwh < value.held_for_kwh, value
