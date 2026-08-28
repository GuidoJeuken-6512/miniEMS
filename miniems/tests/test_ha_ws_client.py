"""HAWebSocketClient: REST polling, token fallback, staleness helpers."""
import asyncio
from datetime import datetime, timedelta, timezone

import pytest
from aioresponses import aioresponses

from const import HA_STATES_URL
from ha_ws_client import HAWebSocketClient


def _state(entity_id, value, last_updated=None, attributes=None):
    return {
        "entity_id": entity_id,
        "state": value,
        "last_updated": last_updated or datetime.now(timezone.utc).isoformat(),
        "last_changed": last_updated or datetime.now(timezone.utc).isoformat(),
        "attributes": attributes or {},
    }


class TestGetStateValue:
    def test_returns_float_for_numeric_state(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "42.5")
        assert client.get_state_value("sensor.x") == 42.5

    @pytest.mark.parametrize("raw", ["unavailable", "unknown", ""])
    def test_none_for_unavailable_states(self, raw):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", raw)
        assert client.get_state_value("sensor.x") is None

    def test_none_for_missing_entity(self):
        client = HAWebSocketClient(["sensor.x"])
        assert client.get_state_value("sensor.x") is None

    def test_none_for_non_numeric_state(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "2026-08-27T10:00:00+00:00")
        assert client.get_state_value("sensor.x") is None


class TestGetStateDatetime:
    def test_parses_iso_timestamp_state(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "2026-08-27T10:00:00+00:00")
        dt = client.get_state_datetime("sensor.x")
        assert dt == datetime(2026, 8, 27, 10, 0, tzinfo=timezone.utc)

    def test_parses_z_suffix(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "2026-08-27T10:00:00Z")
        assert client.get_state_datetime("sensor.x").tzinfo is not None

    def test_naive_timestamp_gets_utc(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "2026-08-27T10:00:00")
        dt = client.get_state_datetime("sensor.x")
        assert dt.tzinfo == timezone.utc

    def test_none_for_non_string_or_unavailable(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "unavailable")
        assert client.get_state_datetime("sensor.x") is None

    def test_none_for_unparsable_string(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "not-a-timestamp")
        assert client.get_state_datetime("sensor.x") is None


