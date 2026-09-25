"""One-shot queries against the Home Assistant WebSocket API.

`ha_state_client.py`'s per-tick state poll is REST (`GET /states`) on purpose –
cheap, stateless, easy to retry. But three things miniEMS needs for device
detection (docs/roadmap/v3.0-geraeteprofile.md) have **no REST endpoint at
all**: the device registry, the entity registry, and the energy-dashboard
preferences. All three are WebSocket-only in Home Assistant.

This module is deliberately NOT a long-lived client: no subscription, no
reconnect loop, no heartbeat. Each call opens a connection, authenticates,
sends its command(s), collects the matching response(s), and closes. That
matches how these three things are actually used – occasionally, when the
device-profile resolver runs (docs/roadmap/v3.0-geraeteprofile.md, "Auflösungskette")
– not on the 30s EMS tick.

Auth mirrors HAStateClient: SUPERVISOR_TOKEN first, long_lived_token as a
fallback if the supervisor token is rejected (HA's WS auth failure is a
message, `auth_invalid`, not an HTTP status – there is no 401 to catch here).
"""
from __future__ import annotations

import asyncio
import logging
import os
from typing import Any

import aiohttp

from const import HA_WEBSOCKET_URL, HA_WS_QUERY_TIMEOUT_SEC

_LOGGER = logging.getLogger(__name__)


class HAWebSocketError(Exception):
    """A one-shot WS query failed – auth, transport, timeout, or HA-side error.

    Callers (the future device-resolver, or the /devices settings page) are
    expected to degrade to "detection unavailable" rather than crash: none of
    this is on the control path.
    """


async def _query(token: str, commands: list[dict[str, Any]]) -> list[dict[str, Any] | None]:
    """Open one connection, authenticate, send each command, collect the
    matching responses in the order `commands` was given (by response `id`,
    not receive order – HA does not guarantee response ordering).

    Raises HAWebSocketError on any failure. A per-command failure (HA
    returned `success: false`) is NOT raised here – that's the caller's
    business via `_unwrap()`, so one failed command among several doesn't
    have to abort the rest.
    """
    if not token:
        raise HAWebSocketError("no token available")
    try:
        async with aiohttp.ClientSession() as session:
            async with session.ws_connect(
                HA_WEBSOCKET_URL, timeout=HA_WS_QUERY_TIMEOUT_SEC
            ) as ws:
                hello = await asyncio.wait_for(ws.receive_json(), timeout=HA_WS_QUERY_TIMEOUT_SEC)
                if hello.get("type") != "auth_required":
                    raise HAWebSocketError(f"unexpected greeting: {hello.get('type')!r}")

                await ws.send_json({"type": "auth", "access_token": token})
                auth_result = await asyncio.wait_for(
                    ws.receive_json(), timeout=HA_WS_QUERY_TIMEOUT_SEC
                )
                if auth_result.get("type") == "auth_invalid":
                    raise _AuthInvalid(auth_result.get("message", "auth_invalid"))
                if auth_result.get("type") != "auth_ok":
                    raise HAWebSocketError(f"unexpected auth response: {auth_result.get('type')!r}")

                for i, cmd in enumerate(commands, start=1):
                    await ws.send_json({"id": i, **cmd})

                by_id: dict[int, dict[str, Any]] = {}
                for _ in commands:
                    msg = await asyncio.wait_for(ws.receive_json(), timeout=HA_WS_QUERY_TIMEOUT_SEC)
                    if "id" in msg:
                        by_id[msg["id"]] = msg
                return [by_id.get(i) for i in range(1, len(commands) + 1)]
    except _AuthInvalid:
        raise
    except asyncio.TimeoutError as exc:
        raise HAWebSocketError("timed out waiting for a response") from exc
    except aiohttp.ClientError as exc:
        raise HAWebSocketError(f"transport error: {exc}") from exc


class _AuthInvalid(Exception):
    """Internal: lets _run() distinguish 'try the other token' from every
    other failure, without exposing HA's auth-message wording as API."""


async def _run(
    commands: list[dict[str, Any]], long_lived_token: str = ""
) -> list[dict[str, Any] | None]:
    """`_query()` with the same SUPERVISOR_TOKEN → long_lived_token fallback
    order as HAStateClient/InverterController. SUPERVISOR_TOKEN is read fresh
    from the environment (it's supervisor-managed, not add-on config);
    `long_lived_token` is the add-on's own config field
    (`cfg.long_lived_token`) and must be passed in by the caller – this
    module has no Config reference of its own.
    """
    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")
    try:
        return await _query(supervisor_token, commands)
    except _AuthInvalid:
        if not long_lived_token:
            raise HAWebSocketError("SUPERVISOR_TOKEN rejected and no long-lived token configured")
        _LOGGER.warning("SUPERVISOR_TOKEN rejected for WS query – falling back to long-lived token")
        return await _query(long_lived_token, commands)


def _unwrap(response: dict[str, Any] | None, label: str) -> Any:
    if response is None:
        raise HAWebSocketError(f"{label}: no response received")
    if not response.get("success", False):
        raise HAWebSocketError(f"{label}: {response.get('error')}")
    return response.get("result")


# ----------------------------------------------------------------------------
# Public API – three one-shot queries, each its own connection.
# ----------------------------------------------------------------------------

async def get_energy_prefs(long_lived_token: str = "") -> dict[str, Any]:
    """HA's energy-dashboard preferences (`energy/get_prefs`), raw.

    See energy_dashboard.py for parsing this into role candidates. Callers
    should treat "no energy dashboard configured" (an empty/near-empty
    result) as a normal, expected case, not an error.
    """
    responses = await _run([{"type": "energy/get_prefs"}], long_lived_token)
    return _unwrap(responses[0], "energy/get_prefs") or {}


async def list_devices(long_lived_token: str = "") -> list[dict[str, Any]]:
    """Raw HA device-registry entries (`config/device_registry/list`)."""
    responses = await _run([{"type": "config/device_registry/list"}], long_lived_token)
    return _unwrap(responses[0], "config/device_registry/list") or []


async def list_entities(long_lived_token: str = "") -> list[dict[str, Any]]:
    """Raw HA entity-registry entries (`config/entity_registry/list`)."""
    responses = await _run([{"type": "config/entity_registry/list"}], long_lived_token)
    return _unwrap(responses[0], "config/entity_registry/list") or []


async def get_registry_snapshot(
    long_lived_token: str = "",
) -> tuple[dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """All three, over one connection – the shape device_registry.py and
    energy_dashboard.py actually want when building a full snapshot (e.g.
    for the /devices settings page), instead of three separate round trips.
    """
    responses = await _run([
        {"type": "energy/get_prefs"},
        {"type": "config/device_registry/list"},
        {"type": "config/entity_registry/list"},
    ], long_lived_token)
    prefs = _unwrap(responses[0], "energy/get_prefs") or {}
    devices = _unwrap(responses[1], "config/device_registry/list") or []
    entities = _unwrap(responses[2], "config/entity_registry/list") or []
    return prefs, devices, entities
