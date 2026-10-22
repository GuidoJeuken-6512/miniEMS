"""Tariff preview built from an HA price entity's attributes.

Until now miniEMS reduced the tariff to a single question: "is the price right
now below a threshold?" (`CostOptimizer.is_cheap_rate`). That throws away a
calendar the entity already carries, and it is the reason grid charging can fire
in the 12:00-16:00 cheap window – economically pointless, because PV covers that
time of day, and grid-adverse, because everything else is exporting then.

Three sources are tried, best first:

1. `rates[]` / `unit_rate_forecast[]` – half-hourly prices of a market tariff
   (Tibber, aWATTar, Octopus Agile). Present but empty on a ToU contract.
2. `timeslots[]` – the time-of-use calendar. Each entry carries a name, a rate in
   ct/kWh, and `activation_rules[]` with the windows it applies to. Windows may
   wrap past midnight (STANDARD runs 21:00-02:00).
3. Nothing usable -> from_entity() returns None and callers keep their previous
   behaviour. This is not a fallback worth hiding: a wrong tariff preview would
   make worse decisions than no preview at all.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class _Window:
    """One tariff window on a generic day. `end` <= `start` means it wraps."""
    start: time
    end: time
    rate_eur_kwh: float
    name: str

    @property
    def wraps_midnight(self) -> bool:
        return self.end <= self.start

    def contains(self, t: time) -> bool:
        if self.wraps_midnight:
            return t >= self.start or t < self.end
        return self.start <= t < self.end


class PriceCurve:
    """Read-only view of the upcoming tariff, in local time."""

    def __init__(
        self,
        windows: list[_Window],
        source: str,
        dated: list[tuple[datetime, datetime, float]] | None = None,
    ) -> None:
        self._windows = windows
        self.source = source
        # Dated slots of a market tariff (v3.3.0), merged where consecutive
        # slots share a rate: [(start, end, €/kWh)], tz-aware, sorted. When
        # set, it replaces the recurring `_windows` calendar entirely.
        self._dated = dated or []

    @property
    def is_dated(self) -> bool:
        return bool(self._dated)

    # ------------------------------------------------------------------

    @classmethod
    def from_entity(
        cls, ws: "HAStateClient", entity_id: str, source: str = "auto"
    ) -> "PriceCurve | None":
        """Build from a price entity, or None when it carries no calendar.

        `source` (config `tariff_source`): "auto" tries dated market rates
        first and falls back to the time-of-use calendar; "rates" and
        "timeslots" use only that one.
        """
        if not entity_id:
            return None

        if source in ("auto", "rates"):
            for attr in ("rates", "unit_rate_forecast", "prices", "forecast"):
                dated = _parse_dated_rates(ws.get_state_attribute(entity_id, attr))
                if dated:
                    return cls([], source=f"{attr}({entity_id})", dated=dated)
            if source == "rates":
                return None

        timeslots = ws.get_state_attribute(entity_id, "timeslots")
        if not isinstance(timeslots, list) or not timeslots:
            return None

        windows: list[_Window] = []
        for slot in timeslots:
            if not isinstance(slot, dict):
                continue
            name = str(slot.get("name", "?"))
            try:
                # Published in ct/kWh ("27.4414"), used everywhere else as €/kWh.
                rate = float(slot["rate"]) / 100.0
            except (KeyError, TypeError, ValueError):
                _LOGGER.warning("PriceCurve: timeslot %r has no usable rate – skipped", name)
                continue
            for rule in slot.get("activation_rules") or []:
                start = _parse_time(rule.get("from_time"))
                end = _parse_time(rule.get("to_time"))
                if start is None or end is None:
                    _LOGGER.warning(
                        "PriceCurve: unparsable window %r in timeslot %r – skipped", rule, name)
                    continue
                windows.append(_Window(start, end, rate, name))

        if not windows:
            return None
        return cls(windows, source=f"timeslots({entity_id})")

    # ------------------------------------------------------------------

    def window_at(self, moment: datetime) -> _Window | None:
        """The window covering `moment`, or None if the calendar has a hole."""
        if self._dated:
            slot = self._dated_slot(moment)
            if slot is None:
                return None
            return _Window(slot[0].timetz().replace(tzinfo=None),
                           slot[1].timetz().replace(tzinfo=None), slot[2], "rate")
        t = moment.timetz().replace(tzinfo=None)
        for w in self._windows:
            if w.contains(t):
                return w
        return None

    def window_end(self, moment: datetime) -> datetime | None:
        """When the current tariff window ends – the value V1 needs.

        Returned as an absolute datetime so a window running past midnight
        yields tomorrow's date rather than a time that looks like the past.
        """
        if self._dated:
            slot = self._dated_slot(moment)
            return slot[1].astimezone(moment.tzinfo) if slot else None
        w = self.window_at(moment)
        if w is None:
            return None
        end = datetime.combine(moment.date(), w.end, tzinfo=moment.tzinfo)
        if end <= moment:
            end += timedelta(days=1)
        return end

    def rate_at(self, moment: datetime) -> float | None:
        """€/kWh at `moment`, or None when the calendar does not cover it."""
        w = self.window_at(moment)
        return w.rate_eur_kwh if w is not None else None

    def _dated_slot(self, moment: datetime) -> tuple[datetime, datetime, float] | None:
        for start, end, rate in self._dated:
            if start <= moment < end:
                return start, end, rate
        return None

    def cheapest_rate_between(self, start: datetime, deadline: datetime) -> float | None:
        """Lowest rate occurring in [start, deadline), or None when unknown."""
        rates = [w.rate_eur_kwh for w in self._windows_between(start, deadline)]
        return min(rates) if rates else None

    def later_window_as_cheap(
        self, now: datetime, deadline: datetime, charge_duration: timedelta
    ) -> bool:
        """Can charging be safely postponed to a later, equally-cheap window?

        `is_cheapest_now()` cannot answer this on its own: two windows at the
        identical rate (this installation's NIEDRIG at 02-06 *and* 12-16) are
        both "as cheap as anything before the deadline", so it says yes to
        both, including the one in the middle of PV production that this
        method exists to avoid.

        True when a window starting after the one covering `now` ends – but
        still early enough that charging for `charge_duration` finishes
        before `deadline` – has a rate no higher than the current window's.
        Deferring then costs nothing and still meets the deadline. False
        whenever no such window exists, the calendar has a hole, or there
        simply is no time left to defer – "don't defer" always just means
        charging slightly earlier than strictly necessary, so every unclear
        case resolves that way, not the other.
        """
        current = self.window_at(now)
        if current is None:
            return False
        latest_feasible_start = deadline - charge_duration
        if latest_feasible_start <= now:
            return False
        window_end_now = self.window_end(now)
        if window_end_now is None or window_end_now >= latest_feasible_start:
            return False
        cheapest_later = self.cheapest_rate_between(window_end_now, latest_feasible_start)
        if cheapest_later is None:
            return False
        return cheapest_later <= current.rate_eur_kwh

    def is_cheapest_now(
        self, moment: datetime, deadline: datetime, tolerance_eur: float = 0.0005
    ) -> bool | None:
        """Is the current window as cheap as anything before `deadline`?

        This is the question `is_cheap_rate()` cannot answer: not "is it cheap
        right now" but "will it get cheaper before I need the energy". None when
        the calendar cannot say – callers must not read that as False.
        """
        current = self.window_at(moment)
        cheapest = self.cheapest_rate_between(moment, deadline)
        if current is None or cheapest is None:
            return None
        return current.rate_eur_kwh <= cheapest + tolerance_eur

    def windows_before(
        self, start: datetime, deadline: datetime
    ) -> list[tuple[datetime, datetime, float]]:
        """Every distinct window in [start, deadline), as real, date-anchored
        (window_start, window_end, rate) tuples clipped to that span.

        Unlike `_windows_between()` (which returns the abstract, recurring
        `_Window` objects – no notion of a specific occurrence), this gives
        the actual datetimes a caller needs to compute a duration or a
        capacity, e.g. energy_plan.py's fill-cheapest-first schedule. A
        window already in progress at `start` is clipped to start there, not
        at its normal start-of-day time.
        """
        if deadline <= start:
            return []
        result: list[tuple[datetime, datetime, float]] = []
        cursor = start
        while cursor < deadline:
            w = self.window_at(cursor)
            if w is None:
                # Calendar gap – advance in small steps, same granularity as
                # _windows_between(), rather than getting stuck.
                cursor += timedelta(minutes=15)
                continue
            w_end = self.window_end(cursor)
            if w_end is None:
                break
            clipped_end = min(w_end, deadline)
            result.append((cursor, clipped_end, w.rate_eur_kwh))
            cursor = clipped_end
        return result

    # ------------------------------------------------------------------

    def _windows_between(self, start: datetime, deadline: datetime) -> list[_Window]:
        """Every window touched by [start, deadline), walked in 15-min steps.

        Stepping beats interval arithmetic here: windows wrap midnight, the span
        can cross several days, and a 15-minute grid cannot miss a window because
        the shortest real one is two hours.
        """
        if deadline <= start:
            return []
        seen: list[_Window] = []
        cursor = start
        step = timedelta(minutes=15)
        while cursor < deadline:
            w = self.window_at(cursor)
            if w is not None and w not in seen:
                seen.append(w)
            cursor += step
        return seen


def _parse_time(raw: object) -> time | None:
    """"02:00:00" / "02:00" -> time. None when unusable."""
    if not isinstance(raw, str):
        return None
    for fmt in ("%H:%M:%S", "%H:%M"):
        try:
            return datetime.strptime(raw, fmt).time()
        except ValueError:
            continue
    return None


_RATE_START_KEYS = ("start", "period_start", "startsAt", "start_time", "from")
_RATE_END_KEYS = ("end", "period_end", "endsAt", "end_time", "to")
_RATE_VALUE_KEYS = ("value_inc_vat", "total", "price", "value", "rate")


def _to_dt(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else None   # a naive time is ambiguous – reject


def _parse_dated_rates(raw: object) -> list[tuple[datetime, datetime, float]]:
    """Market-tariff price list -> merged [(start, end, €/kWh)].

    Accepts the common shapes (Octopus `rates[]` with value_inc_vat, Tibber
    `startsAt`/`total`, generic start/end/price). A missing end is taken
    from the next entry's start (else +1 h). Values above 2 are read as
    ct/kWh – no retail tariff costs 2 €/kWh, and no ct value is below 2
    outside negative-price hours, where the sign alone already decides.
    Empty list whenever the shape is not recognised.
    """
    if not isinstance(raw, list) or not raw:
        return []
    parsed: list[tuple[datetime, datetime | None, float]] = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        start = next((_to_dt(entry[k]) for k in _RATE_START_KEYS if k in entry), None)
        if start is None:
            continue
        end = next((_to_dt(entry[k]) for k in _RATE_END_KEYS if k in entry), None)
        value = None
        for k in _RATE_VALUE_KEYS:
            if k in entry:
                try:
                    value = float(entry[k])
                except (TypeError, ValueError):
                    value = None
                break
        if value is None:
            continue
        if value > 2.0:
            value /= 100.0
        parsed.append((start, end, value))
    if not parsed:
        return []
    parsed.sort(key=lambda p: p[0])
    slots: list[tuple[datetime, datetime, float]] = []
    for i, (start, end, value) in enumerate(parsed):
        if end is None:
            end = parsed[i + 1][0] if i + 1 < len(parsed) else start + timedelta(hours=1)
        if end <= start:
            continue
        if slots and slots[-1][1] == start and abs(slots[-1][2] - value) < 1e-9:
            slots[-1] = (slots[-1][0], end, value)
        else:
            slots.append((start, end, value))
    return slots
