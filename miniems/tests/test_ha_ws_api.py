"""ha_ws_api.py: one-shot WebSocket queries (registry + energy prefs).

No real network: aiohttp.ClientSession is replaced with a scripted fake that
implements the same async-context-manager/ws_connect surface, mirroring the
project's hand-rolled-fake style (FakeWS, FakeOptimizer, ...) rather than a
mocking framework.
"""
import asyncio

import aiohttp
import pytest

import ha_ws_api
from ha_ws_api import (
    HAWebSocketError,
    get_energy_prefs,
    get_registry_snapshot,
    list_devices,
    list_entities,
)


class _FakeWSConnection:
    """Async context manager double for aiohttp's websocket response,
    scripted with a queue of receive_json() replies."""

    def __init__(self, replies: list[dict]) -> None:
        self._replies = list(replies)
        self.sent: list[dict] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    async def send_json(self, data):
        self.sent.append(data)

    async def receive_json(self):
        if not self._replies:
            raise AssertionError("no more scripted WS replies")
        return self._replies.pop(0)


class _FakeSession:
    """Async context manager double for aiohttp.ClientSession, whose only
    job here is to hand back a scripted _FakeWSConnection from ws_connect()."""

    def __init__(self, ws: _FakeWSConnection) -> None:
        self._ws = ws

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ws_connect(self, url, timeout=None):
        return self._ws


class _RaisingSession:
    """Session double whose ws_connect() raises, for transport-error tests."""

    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def __aenter__(self):
        return self

    async def __aexit__(self, *exc):
        return False

    def ws_connect(self, url, timeout=None):
        raise self._exc


class _ScriptedSessions:
    """Callable double for the `aiohttp.ClientSession` class itself – each
    call (one per connection attempt) pops the next scripted session, so a
    test can control exactly what happens on a fallback retry's second
    connection."""

    def __init__(self, sessions: list) -> None:
        self._sessions = list(sessions)

    def __call__(self, *a, **kw):
        if not self._sessions:
            raise AssertionError("no more scripted sessions – unexpected extra connection")
        return self._sessions.pop(0)


def _ok_session(result, *, extra_replies=None):
    replies = [
        {"type": "auth_required"},
        {"type": "auth_ok"},
        {"id": 1, "type": "result", "success": True, "result": result},
    ]
    if extra_replies:
        replies.extend(extra_replies)
    return _FakeSession(_FakeWSConnection(replies))


@pytest.fixture(autouse=True)
def _supervisor_token(monkeypatch):
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-token")


class TestGetEnergyPrefs:
    @pytest.mark.asyncio
    async def test_returns_the_result_dict(self, monkeypatch):
        session = _ok_session({"energy_sources": [{"type": "solar"}]})
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        result = await get_energy_prefs()
        assert result == {"energy_sources": [{"type": "solar"}]}

    @pytest.mark.asyncio
    async def test_empty_dict_when_result_is_none(self, monkeypatch):
        session = _ok_session(None)
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        assert await get_energy_prefs() == {}

    @pytest.mark.asyncio
    async def test_sends_the_expected_command(self, monkeypatch):
        session = _ok_session({})
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        await get_energy_prefs()
        sent = session._ws.sent
        assert sent[0] == {"type": "auth", "access_token": "sup-token"}
        assert sent[1] == {"id": 1, "type": "energy/get_prefs"}


class TestListDevicesAndEntities:
    @pytest.mark.asyncio
    async def test_list_devices_returns_list(self, monkeypatch):
        devices = [{"id": "abc", "manufacturer": "Deye", "model": "SG0*LP3"}]
        session = _ok_session(devices)
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        assert await list_devices() == devices

    @pytest.mark.asyncio
    async def test_list_devices_empty_list_when_no_result(self, monkeypatch):
        session = _ok_session(None)
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        assert await list_devices() == []

    @pytest.mark.asyncio
    async def test_list_entities_returns_list(self, monkeypatch):
        entities = [{"entity_id": "sensor.x", "device_id": "abc"}]
        session = _ok_session(entities)
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        assert await list_entities() == entities


