"""Logs when a monitored sensor drops out and when it comes back.

The dashboard already lists unavailable sensors, but nothing reached the add-on
log – so a gap in the energy accounting (which counts a missing power reading
as 0 W) could not be traced back to the sensor that caused it. Logs only on
transitions, not every tick.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime

_LOGGER = logging.getLogger(__name__)

_UNAVAILABLE = ("unavailable", "unknown", "")


@dataclass
class _Outage:
    since: datetime
    misses: int = 0
    logged: bool = False
    state: str | None = None


class SensorAvailabilityMonitor:
    def __init__(self, debounce_ticks: int = 2) -> None:
        # A single missing tick (HA restart, reconnect) is not worth a warning.
        self._debounce = max(1, debounce_ticks)
        self._outages: dict[str, _Outage] = {}

    @property
    def unavailable(self) -> list[str]:
        return [e for e, o in self._outages.items() if o.logged]

    def update(
        self,
        now: datetime,
        entities: dict[str, str],
        raw_state: Callable[[str], str | None],
        impact: dict[str, str] | None = None,
    ) -> None:
        """`entities` maps entity_id → readable label; `impact` optionally adds
        what the outage means for an entity (e.g. "accounting counts 0 W")."""
        impact = impact or {}
        for entity, label in entities.items():
            raw = raw_state(entity)
            outage = self._outages.get(entity)
            if raw is None or raw in _UNAVAILABLE:
                if outage is None:
                    outage = self._outages[entity] = _Outage(since=now)
                outage.misses += 1
                outage.state = raw
                if outage.misses >= self._debounce and not outage.logged:
                    outage.logged = True
                    what = "not found in Home Assistant" if raw is None else f"state {raw!r}"
                    note = f" – {impact[entity]}" if entity in impact else ""
                    _LOGGER.warning(
                        "Sensor unavailable: %s (%s), %s since %s%s",
                        label, entity, what, outage.since.strftime("%H:%M:%S"), note,
                    )
            elif outage is not None:
                del self._outages[entity]
                if outage.logged:
                    _LOGGER.info(
                        "Sensor available again: %s (%s) after %.0f s",
                        label, entity, (now - outage.since).total_seconds(),
                    )
        for entity in [e for e in self._outages if e not in entities]:
            del self._outages[entity]   # no longer configured
