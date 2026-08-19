"""In-memory ring buffer of EMS events for miniEMS, backed by SQLite.

Entries are added whenever:
  - The EMS operating mode changes (Idle / PV Charging / Export Surplus /
    Grid Charging / Battery Protection)             (entry_type="mode_change")
  - The electricity price changes                    (entry_type="price_change")
  - An inverter write channel confirms, fails, or is still unconfirmed at
    shutdown                                          (entry_type="write_confirm")

Each mode_change entry carries the new `mode` and the `reason` the decision
logic produced for it (e.g. "time backstop", "forecast below battery+load need")
so the frontend can explain grid-friendly PV strategy transitions, not just
show that *something* changed.

write_confirm entries exist purely for later analysis (the add-on's own log
buffer is far too short-lived for a multi-day latency picture) – see
InverterController.pop_write_events() – and are excluded from to_list()'s
default output so the human-facing Log page isn't cluttered with them.

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
    state: str                             # "on" | "off" | "price_change" | "write_confirm"
    battery_kwh_freetochange: float
    battery_kwh_useable: float
    predicted_load_kwh: float | None
    entry_type: str = "mode_change"        # "mode_change" | "price_change" | "write_confirm"
    price_eur_kwh: float | None = None     # filled for price_change entries
    mode: str = ""                         # EMSMode.value at the time of this entry
    reason: str = ""                       # ModeDecision.reason, e.g. "time backstop"
    # Filled for write_confirm entries only – see InverterController.pop_write_events()
    write_channel: str | None = None       # e.g. "Discharge current (number.deye8k_...)"
    write_latency_sec: float | None = None # time from becoming unconfirmed to this outcome
    write_outcome: str | None = None       # "confirmed" | "failed" | "unconfirmed_at_shutdown"


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

    def to_list(self, include_write_confirm: bool = False) -> list[dict[str, Any]]:
        """Return entries as JSON-serialisable dicts, newest first.

        write_confirm entries are diagnostic-only (per-channel write
        lifecycle, not a mode/price change a human is watching for) and are
        excluded by default so the Log page stays readable; they remain
        fully queryable in SQLite regardless.
        """
        return [
            asdict(e) for e in reversed(self._buf)
            if include_write_confirm or e.entry_type != "write_confirm"
        ]

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
                write_channel=row.get("write_channel"),
                write_latency_sec=row.get("write_latency_sec"),
                write_outcome=row.get("write_outcome"),
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