class TestGetRegistrySnapshot:
    @pytest.mark.asyncio
    async def test_returns_all_three_in_order(self, monkeypatch):
        replies = [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"id": 1, "type": "result", "success": True, "result": {"energy_sources": []}},
            {"id": 2, "type": "result", "success": True, "result": [{"id": "dev1"}]},
            {"id": 3, "type": "result", "success": True, "result": [{"entity_id": "sensor.x"}]},
        ]
        session = _FakeSession(_FakeWSConnection(replies))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        prefs, devices, entities = await get_registry_snapshot()
        assert prefs == {"energy_sources": []}
        assert devices == [{"id": "dev1"}]
        assert entities == [{"entity_id": "sensor.x"}]

    @pytest.mark.asyncio
    async def test_matches_responses_by_id_not_receive_order(self, monkeypatch):
        # HA does not guarantee response ordering - id 3 arrives before id 1.
        replies = [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"id": 3, "type": "result", "success": True, "result": ["entities"]},
            {"id": 1, "type": "result", "success": True, "result": {"energy_sources": []}},
            {"id": 2, "type": "result", "success": True, "result": ["devices"]},
        ]
        session = _FakeSession(_FakeWSConnection(replies))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        prefs, devices, entities = await get_registry_snapshot()
        assert prefs == {"energy_sources": []}
        assert devices == ["devices"]
        assert entities == ["entities"]


class TestUnwrapAndErrors:
    @pytest.mark.asyncio
    async def test_success_false_raises(self, monkeypatch):
        replies = [
            {"type": "auth_required"},
            {"type": "auth_ok"},
            {"id": 1, "type": "result", "success": False, "error": {"code": "unknown_command"}},
        ]
        session = _FakeSession(_FakeWSConnection(replies))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(HAWebSocketError):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_no_response_raises(self, monkeypatch):
        # Only 2 replies (hello + auth) for a query expecting a 3rd - receive_json()
        # raises AssertionError from the fake, which the caller must not swallow
        # as if it were a normal empty result.
        replies = [{"type": "auth_required"}, {"type": "auth_ok"}]
        session = _FakeSession(_FakeWSConnection(replies))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(AssertionError):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_unexpected_greeting_raises(self, monkeypatch):
        session = _FakeSession(_FakeWSConnection([{"type": "not_a_greeting"}]))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(HAWebSocketError, match="greeting"):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_unexpected_auth_response_raises(self, monkeypatch):
        session = _FakeSession(_FakeWSConnection([
            {"type": "auth_required"}, {"type": "something_else"},
        ]))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(HAWebSocketError, match="auth response"):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_no_token_raises(self, monkeypatch):
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        with pytest.raises(HAWebSocketError, match="no token"):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_timeout_raises_ha_websocket_error(self, monkeypatch):
        class _HangingWS(_FakeWSConnection):
            async def receive_json(self):
                raise asyncio.TimeoutError

        session = _FakeSession(_HangingWS([]))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(HAWebSocketError, match="timed out"):
            await get_energy_prefs()

    @pytest.mark.asyncio
    async def test_transport_error_raises_ha_websocket_error(self, monkeypatch):
        session = _RaisingSession(aiohttp.ClientConnectionError("connection refused"))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([session]))
        with pytest.raises(HAWebSocketError, match="transport error"):
            await get_energy_prefs()


class TestLongLivedTokenFallback:
    @pytest.mark.asyncio
    async def test_falls_back_on_auth_invalid(self, monkeypatch):
        rejected = _FakeSession(_FakeWSConnection([
            {"type": "auth_required"},
            {"type": "auth_invalid", "message": "Invalid access token"},
        ]))
        accepted = _ok_session({"energy_sources": []})
        monkeypatch.setattr(
            ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([rejected, accepted])
        )
        result = await get_energy_prefs(long_lived_token="llt-token")
        assert result == {"energy_sources": []}
        assert accepted._ws.sent[0] == {"type": "auth", "access_token": "llt-token"}

    @pytest.mark.asyncio
    async def test_raises_when_no_long_lived_token_configured(self, monkeypatch):
        rejected = _FakeSession(_FakeWSConnection([
            {"type": "auth_required"},
            {"type": "auth_invalid", "message": "Invalid access token"},
        ]))
        monkeypatch.setattr(ha_ws_api.aiohttp, "ClientSession", _ScriptedSessions([rejected]))
        with pytest.raises(HAWebSocketError, match="no long-lived token"):
            await get_energy_prefs()   # long_lived_token defaults to ""
