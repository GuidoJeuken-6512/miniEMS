"""In-memory ring buffer of EMS events for miniEMS, backed by SQLite.

Entries are added whenever:
  - The EMS operating mode changes (Idle / PV Charging / Export Surplus /
    Grid Charging / Battery Protection)             (entry_type="mode_change")
  - The electricity price changes                    (entry_type="price_change")

Each mode_change entry carries the new `mode` and the `reason` the decision
logic produced for it (e.g. "time backstop", "forecast below battery need")
so the frontend can explain grid-friendly PV strategy transitions, not just
show that *something* changed.

The buffer is capped at max_entries (oldest entries evicted automatically).
to_list() returns entries newest-first for the frontend log panel.

Persistence:
  - Every entry is written to the `event_log` SQLite table immediately.
  - On startup, restore_from_db() repopulates the in-memory buffer.
  - cleanup_old_entries() removes rows older than the configured retention window.
"""
import logging
from collections import deque
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)


@dataclass
class LogEntry:
    timestamp: str                         # ISO 8601 string
    state: str                             # "on" | "off" | "price_change"
    battery_kwh_freetochange: float
    battery_kwh_useable: float
    predicted_load_kwh: float | None
    entry_type: str = "mode_change"        # "mode_change" | "price_change"
    price_eur_kwh: float | None = None     # filled for price_change entries
    mode: str = ""                         # EMSMode.value at the time of this entry
    reason: str = ""                       # ModeDecision.reason, e.g. "time backstop"


class EventLog:
    """Fixed-size ring buffer of LogEntry objects, persisted to SQLite."""

    def __init__(self, max_entries: int = 100, store: "EnergyStore | None" = None) -> None:
        self._buf: deque[LogEntry] = deque(maxlen=max_entries)
        self._store = store

    async def append(self, entry: LogEntry) -> None:
        """Add to in-memory buffer and persist to DB."""
        self._buf.append(entry)
        if self._store:
            await self._store.append_event(asdict(entry))

    def to_list(self) -> list[dict[str, Any]]:
        """Return all entries as JSON-serialisable dicts, newest first."""
        return [asdict(e) for e in reversed(self._buf)]

    async def restore_from_db(self) -> None:
        """Load the last N entries from DB into the in-memory buffer on startup."""
        if not self._store:
            return
        rows = await self._store.load_recent_events(limit=self._buf.maxlen or 100)
        # rows are newest-first; reverse so oldest enters the deque first
        for row in reversed(rows):
            self._buf.append(LogEntry(
                timestamp=row["timestamp"],
                state=row["state"],
                battery_kwh_freetochange=row["battery_kwh_freetochange"],
                battery_kwh_useable=row["battery_kwh_useable"],
                predicted_load_kwh=row["predicted_load_kwh"],
                entry_type=row["entry_type"],
                price_eur_kwh=row["price_eur_kwh"],
                mode=row.get("mode") or "",
                reason=row.get("reason") or "",
            ))
        _LOGGER.info("EventLog: restored %d entries from DB", len(rows))

    async def cleanup_old_entries(self, retention_days: int) -> None:
        """Remove DB rows older than retention_days. Called once per day."""
        if not self._store:
            return
        deleted = await self._store.cleanup_event_log(retention_days)
        if deleted:
            _LOGGER.info(
                "EventLog: removed %d entries older than %d days", deleted, retention_days
            )
