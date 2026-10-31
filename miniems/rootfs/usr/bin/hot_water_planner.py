"""Hot-water heating plan (docs/roadmap/v3.4-warmwasser.md, Schritt 5). Pure, no I/O.

The boiler is simulated slot by slot (standby loss ∝ T − room, plus the
learned draw profile). Heating happens only in *whole charges*: a charge
starts in some slot and runs at the heat pump's learned thermal power until
the boiler reaches the shut-off temperature – exactly what the controller
does (the written setpoint is only a start impulse, miniEMS ends the charge
at shut-off).

Planning (greedy, like energy_plan.compute_energy_plan but per charge):

1. Simulate and find the first slot whose temperature would violate a
   requirement (day target, workday morning target).
2. Every slot up to that violation is a candidate start. A candidate is only
   valid if its charge fixes the violation. Each valid candidate is scored

       cost per kWh of heat ÷ retention (charge end → violation)

   where cost per kWh_th = Σ electricity × effective price ÷ heat. Heating
   earlier loses more on the way, the retention divides that in.
3. "Charge now" (slot 0) wins whenever it is at most `tolerance_pct` dearer
   than the best later candidate – so the question "do I make it to the next
   cheap window?" is answered with "no" only when waiting saves more than the
   tolerance. Otherwise the cheapest candidate wins, ties go to the later slot.
4. Add that charge, simulate again, repeat until nothing is violated.

Effective price per kWh of electricity in a slot (see HWSlot's battery fields):

    PV-surplus share : feed-in tariff, or – while the battery is not full –
                       max(feed-in, refill price): that kWh would otherwise
                       charge the battery and must be bought back later
    remaining share  : grid tariff, or min(refill price, tariff) while the
                       battery has usable energy and may discharge
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from const import HW_WH_PER_L_K

if TYPE_CHECKING:
    from hot_water_model import HotWaterModel

# A charge only starts below shut-off minus this – same rule as the controller.
_START_MARGIN_K = 0.5
_MAX_CHARGES = 48
_EPS = 1e-9


@dataclass(frozen=True)
class HWSlot:
    start: datetime
    end: datetime
    rate_eur_kwh: float | None
    pv_surplus_kwh: float | None   # electrical, None = unknown
    outdoor_c: float | None
    # Battery at this slot (battery_projection.py). None = battery not part of
    # the price (no SoC known) – then only PV and grid count.
    battery_full: bool | None = None
    battery_has_energy: bool | None = None
    battery_blocked: bool = False          # grid-charge window: no discharge
    refill_eur_kwh: float | None = None    # what a battery kWh costs to buy back

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass(frozen=True)
class HWRequirement:
    at: datetime
    min_c: float
    label: str


@dataclass
class HWWindow:
    """One planned charge: from its start until the shut-off temperature."""
    start: datetime
    end: datetime
    heat_kwh_th: float
    elec_kwh: float
    cost_eur: float
    cop: float
    source: str        # "pv" | "battery" | "grid" – by electricity share
    goal_c: float      # temperature at the end of the charge (≈ shut-off)
    from_c: float = 0.0

    @property
    def rate_eur_kwh(self) -> float:
        """Average effective electricity price of the charge."""
        return self.cost_eur / self.elec_kwh if self.elec_kwh > 0 else 0.0

    @property
    def cost_th_eur_kwh(self) -> float:
        """Cost per kWh of heat."""
        return self.cost_eur / self.heat_kwh_th if self.heat_kwh_th > 0 else 0.0

    def as_dict(self) -> dict[str, Any]:
        return {
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "heat_kwh_th": round(self.heat_kwh_th, 2),
            "energy_kwh": round(self.elec_kwh, 3),
            "rate_eur_kwh": round(self.rate_eur_kwh, 4),
            "cost_th_eur_kwh": round(self.cost_th_eur_kwh, 4),
            "cost_eur": round(self.cost_eur, 3),
            "cop": round(self.cop, 2),
            "source": self.source,
            "from_c": round(self.from_c, 1),
            "goal_c": round(self.goal_c, 1),
        }


@dataclass
class HotWaterPlan:
    computed_at: datetime
    boil_c: float
    floor_c: float
    shutoff_c: float = 0.0
    windows: list[HWWindow] = field(default_factory=list)
    projection: list[tuple[datetime, float]] = field(default_factory=list)
    requirements: list[HWRequirement] = field(default_factory=list)
    feasible: bool = True
    reason: str = ""
    # Planned electrical kWh per slot, keyed by slot end (on the slot grid).
    planned_elec_by_end: dict[datetime, float] = field(default_factory=dict)
    pv_elec_by_end: dict[datetime, float] = field(default_factory=dict)
    # Decision for the current slot.
    charge_now: bool = False
    need_at: datetime | None = None          # first violation without any heating
    need_c: float | None = None              # the temperature required then
    now_cost_th_eur_kwh: float | None = None  # a charge started now, per kWh heat
    now_pv_cost_th_eur_kwh: float | None = None  # pure PV heat now (feed-in / COP)
    decision: str = ""
    # Per-slot pricing, for diagnosis ("why grid and not battery?").
    slot_info: list[dict[str, Any]] = field(default_factory=list)

    @property
    def estimated_cost_eur(self) -> float:
        return sum(w.cost_eur for w in self.windows)

    def window_at(self, moment: datetime) -> HWWindow | None:
        return next((w for w in self.windows if w.start <= moment < w.end), None)

    def next_window(self, moment: datetime) -> HWWindow | None:
        return next((w for w in self.windows if w.start > moment), None)

    def temp_at(self, moment: datetime) -> float | None:
        prev = None
        for t, v in self.projection:
            if t > moment:
                return v if prev is None else prev
            prev = v
        return prev

    def as_dict(self) -> dict[str, Any]:
        return {
            "computed_at": self.computed_at.isoformat(),
            "boil_c": round(self.boil_c, 1),
            "floor_c": round(self.floor_c, 1),
            "shutoff_c": round(self.shutoff_c, 1),
            "feasible": self.feasible,
            "reason": self.reason,
            "decision": self.decision,
            "charge_now": self.charge_now,
            "need_at": self.need_at.isoformat() if self.need_at else None,
            "now_cost_th_eur_kwh": (round(self.now_cost_th_eur_kwh, 4)
                                    if self.now_cost_th_eur_kwh is not None else None),
            "estimated_cost_eur": round(self.estimated_cost_eur, 3),
            "heat_kwh_th": round(sum(w.heat_kwh_th for w in self.windows), 2),
            "energy_kwh": round(sum(w.elec_kwh for w in self.windows), 3),
            "windows": [w.as_dict() for w in self.windows],
            "slots": self.slot_info,
            "projection": [{"t": t.isoformat(), "temp_c": round(v, 1)} for t, v in self.projection],
            "requirements": [
                {"at": r.at.isoformat(), "min_c": r.min_c, "label": r.label}
                for r in self.requirements
            ],
        }


def _slot_price(s: HWSlot, pv_frac: float, feed_in: float) -> tuple[float | None, str]:
    """Effective €/kWh_el for heating in this slot and the dominant source."""
    battery = s.battery_full is not None and s.refill_eur_kwh is not None
    pv_price = feed_in
    if battery and not s.battery_full:
        pv_price = max(feed_in, s.refill_eur_kwh)   # type: ignore[arg-type]
    rest_source = "grid"
    if s.rate_eur_kwh is None:
        rest = None
    else:
        rest = s.rate_eur_kwh
        if battery and s.battery_has_energy and not s.battery_blocked:
            if s.refill_eur_kwh < rest:   # type: ignore[operator]
                rest = s.refill_eur_kwh
                rest_source = "battery"
    if rest is None:
        return (pv_price if pv_frac >= 1.0 else None), "pv"
    price = pv_frac * pv_price + (1.0 - pv_frac) * rest   # type: ignore[operator]
    return price, ("pv" if pv_frac >= 0.5 else rest_source)


def plan_hot_water(
    now: datetime,
    slots: list[HWSlot],
    boil_c: float,
    model: "HotWaterModel",
    *,
    floor_c: float,
    shutoff_c: float,
    volume_l: float,
    feed_in_eur_kwh: float,
    requirements: list[HWRequirement],
    tolerance_pct: float = 5.0,
) -> HotWaterPlan:
    plan = HotWaterPlan(computed_at=now, boil_c=boil_c, floor_c=floor_c, shutoff_c=shutoff_c,
                        requirements=list(requirements))
    if not slots:
        plan.feasible = False
        plan.reason = "no planning horizon"
        plan.decision = plan.reason
        return plan

    n = len(slots)
    k_per_kwh = 1000.0 / (HW_WH_PER_L_K * volume_l)   # K per kWh of heat
    cop = [model.cop_at(s.outdoor_c) for s in slots]
    cap_k = [model.heat_kw_th * s.hours * k_per_kwh for s in slots]
    draw = [model.draw_k(s.start, s.end) for s in slots]

    pv_frac: list[float] = []
    for i, s in enumerate(slots):
        full_elec = cap_k[i] / k_per_kwh / cop[i]
        if s.pv_surplus_kwh is None or full_elec <= 0:
            pv_frac.append(0.0)
        else:
            pv_frac.append(max(0.0, min(1.0, s.pv_surplus_kwh / full_elec)))
    prices = [_slot_price(s, pv_frac[i], feed_in_eur_kwh) for i, s in enumerate(slots)]
    # € per kWh of heat when heating in slot i; None = price unknown
    cost_th = [p / cop[i] if p is not None else None for i, (p, _) in enumerate(prices)]
    rnd = lambda v, d=4: round(v, d) if v is not None else None   # noqa: E731
    plan.slot_info = [
        {
            "start": s.start.isoformat(),
            "rate_eur_kwh": rnd(s.rate_eur_kwh),
            "pv_surplus_kwh": rnd(s.pv_surplus_kwh, 3),
            "pv_frac": round(pv_frac[i], 2),
            "battery_full": s.battery_full,
            "battery_has_energy": s.battery_has_energy,
            "battery_blocked": s.battery_blocked,
            "refill_eur_kwh": rnd(s.refill_eur_kwh),
            "price_eur_kwh": rnd(prices[i][0]),
            "source": prices[i][1],
            "cop": round(cop[i], 2),
            "cost_th_eur_kwh": rnd(cost_th[i]),
        }
        for i, s in enumerate(slots[:48])
    ]

    required = [floor_c] * n
    for r in requirements:
        idx = next((i for i, s in enumerate(slots) if s.end >= r.at), None)
        if idx is not None:
            required[idx] = max(required[idx], r.min_c)

    def simulate(starts: set[int]) -> tuple[list[float], list[float], list[float]]:
        """Slot start/end temperatures and heat (K) added per slot."""
        t = boil_c
        charging = False
        st, en, heat = [], [], []
        for i, s in enumerate(slots):
            st.append(t)
            if i in starts and t < shutoff_c - _START_MARGIN_K:
                charging = True
            add = 0.0
            if charging:
                add = max(0.0, min(cap_k[i], shutoff_c - t))
                t += add
                if t >= shutoff_c - 1e-6:
                    charging = False
            heat.append(add)
            t -= model.loss_k_per_h(t) * s.hours + draw[i]
            en.append(t)
        return st, en, heat

    def first_violation(en: list[float]) -> int | None:
        return next((i for i in range(n) if en[i] < required[i] - 1e-6), None)

    def charge_cost(base_heat: list[float], new_heat: list[float]) -> tuple[float, float, int] | None:
        """(heat kWh, cost €, last heated slot) of the heat a new charge adds."""
        heat_kwh = cost = 0.0
        last = -1
        for i in range(n):
            dk = new_heat[i] - base_heat[i]
            if dk <= _EPS:
                continue
            if cost_th[i] is None:
                return None
            heat_kwh += dk / k_per_kwh
            cost += dk / k_per_kwh * cost_th[i]   # type: ignore[operator]
            last = i
        return (heat_kwh, cost, last) if heat_kwh > _EPS else None

    # ── decision info for "now", before any charge is planned ──────────
    _, en0, heat0 = simulate(set())
    v0 = first_violation(en0)
    if v0 is not None:
        plan.need_at = slots[v0].end
        plan.need_c = required[v0]
    _, _, heat_now = simulate({0})
    now_charge = charge_cost(heat0, heat_now)
    if now_charge is not None:
        plan.now_cost_th_eur_kwh = now_charge[1] / now_charge[0]
    elif cost_th[0] is not None:
        plan.now_cost_th_eur_kwh = cost_th[0]
    plan.now_pv_cost_th_eur_kwh = feed_in_eur_kwh / cop[0]

    # ── greedy: whole charges until no requirement is violated ─────────
    charges: list[int] = []
    for _ in range(_MAX_CHARGES):
        _, en, heat = simulate(set(charges))
        v = first_violation(en)
        if v is None:
            break
        candidates: list[tuple[int, float]] = []
        for j in range(v + 1):
            if j in charges:
                continue
            _, en2, heat2 = simulate(set(charges) | {j})
            if en2[v] < required[v] - 1e-6:
                continue
            cc = charge_cost(heat, heat2)
            if cc is None:
                continue
            heat_kwh, cost, last = cc
            retention = 1.0
            for m in range(last, v + 1):
                retention *= max(0.0, 1.0 - model.loss_coeff * slots[m].hours)
            if retention <= 0:
                continue
            candidates.append((j, cost / heat_kwh / retention))
        if not candidates:
            plan.feasible = False
            label = next((r.label for r in requirements
                          if required[v] == r.min_c and slots[v].end >= r.at), "minimum")
            plan.reason = (f"cannot hold {required[v]:.0f} °C ({label}) at "
                           f"{slots[v].end.strftime('%H:%M')}")
            break
        best = min(score for _, score in candidates)
        now_c = next((score for j, score in candidates if j == 0), None)
        if now_c is not None and now_c <= best * (1.0 + tolerance_pct / 100.0) + _EPS:
            pick = 0
        else:
            pick = max(j for j, score in candidates if score <= best + _EPS)   # ties: later
        charges.append(pick)

    st, en, heat = simulate(set(charges))
    plan.projection = [(slots[0].start, boil_c)] + [(s.end, en[i]) for i, s in enumerate(slots)]

    # ── windows = one per charge (contiguous heated slots) ─────────────
    window: HWWindow | None = None
    src_elec: dict[str, float] = {}
    for i, s in enumerate(slots):
        if heat[i] <= _EPS:
            if window is not None:
                window.source = max(src_elec, key=src_elec.get)
            window = None
            src_elec = {}
            continue
        heat_kwh = heat[i] / k_per_kwh
        elec = heat_kwh / cop[i]
        price, src = prices[i]
        price = price or 0.0
        plan.planned_elec_by_end[s.end] = elec
        plan.pv_elec_by_end[s.end] = elec * pv_frac[i]
        src_elec[src] = src_elec.get(src, 0.0) + elec
        if window is None or i in charges:
            if window is not None:
                window.source = max(src_elec, key=src_elec.get)
                src_elec = {src: elec}
            window = HWWindow(start=s.start, end=s.end, heat_kwh_th=heat_kwh, elec_kwh=elec,
                              cost_eur=elec * price, cop=cop[i], source=src,
                              goal_c=st[i] + heat[i], from_c=st[i])
            plan.windows.append(window)
        else:
            total = window.elec_kwh + elec
            window.cop = (window.cop * window.elec_kwh + cop[i] * elec) / total
            window.end = s.end
            window.heat_kwh_th += heat_kwh
            window.elec_kwh = total
            window.cost_eur += elec * price
            window.goal_c = st[i] + heat[i]
    if window is not None and src_elec:
        window.source = max(src_elec, key=src_elec.get)

    # ── decision text for the current slot ─────────────────────────────
    plan.charge_now = 0 in charges and bool(plan.windows) and plan.windows[0].start == slots[0].start
    ct = lambda v: f"{v * 100:.1f} ct/kWh_th" if v is not None else "– ct/kWh_th"   # noqa: E731
    if plan.charge_now:
        w = plan.windows[0]
        need = (f"would fall below {plan.need_c:.0f} °C at {plan.need_at.strftime('%H:%M')}"
                if plan.need_at is not None and plan.need_c is not None else "needed")
        nxt = plan.next_window(slots[0].end)
        alt = (f", next option {nxt.start.strftime('%H:%M')} {ct(nxt.cost_th_eur_kwh)}"
               if nxt is not None else "")
        plan.decision = (f"charge now until {shutoff_c:.0f} °C – {need}; "
                         f"{ct(w.cost_th_eur_kwh)} ({w.source}, COP {w.cop:.1f}){alt}")
    elif plan.windows:
        w = plan.windows[0]
        t_then = plan.temp_at(w.start)
        reach = f" ({t_then:.1f} °C then)" if t_then is not None else ""
        plan.decision = (f"wait – enough until {w.start.strftime('%H:%M')}{reach}; "
                         f"now {ct(plan.now_cost_th_eur_kwh)}, then {ct(w.cost_th_eur_kwh)} "
                         f"({w.source}, COP {w.cop:.1f})")
    elif plan.feasible:
        plan.reason = plan.reason or "no heating needed within the horizon"
        plan.decision = f"wait – {plan.reason}"
    else:
        plan.decision = plan.reason
    return plan
