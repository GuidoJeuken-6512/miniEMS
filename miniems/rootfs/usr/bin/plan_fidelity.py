"""C4 – did we keep to the plan? (docs/roadmap/gesamtplan.md, C4).

Per actuator channel and minute: target against what the device reports.
A minute counts as kept when the reported value matches the target within
the channel's tolerance for the whole minute's last reading. Results:

- per day and channel: minutes observed, minutes kept → a daily quota,
- deviation periods (consecutive missed minutes) with an explanation:
  the write is still unconfirmed, or the device reports something else.

Nothing is recorded in simulation: there the device never received the
target, so a "deviation" would mean nothing.

Pure apart from the optional store flush.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from store import EnergyStore

MAX_DEVIATIONS = 50


@dataclass(frozen=True)
class ChannelReading:
    name: str                       # e.g. "charge_current"
    label: str
    target: Any                     # float or str
    actual: Any                     # float or str or None
    tolerance: float = 0.5
    confirmed: bool = True          # write channel state

    @property
    def kept(self) -> bool:
        if self.target is None:
            return True
        if self.actual is None:
            return False
        if isinstance(self.target, (int, float)) and not isinstance(self.target, bool):
            try:
                return abs(float(self.actual) - float(self.target)) <= self.tolerance
            except (TypeError, ValueError):
                return False
        return str(self.actual) == str(self.target)

    @property
    def explanation(self) -> str:
        if self.actual is None:
            return "device reports no value"
        if not self.confirmed:
            return "write sent, not confirmed yet"
        return f"device reports {self.actual} instead of {self.target}"


@dataclass
class Deviation:
    channel: str
    label: str
    start: datetime
    end: datetime
    target: Any
    actual: Any
    explanation: str

    def as_dict(self) -> dict:
        return {"channel": self.channel, "label": self.label, "start": self.start.isoformat(),
                "end": self.end.isoformat(), "minutes": round((self.end - self.start).total_seconds() / 60),
                "target": self.target, "actual": self.actual, "explanation": self.explanation}


@dataclass
class DayCounts:
    minutes: int = 0
    kept: int = 0


@dataclass
class PlanJournal:
    day: date | None = None
    counts: dict[str, DayCounts] = field(default_factory=dict)
    deviations: list[Deviation] = field(default_factory=list)
    _open: dict[str, Deviation] = field(default_factory=dict)
    _last_minute: datetime | None = None
    _dirty_days: dict[date, dict[str, DayCounts]] = field(default_factory=dict)

    def record(self, now: datetime, readings: list[ChannelReading], simulation: bool) -> None:
        """Feed one tick; counts at most once per minute."""
        if simulation:
            self._open.clear()
            return
        minute = now.replace(second=0, microsecond=0)
        if self._last_minute == minute:
            return
        self._last_minute = minute
        if self.day != minute.date():
            if self.day is not None and self.counts:
                self._dirty_days[self.day] = self.counts
            self.day = minute.date()
            self.counts = {}
        for r in readings:
            if r.target is None:
                continue
            c = self.counts.setdefault(r.name, DayCounts())
            c.minutes += 1
            if r.kept:
                c.kept += 1
                dev = self._open.pop(r.name, None)
                if dev is not None:
                    dev.end = minute
                    self._push(dev)
            else:
                dev = self._open.get(r.name)
                if dev is None:
                    self._open[r.name] = Deviation(r.name, r.label, minute, minute,
                                                   r.target, r.actual, r.explanation)
                else:
                    dev.end = minute
                    dev.actual, dev.explanation = r.actual, r.explanation

    def _push(self, dev: Deviation) -> None:
        if dev.end > dev.start:
            self.deviations.append(dev)
            del self.deviations[:-MAX_DEVIATIONS]

    def quota(self) -> float | None:
        total = sum(c.minutes for c in self.counts.values())
        if total == 0:
            return None
        return sum(c.kept for c in self.counts.values()) / total

    def status(self, now: datetime) -> dict[str, Any]:
        q = self.quota()
        open_devs = []
        for dev in self._open.values():
            d = Deviation(dev.channel, dev.label, dev.start, now.replace(second=0, microsecond=0),
                          dev.target, dev.actual, dev.explanation)
            open_devs.append(d.as_dict())
        return {
            "day": self.day.isoformat() if self.day else None,
            "quota_pct": None if q is None else round(q * 100, 1),
            "channels": {n: {"minutes": c.minutes, "kept": c.kept,
                             "quota_pct": round(100 * c.kept / c.minutes, 1) if c.minutes else None}
                         for n, c in self.counts.items()},
            "open": open_devs,
            "recent": [d.as_dict() for d in self.deviations[-10:]],
        }

    async def flush(self, store: "EnergyStore | None") -> None:
        """Persist today's quota (and a finished day) into daily_stats."""
        if store is None:
            return
        days = dict(self._dirty_days)
        if self.day is not None and self.counts:
            days[self.day] = self.counts
        for day, counts in days.items():
            total = sum(c.minutes for c in counts.values())
            kept = sum(c.kept for c in counts.values())
            await store.upsert_day(day, {"plan_minutes": total, "plan_kept_minutes": kept})
        self._dirty_days.clear()
