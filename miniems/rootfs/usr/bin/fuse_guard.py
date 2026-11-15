"""B1 – main-fuse guard and §14a dimming (docs/roadmap/gesamtplan.md, B1).

Watches the current per phase at the grid connection and tells every
controllable large load (grid charging of the battery, the wallbox) how much
it may draw. The guard only ever *reduces*: it never starts a load.

Thermal model, as a fuse heats up: each phase's I² is low-pass filtered with
τ = 60 s, so a short inrush peak does not throttle but a sustained overload
does. Two triggers throttle:

- the filtered (thermal) current exceeds the working limit
  (``rated × work_limit_pct``), and
- any single reading exceeds ``TRIP_FACTOR × rated`` – immediately, without
  waiting for the filter.

After a throttle no increase is allowed for HOLD_SEC, so a load that was just
reduced does not climb straight back into the overload.

Phases that export (power < 0) are never the reason for a throttle: more load
there first cancels the export.

Stale or missing readings never count as "the house draws 0 A". The guard
assumes the learned base current of the phase (what flows when no controlled
load runs) plus a margin, or half the rated current before anything was
learned.

Pure logic: no Home Assistant access, time is passed in.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

TAU_SEC = 60.0          # thermal filter of I² per phase
TRIP_FACTOR = 1.2       # single reading above this × rated → throttle at once
HOLD_SEC = 20.0         # no increase for this long after a throttle
BASE_TAU_SEC = 3600.0   # learning of the uncontrolled base current per phase
STALE_MARGIN_A = 6.0    # added to the learned base current when readings are stale
NOMINAL_V = 230.0


@dataclass
class PhaseState:
    i2_filtered: float | None = None      # A²
    base_a: float | None = None           # learned uncontrolled current
    last_a: float | None = None           # last single reading
    exporting: bool = False


@dataclass
class FuseStatus:
    """What the guard knows right now, for the dashboard and the reason text."""

    active: bool
    stale: bool
    phases_a: list[float | None]          # thermal (filtered) current per phase
    headroom_a: float | None              # smallest headroom of the importing phases
    throttling: bool
    tripped: bool
    reason: str
    dimming: bool = False
    dimming_limit_w: float | None = None
    limits_w: dict[str, float] = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "active": self.active,
            "stale": self.stale,
            "phases_a": [None if a is None else round(a, 1) for a in self.phases_a],
            "headroom_a": None if self.headroom_a is None else round(self.headroom_a, 1),
            "throttling": self.throttling,
            "tripped": self.tripped,
            "reason": self.reason,
            "dimming": self.dimming,
            "dimming_limit_w": self.dimming_limit_w,
            "limits_w": {k: round(v) for k, v in self.limits_w.items()},
        }


class FuseGuard:
    """Per-phase thermal model of the main fuse."""

    def __init__(self, rated_a: float, work_limit_pct: float = 90.0, phases: int = 3,
                 voltage_v: float = NOMINAL_V) -> None:
        self.rated_a = float(rated_a)
        self.work_limit_a = self.rated_a * work_limit_pct / 100.0
        self.voltage_v = voltage_v
        self._phases = [PhaseState() for _ in range(phases)]
        self._last_t: float | None = None
        self._hold_until: float | None = None
        self._stale = True
        self._tripped = False
        self._over = False
        self._reason = "no readings yet"

    # ------------------------------------------------------------------
    # Input
    # ------------------------------------------------------------------

    def update(self, t: float, currents_a: list[float | None] | None,
               powers_w: list[float | None] | None = None,
               controlled_a: float = 0.0) -> None:
        """Feed one set of readings taken at monotonic time ``t``.

        ``currents_a`` – unsigned current per phase, None = missing/stale.
        ``powers_w``   – optional signed power per phase (+ = import), used
                         only to recognise an exporting phase.
        ``controlled_a`` – current per phase the controlled loads draw right
                         now (symmetric share), excluded from the learned base.
        """
        dt = 0.0 if self._last_t is None else max(0.0, t - self._last_t)
        self._last_t = t
        n = len(self._phases)
        currents = list(currents_a or [None] * n)[:n] + [None] * max(0, n - len(currents_a or []))
        powers = list(powers_w or [None] * n)[:n] + [None] * max(0, n - len(powers_w or []))
        self._stale = any(c is None for c in currents)
        alpha = 1.0 if dt <= 0 else 1.0 - math.exp(-dt / TAU_SEC)
        beta = 0.0 if dt <= 0 else 1.0 - math.exp(-dt / BASE_TAU_SEC)

        tripped = False
        for ph, cur, pw in zip(self._phases, currents, powers):
            ph.exporting = pw is not None and pw < 0
            ph.last_a = cur
            if cur is None:
                continue
            i2 = cur * cur
            ph.i2_filtered = i2 if ph.i2_filtered is None else ph.i2_filtered + alpha * (i2 - ph.i2_filtered)
            uncontrolled = max(0.0, cur - max(0.0, controlled_a))
            if ph.base_a is None:
                ph.base_a = uncontrolled
            elif beta > 0:
                ph.base_a += beta * (uncontrolled - ph.base_a)
            if not ph.exporting and cur > TRIP_FACTOR * self.rated_a:
                tripped = True

        over = any(not ph.exporting and a is not None and a > self.work_limit_a
                   for ph, a in zip(self._phases, self.thermal_a()))
        if tripped or over:
            self._hold_until = t + HOLD_SEC
        self._tripped = tripped
        self._over = over
        if tripped:
            self._reason = f"single reading above {TRIP_FACTOR:.1f} × {self.rated_a:.0f} A"
        elif over:
            self._reason = f"thermal current above working limit {self.work_limit_a:.0f} A"
        elif self._stale:
            self._reason = "phase currents stale – assuming base load + margin"
        else:
            self._reason = "ok"

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def thermal_a(self) -> list[float | None]:
        """Effective (filtered) current per phase, stale phases estimated."""
        out: list[float | None] = []
        for ph in self._phases:
            if ph.last_a is None or ph.i2_filtered is None:
                out.append(self._assumed_a(ph))
            else:
                out.append(math.sqrt(max(0.0, ph.i2_filtered)))
        return out

    def _assumed_a(self, ph: PhaseState) -> float:
        """Never 0 A: learned base + margin, else half the rated current."""
        if ph.base_a is not None:
            return ph.base_a + STALE_MARGIN_A
        return 0.5 * self.rated_a

    def headroom_a(self) -> float:
        """Smallest headroom over the importing phases (may be negative)."""
        rooms = []
        for ph, a in zip(self._phases, self.thermal_a()):
            if ph.exporting:
                rooms.append(self.work_limit_a)
                continue
            now_a = a if a is not None else self._assumed_a(ph)
            if ph.last_a is not None:
                # A fresh single reading above the thermal value counts too:
                # the filter lags behind a load that just started.
                now_a = max(now_a, ph.last_a) if ph.last_a > self.work_limit_a else now_a
            rooms.append(self.work_limit_a - now_a)
        return min(rooms) if rooms else 0.0

    def in_hold(self, t: float) -> bool:
        return self._hold_until is not None and t < self._hold_until

    def limit_w(self, t: float, own_w: float, phases: int = 3) -> float:
        """Most a load drawing ``own_w`` now may draw after this reading.

        ``phases`` – how many phases the load uses (symmetric share). The
        result is never negative. While the hold time runs, the load may not
        rise above what it draws now.
        """
        own_w = max(0.0, own_w)
        if self._tripped:
            # Immediate: drop the controlled load entirely, the filter catches up.
            return 0.0
        room_w = self.headroom_a() * self.voltage_v * max(1, phases)
        limit = max(0.0, own_w + room_w)
        if self.in_hold(t):
            limit = min(limit, own_w)
        return limit

    def status(self, t: float, dimming: bool = False, dimming_limit_w: float | None = None,
               limits_w: dict[str, float] | None = None) -> FuseStatus:
        return FuseStatus(
            active=True,
            stale=self._stale,
            phases_a=self.thermal_a(),
            headroom_a=self.headroom_a(),
            throttling=self._over or self._tripped or self.in_hold(t),
            tripped=self._tripped,
            reason=self._reason,
            dimming=dimming,
            dimming_limit_w=dimming_limit_w if dimming else None,
            limits_w=dict(limits_w or {}),
        )


def dimming_share_w(limit_w: float, demands_w: dict[str, float],
                    order: list[str]) -> dict[str, float]:
    """§14a: split the guaranteed minimum power over the controllable loads.

    ``demands_w`` – what each load wants now; ``order`` – priority, first
    served first. Loads not in ``order`` come last in name order. Every load
    gets at most its demand, the sum never exceeds ``limit_w``.
    """
    remaining = max(0.0, limit_w)
    names = [n for n in order if n in demands_w] + sorted(n for n in demands_w if n not in order)
    out: dict[str, float] = {}
    for name in names:
        give = min(max(0.0, demands_w[name]), remaining)
        out[name] = give
        remaining -= give
    return out
