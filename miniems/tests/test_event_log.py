"""EventLog: ring buffer + SQLite persistence, against a fake store."""
import pytest

from event_log import EventLog, LogEntry


class FakeStore:
    def __init__(self) -> None:
        self.appended: list[dict] = []
        self.cleanup_calls: list[int] = []
        self.recent: list[dict] = []

    async def append_event(self, entry: dict) -> None:
        self.appended.append(entry)

    async def load_recent_events(self, limit: int = 100) -> list[dict]:
        return self.recent[:limit]

    async def cleanup_event_log(self, retention_days: int) -> int:
        self.cleanup_calls.append(retention_days)
        return 3


def _entry(**overrides) -> LogEntry:
    base = dict(
        timestamp="2026-08-27T10:00:00+00:00",
        state="on",
        battery_kwh_freetochange=1.0,
        battery_kwh_useable=2.0,
        predicted_load_kwh=None,
        mode="Idle",
        reason="startup",
    )
    base.update(overrides)
    return LogEntry(**base)


@pytest.mark.asyncio
async def test_append_adds_to_buffer_and_persists():
    store = FakeStore()
    log = EventLog(max_entries=10, store=store)
    await log.append(_entry())
    assert len(log.to_list()) == 1
    assert len(store.appended) == 1


@pytest.mark.asyncio
async def test_append_without_store_does_not_error():
    log = EventLog(max_entries=10, store=None)
    await log.append(_entry())
    assert len(log.to_list()) == 1


def test_ring_buffer_evicts_oldest():
    log = EventLog(max_entries=2, store=None)
    import asyncio
    asyncio.run(log.append(_entry(mode="a")))
    asyncio.run(log.append(_entry(mode="b")))
    asyncio.run(log.append(_entry(mode="c")))
    modes = [e["mode"] for e in log.to_list()]
    assert modes == ["c", "b"]   # newest first, "a" evicted


def test_to_list_newest_first():
    log = EventLog(max_entries=10, store=None)
    import asyncio
    asyncio.run(log.append(_entry(mode="first")))
    asyncio.run(log.append(_entry(mode="second")))
    entries = log.to_list()
    assert entries[0]["mode"] == "second"
    assert entries[1]["mode"] == "first"


def test_to_list_excludes_write_confirm_by_default():
    log = EventLog(max_entries=10, store=None)
    import asyncio
    asyncio.run(log.append(_entry(entry_type="mode_change")))
    asyncio.run(log.append(_entry(entry_type="write_confirm")))
    assert len(log.to_list()) == 1
    assert len(log.to_list(include_write_confirm=True)) == 2


@pytest.mark.asyncio
async def test_restore_from_db_reverses_newest_first_rows():
    store = FakeStore()
    # DB returns newest-first; restore must re-reverse so oldest enters the
    # deque first, matching the append order at runtime.
    store.recent = [
        {"timestamp": "t2", "state": "on", "battery_kwh_freetochange": 0,
         "battery_kwh_useable": 0, "predicted_load_kwh": None, "entry_type": "mode_change",
         "price_eur_kwh": None, "mode": "second", "reason": ""},
        {"timestamp": "t1", "state": "on", "battery_kwh_freetochange": 0,
         "battery_kwh_useable": 0, "predicted_load_kwh": None, "entry_type": "mode_change",
         "price_eur_kwh": None, "mode": "first", "reason": ""},
    ]
    log = EventLog(max_entries=10, store=store)
    await log.restore_from_db()
    modes = [e["mode"] for e in log.to_list()]
    assert modes == ["second", "first"]


@pytest.mark.asyncio
async def test_restore_from_db_noop_without_store():
    log = EventLog(max_entries=10, store=None)
    await log.restore_from_db()
    assert log.to_list() == []


@pytest.mark.asyncio
async def test_cleanup_old_entries_delegates_to_store():
    store = FakeStore()
    log = EventLog(max_entries=10, store=store)
    await log.cleanup_old_entries(30)
    assert store.cleanup_calls == [30]


@pytest.mark.asyncio
async def test_cleanup_old_entries_noop_without_store():
    log = EventLog(max_entries=10, store=None)
    await log.cleanup_old_entries(30)   # must not raise