class TestGetStateAttribute:
    def test_returns_attribute_value(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "1", attributes={"foo": "bar"})
        assert client.get_state_attribute("sensor.x", "foo") == "bar"

    def test_none_for_missing_attribute(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_cache["sensor.x"] = _state("sensor.x", "1")
        assert client.get_state_attribute("sensor.x", "foo") is None

    def test_none_for_missing_entity(self):
        client = HAWebSocketClient(["sensor.x"])
        assert client.get_state_attribute("sensor.x", "foo") is None


class TestStaleness:
    def test_is_stale_true_when_never_received(self):
        client = HAWebSocketClient(["sensor.x"])
        assert client.is_stale("sensor.x", 300) is True

    def test_is_stale_false_when_fresh(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_ts["sensor.x"] = datetime.now(timezone.utc)
        assert client.is_stale("sensor.x", 300) is False

    def test_is_stale_true_when_older_than_max_age(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_ts["sensor.x"] = datetime.now(timezone.utc) - timedelta(seconds=400)
        assert client.is_stale("sensor.x", 300) is True

    def test_get_state_age_sec_none_when_never_received(self):
        client = HAWebSocketClient(["sensor.x"])
        assert client.get_state_age_sec("sensor.x") is None

    def test_get_state_age_sec_never_negative(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_ts["sensor.x"] = datetime.now(timezone.utc) + timedelta(seconds=5)
        assert client.get_state_age_sec("sensor.x") == 0.0


class TestIsStaleDaily:
    def test_true_when_never_received(self):
        client = HAWebSocketClient(["sensor.x"])
        assert client.is_stale_daily("sensor.x", 900) is True

    def test_false_when_timestamp_is_today_local(self):
        client = HAWebSocketClient(["sensor.x"])
        client._state_ts["sensor.x"] = datetime.now().astimezone()
        assert client.is_stale_daily("sensor.x", 900) is False

    def test_true_when_timestamp_is_yesterday_past_grace(self):
        client = HAWebSocketClient(["sensor.x"])
        yesterday = datetime.now().astimezone() - timedelta(days=1)
        client._state_ts["sensor.x"] = yesterday
        now = datetime.now().astimezone()
        # Only "stale" if we're currently past the grace window after midnight
        seconds_since_midnight = (
            now - now.replace(hour=0, minute=0, second=0, microsecond=0)
        ).total_seconds()
        expected_stale = seconds_since_midnight > 900
        assert client.is_stale_daily("sensor.x", 900) is expected_stale


class TestParseTs:
    def test_prefers_last_updated_over_last_changed(self):
        state = {"last_updated": "2026-08-27T10:00:00+00:00", "last_changed": "2026-08-27T09:00:00+00:00"}
        ts = HAWebSocketClient._parse_ts(state)
        assert ts.hour == 10

    def test_falls_back_to_last_changed(self):
        state = {"last_changed": "2026-08-27T09:00:00+00:00"}
        ts = HAWebSocketClient._parse_ts(state)
        assert ts.hour == 9

    def test_none_when_neither_present(self):
        assert HAWebSocketClient._parse_ts({}) is None

    def test_none_for_unparsable(self):
        assert HAWebSocketClient._parse_ts({"last_updated": "garbage"}) is None


class TestUpdateCache:
    def test_updates_state_and_timestamp_for_monitored_entities(self):
        client = HAWebSocketClient(["sensor.x"])
        client._update_cache([_state("sensor.x", "5")])
        assert client.get_state_value("sensor.x") == 5.0
        assert "sensor.x" in client._state_ts

    def test_ignores_entities_not_monitored(self):
        client = HAWebSocketClient(["sensor.x"])
        client._update_cache([_state("sensor.y", "5")])
        assert client.get_state_value("sensor.y") is None

    def test_sets_ready_event(self):
        client = HAWebSocketClient(["sensor.x"])
        assert not client._ready.is_set()
        client._update_cache([_state("sensor.x", "5")])
        assert client._ready.is_set()

    @pytest.mark.asyncio
    async def test_fires_callback_on_value_change(self):
        # _update_cache uses asyncio.ensure_future(), which schedules onto
        # whatever loop is running *right now* – so this must run inside an
        # async test, not be called synchronously and drained via a second,
        # unrelated asyncio.run() afterwards.
        received = []

        async def _cb(eid, state):
            received.append((eid, state["state"]))

        client = HAWebSocketClient(["sensor.x"], on_state_change=_cb)
        client._update_cache([_state("sensor.x", "5")])
        await asyncio.sleep(0)
        assert received == [("sensor.x", "5")]

    @pytest.mark.asyncio
    async def test_no_callback_when_value_unchanged(self):
        calls = []

        async def _cb(eid, state):
            calls.append(1)

        client = HAWebSocketClient(["sensor.x"], on_state_change=_cb)
        client._update_cache([_state("sensor.x", "5")])
        client._update_cache([_state("sensor.x", "5")])
        await asyncio.sleep(0)
        assert len(calls) == 1


class TestFetchStates:
    @pytest.mark.asyncio
    async def test_success_populates_cache(self):
        client = HAWebSocketClient(["sensor.x"])
        with aioresponses() as m:
            m.get(HA_STATES_URL, payload=[_state("sensor.x", "10")])
            ok = await client._fetch_states()
        assert ok is True
        assert client.get_state_value("sensor.x") == 10.0

    @pytest.mark.asyncio
    async def test_401_switches_to_long_lived_token_and_retries(self):
        client = HAWebSocketClient(["sensor.x"], long_lived_token="llt-secret")
        with aioresponses() as m:
            m.get(HA_STATES_URL, status=401)
            m.get(HA_STATES_URL, payload=[_state("sensor.x", "10")])
            ok = await client._fetch_states()
        assert ok is True
        assert client._active_token == "llt-secret"

    @pytest.mark.asyncio
    async def test_401_without_long_lived_token_fails(self):
        client = HAWebSocketClient(["sensor.x"])
        with aioresponses() as m:
            m.get(HA_STATES_URL, status=401)
            ok = await client._fetch_states()
        assert ok is False

    @pytest.mark.asyncio
    async def test_server_error_returns_false(self):
        client = HAWebSocketClient(["sensor.x"])
        with aioresponses() as m:
            m.get(HA_STATES_URL, status=500)
            ok = await client._fetch_states()
        assert ok is False

    @pytest.mark.asyncio
    async def test_transport_error_returns_false(self):
        client = HAWebSocketClient(["sensor.x"])
        with aioresponses() as m:
            m.get(HA_STATES_URL, exception=ConnectionError("boom"))
            ok = await client._fetch_states()
        assert ok is False


class TestWaitReady:
    @pytest.mark.asyncio
    async def test_wait_ready_returns_once_set(self):
        client = HAWebSocketClient(["sensor.x"])
        client._ready.set()
        await asyncio.wait_for(client.wait_ready(), timeout=1)

    @pytest.mark.asyncio
    async def test_run_can_be_stopped(self, monkeypatch):
        import ha_ws_client as ha_ws_client_module
        # run() sleeps HA_POLL_INTERVAL_SEC (15s) between iterations – stop()
        # only prevents the *next* iteration, it doesn't interrupt an
        # in-flight sleep. Shrink the interval so the loop actually re-checks
        # `_running` within the test's lifetime instead of waiting 15s.
        monkeypatch.setattr(ha_ws_client_module, "HA_POLL_INTERVAL_SEC", 0.01)
        client = HAWebSocketClient(["sensor.x"])
        with aioresponses() as m:
            m.get(HA_STATES_URL, payload=[_state("sensor.x", "1")], repeat=True)
            task = asyncio.create_task(client.run())
            await asyncio.sleep(0.05)
            await client.stop()
            await asyncio.wait_for(task, timeout=1)
        assert client._running is False
