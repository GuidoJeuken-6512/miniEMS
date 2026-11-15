"""B5 – EV charging from PV surplus in real time (docs/roadmap/gesamtplan.md, B5).

A small state machine, fed every tick with the power the EV may take
(``available_w``: what it draws now minus the grid import, plus the battery's
charge power when the battery yields). It answers: run or pause, how many
amps, how many phases.

- Start once the surplus reaches the single-phase minimum (≈ 1.4 kW at 6 A),
  always single-phase first. Stays single-phase while the sun is changeable.
- Switch to three phases from a computed 18 A single-phase, back below 5.5 A
  three-phase. After every switch SWITCH_LOCK_SEC lock (contactor wear).
- Readjust every ADJUST_SEC, every ADJUST_FAST_SEC while the car buys
  expensive grid power.
- Run at least MIN_RUN_SEC after a start (at the minimum current if the sun
  goes). Then pause with 0 A – the session is not ended.

Pure: time is passed in (monotonic seconds).
"""
from __future__ import annotations

import math
from collections import deque
from dataclasses import dataclass

TO_THREE_PHASE_A = 18.0
TO_ONE_PHASE_A = 5.5
SWITCH_LOCK_SEC = 15 * 60
ADJUST_SEC = 5 * 60
ADJUST_FAST_SEC = 60
MIN_RUN_SEC = 30 * 60
VARIABILITY_WINDOW_SEC = 10 * 60
VARIABLE_SPREAD_W = 2000.0


@dataclass(frozen=True)
class SurplusCommand:
    charging: bool
    current_a: float
    phases: int
    reason: str


class SurplusRegulator:
    def __init__(self, min_a: float, max_a: float, phases_max: int,
                 w_per_a_1p: float, w_per_a_max: float, can_switch: bool) -> None:
        self.min_a = min_a
        self.max_a = max_a
        self.phases_max = phases_max
        self._w_per_a = {1: w_per_a_1p, phases_max: w_per_a_max}
        self.can_switch = can_switch and phases_max > 1
        self.phases = 1 if self.can_switch else phases_max
        self.charging = False
        self.current_a = 0.0
        self.started_at: float | None = None
        self.switched_at: float | None = None
        self.adjusted_at: float | None = None
        self._samples: deque[tuple[float, float]] = deque()
        self.reason = "waiting for surplus"

    def w_per_a(self, phases: int | None = None) -> float:
        return self._w_per_a.get(phases or self.phases, self._w_per_a[1])

    def min_w(self) -> float:
        return self.min_a * self.w_per_a(1 if self.can_switch else self.phases_max)

    def reset(self) -> None:
        """Unplugged or mode changed: forget the session."""
        self.__init__(self.min_a, self.max_a, self.phases_max, self._w_per_a[1],
                      self._w_per_a[self.phases_max], self.can_switch)

    def _variable(self, t: float, available_w: float) -> bool:
        self._samples.append((t, available_w))
        while self._samples and self._samples[0][0] < t - VARIABILITY_WINDOW_SEC:
            self._samples.popleft()
        values = [v for _, v in self._samples]
        return len(values) >= 2 and (max(values) - min(values)) > VARIABLE_SPREAD_W

    def _locked(self, t: float) -> bool:
        return self.switched_at is not None and t - self.switched_at < SWITCH_LOCK_SEC

    def step(self, t: float, available_w: float, expensive_import: bool = False,
             cap_w: float | None = None) -> SurplusCommand:
        """One tick. ``cap_w`` – fuse/§14a limit for the EV (None = none)."""
        variable = self._variable(t, available_w)
        if cap_w is not None:
            available_w = min(available_w, cap_w)

        if not self.charging:
            if available_w >= self.min_w():
                self.charging = True
                self.started_at = t
                self.adjusted_at = t
                self.phases = 1 if self.can_switch else self.phases_max
                self.current_a = self._clamp(available_w / self.w_per_a())
                self.reason = f"pv surplus {available_w:.0f} W – start {self.phases}-phase"
            else:
                self.current_a = 0.0
                self.reason = f"pv surplus {available_w:.0f} W below {self.min_w():.0f} W"
            return self._cmd()

        interval = ADJUST_FAST_SEC if expensive_import else ADJUST_SEC
        over_cap = cap_w is not None and self.current_a * self.w_per_a() > cap_w + 1.0
        if (self.adjusted_at is not None and t - self.adjusted_at < interval
                and not over_cap):
            return self._cmd()
        self.adjusted_at = t

        target_a = available_w / self.w_per_a()
        switched = False
        if self.can_switch and not self._locked(t):
            if self.phases == 1 and target_a >= TO_THREE_PHASE_A and not variable:
                self.phases = self.phases_max
                self.switched_at = t
                target_a = available_w / self.w_per_a()
                switched = True
                self.reason = f"pv surplus {available_w:.0f} W – switch to {self.phases} phases"
            elif self.phases > 1 and target_a < TO_ONE_PHASE_A:
                self.phases = 1
                self.switched_at = t
                target_a = available_w / self.w_per_a()
                switched = True
                self.reason = f"pv surplus {available_w:.0f} W – switch to 1 phase"

        if target_a < self.min_a:
            in_min_run = self.started_at is not None and t - self.started_at < MIN_RUN_SEC
            if in_min_run and not over_cap:
                self.current_a = self.min_a
                self.reason = "minimum run time – holding minimum current"
                return self._cmd()
            self.charging = False
            self.current_a = 0.0
            self.reason = (f"pv surplus {available_w:.0f} W gone – pause at 0 A"
                           if not over_cap else "fuse limit – pause at 0 A")
            return self._cmd()

        self.current_a = self._clamp(target_a)
        if not switched:
            self.reason = f"pv surplus {available_w:.0f} W – {self.current_a:.0f} A × {self.phases}"
        return self._cmd()

    def _clamp(self, amps: float) -> float:
        return float(max(self.min_a, min(self.max_a, math.floor(amps))))

    def _cmd(self) -> SurplusCommand:
        return SurplusCommand(self.charging, self.current_a if self.charging else 0.0,
                              self.phases, self.reason)
