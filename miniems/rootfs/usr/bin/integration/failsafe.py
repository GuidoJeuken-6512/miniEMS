"""Failsafe watch (A10): reset the add-on's actuators when it goes quiet.

On a clean shutdown the add-on resets every actuator itself
(`restore_safe_defaults()`). A crash, SIGKILL or a hung loop never gets that
far, and the last state stays on the devices – grid charging on, discharge
blocked, a boiler setpoint far above the idle value.

The add-on therefore publishes, with every status, the service calls that
undo its control (`failsafe`: timeout and actions). This integration runs in
the Home Assistant core, independent of the add-on, keeps the last copy and
runs it once when the add-on has not reported for longer than the timeout.

Pure logic without Home Assistant imports, so it can be tested on its own.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any


def valid_spec(raw: Any) -> dict[str, Any] | None:
    """The add-on's `failsafe` block, or None when absent or malformed."""
    if not isinstance(raw, dict):
        return None
    try:
        timeout = float(raw.get("timeout_min", 0))
    except (TypeError, ValueError):
        return None
    actions = raw.get("actions")
    if timeout <= 0 or not isinstance(actions, list):
        return None
    clean = []
    for action in actions:
        if not isinstance(action, dict):
            continue
        service = action.get("service")
        data = action.get("data", {})
        if isinstance(service, str) and service.count(".") == 1 and isinstance(data, dict):
            clean.append({"service": service, "data": data})
    if not clean:
        return None
    return {"timeout_min": timeout, "actions": clean}


class FailsafeWatch:
    """Tracks the last good status and decides when to reset."""

    def __init__(self, spec: dict[str, Any] | None = None) -> None:
        self.spec = valid_spec(spec)
        self.last_ok: datetime | None = None
        self.fired_at: datetime | None = None

    def on_success(self, data: dict[str, Any], now: datetime) -> bool:
        """A good status arrived. True when this ends a fired failsafe."""
        self.last_ok = now
        self.spec = valid_spec(data.get("failsafe"))
        recovered = self.fired_at is not None
        self.fired_at = None
        return recovered

    def due(self, now: datetime) -> list[dict[str, Any]] | None:
        """The actions to run now, once per outage – else None.

        Nothing is due before the first good status of this session: without
        it there is no proof the add-on ever controlled anything, and a copy
        from storage may be stale.
        """
        if self.spec is None or self.last_ok is None or self.fired_at is not None:
            return None
        if now - self.last_ok < timedelta(minutes=self.spec["timeout_min"]):
            return None
        self.fired_at = now
        return list(self.spec["actions"])
