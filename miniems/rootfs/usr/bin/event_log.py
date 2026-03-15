"""In-memory ring buffer of EMS events for miniEMS.

Entries are added whenever:
  - The EMS switches grid-charge mode on or off  (entry_type="mode_change")
  - The electricity price changes                (entry_type="price_change")

The buffer is capped at max_entries (oldest entries evicted automatically).
to_list() returns entries newest-first for the frontend log panel.
"""
from collections import deque
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class LogEntry:
    timestamp: str                         # ISO 8601 string
    state: str                             # "on" | "off" | "price_change"
    battery_kwh_freetochange: float
    battery_kwh_useable: float
    predicted_load_kwh: float | None
    entry_type: str = "mode_change"        # "mode_change" | "price_change"
    price_eur_kwh: float | None = None     # filled for price_change entries


class EventLog:
    """Fixed-size ring buffer of LogEntry objects."""

    def __init__(self, max_entries: int = 100) -> None:
        self._buf: deque[LogEntry] = deque(maxlen=max_entries)

    def append(self, entry: LogEntry) -> None:
        """Add an entry; oldest is automatically dropped when buffer is full."""
        self._buf.append(entry)

    def to_list(self) -> list[dict[str, Any]]:
        """Return all entries as JSON-serialisable dicts, newest first."""
        return [asdict(e) for e in reversed(self._buf)]
