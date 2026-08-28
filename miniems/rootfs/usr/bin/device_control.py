"""The Watt boundary: converts an EMS-level power request (Watts) to/from
whatever unit a device's actuator natively speaks (A, W, %), per a device
profile's `control.<role>` declaration (`device_profile.ActuatorControl`).

See docs/roadmap/v3.0-geraeteprofile.md, "Die Watt-Abstraktion". Pure
conversion/clamping functions plus one small stateful cache
(`LiveLimitsCache`) for the one thing that must survive across ticks: never
letting a momentarily `unavailable` HA attribute silently lower a charge
ceiling.
"""
from __future__ import annotations

from dataclasses import dataclass

from device_profile import ActuatorControl


@dataclass(frozen=True)
class ActuatorLimits:
    """Live (or fallback) min/max/step for one actuator, in the actuator's
    own native unit (A for `dc_current`/`ac_current`, W for `native_power`,
    % for `percent_of_rated`)."""

    min: float
    max: float
    step: float


def to_actuator_value(
    power_w: float,
    control: ActuatorControl,
    *,
    voltage_v: float | None = None,
    phases: int | None = None,
) -> float:
    """EMS Watts -> the actuator's native value, per `control.via`.

    A missing/non-positive voltage on a `dc_current` control falls back to
    the profile's `voltage_fallback_v` rather than raising or returning a
    value that would block the write – an approximation beats stopping the
    write entirely, matching the project's fail-safe style elsewhere (e.g.
    `EMSController._should_hold_pv_charge()`).
    """
    via = control.via
    if via == "dc_current":
        v = voltage_v if voltage_v and voltage_v > 0 else control.voltage_fallback_v
        return power_w / v if v and v > 0 else 0.0
    if via == "ac_current":
        v = control.voltage_fixed_v or 230.0
        n = phases or 1
        return power_w / (v * n) if v > 0 and n > 0 else 0.0
    if via == "percent_of_rated":
        rated = control.rated_power_w
        return 100.0 * power_w / rated if rated and rated > 0 else 0.0
    # "native_power" and any unrecognised via: pass through unchanged - the
    # safest default is to not silently reinterpret a number we don't have a
    # declared conversion for.
    return power_w


def from_actuator_value(
    value: float,
    control: ActuatorControl,
    *,
    voltage_v: float | None = None,
    phases: int | None = None,
) -> float:
    """The inverse of `to_actuator_value()` - reports a *confirmed* actuator
    reading back in Watts (e.g. for the dashboard)."""
    via = control.via
    if via == "dc_current":
        v = voltage_v if voltage_v and voltage_v > 0 else control.voltage_fallback_v
        return value * v if v and v > 0 else 0.0
    if via == "ac_current":
        v = control.voltage_fixed_v or 230.0
        n = phases or 1
        return value * v * n
    if via == "percent_of_rated":
        rated = control.rated_power_w
        return value / 100.0 * rated if rated and rated > 0 else 0.0
    return value


def clamp_to_limits(value: float, limits: ActuatorLimits) -> float:
    """Clamp to [min, max] and quantise to the nearest step – correctness
    the pre-profile ampere code never had (a bare `int()` truncation)."""
    if limits.step and limits.step > 0:
        value = round(value / limits.step) * limits.step
    return max(limits.min, min(limits.max, value))


def resolve_limits(
    live_min: float | None,
    live_max: float | None,
    live_step: float | None,
    control: ActuatorControl,
) -> ActuatorLimits:
    """Live entity attributes first, the profile's `fallback_limits` second –
    per field, not as an all-or-nothing switch, so an actuator exposing only
    `max` still benefits from a live `min`/`step` if present."""
    fb = control.fallback_limits or {}
    lo = live_min if live_min is not None else fb.get("min", 0.0)
    hi = live_max if live_max is not None else fb.get("max", 0.0)
    step = live_step if live_step is not None else fb.get("step", 1.0)
    return ActuatorLimits(min=float(lo), max=float(hi), step=float(step))


class LiveLimitsCache:
    """Remembers the last-good `max` per actuator entity, so a momentarily
    `unavailable` HA attribute never silently lowers a charge/discharge
    ceiling – a transient Solarman hiccup must never look like "the battery
    can only take 0 A now" and block charging. `min`/`step` are re-resolved
    fresh every call (their fallback defaults are never wrong-side-unsafe
    the way a stale-low `max` would be, so there is nothing to cache there).
    """

    def __init__(self) -> None:
        self._last_good_max: dict[str, float] = {}

    def resolve(
        self,
        entity_id: str,
        live_min: float | None,
        live_max: float | None,
        live_step: float | None,
        control: ActuatorControl,
    ) -> ActuatorLimits:
        limits = resolve_limits(live_min, live_max, live_step, control)
        if live_max is not None:
            self._last_good_max[entity_id] = limits.max
        elif entity_id in self._last_good_max:
            limits = ActuatorLimits(min=limits.min, max=self._last_good_max[entity_id], step=limits.step)
        return limits
