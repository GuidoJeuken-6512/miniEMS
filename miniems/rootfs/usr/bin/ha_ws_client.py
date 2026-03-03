"""Home Assistant state client – REST API via Supervisor proxy with token fallback."""
import asyncio
import logging
import os
from collections.abc import Callable, Awaitable
from typing import Any

import aiohttp

_LOGGER = logging.getLogger(__name__)

HA_STATES_URL = "http://hassio/homeassistant/api/states"
_POLL_INTERVAL = 15  # seconds between state refreshes
StateCallback = Callable[[str, dict[str, Any]], Awaitable[None]]


class HAWebSocketClient:
    """Fetches entity states from HA Core via Supervisor REST proxy.

    Authentication order:
    1. SUPERVISOR_TOKEN environment variable (automatic, no config needed)
    2. long_lived_token from addon config (fallback if supervisor token is rejected)
    """

    def __init__(
        self,
        entities: list[str],
        on_state_change: StateCallback,
        long_lived_token: str = "",
    ) -> None:
        self._entities = set(entities)
        self._on_state_change = on_state_change
        self._supervisor_token: str = os.environ.get("SUPERVISOR_TOKEN", "")
        self._long_lived_token: str = long_lived_token
        self._active_token: str = self._supervisor_token  # start with supervisor token
        self._state_cache: dict[str, dict[str, Any]] = {}
        self._ready = asyncio.Event()
        self._running: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state_cache(self) -> dict[str, dict[str, Any]]:
        return self._state_cache

    async def wait_ready(self) -> None:
        """Wait until initial states have been loaded from HA."""
        await self._ready.wait()

    def get_state_value(self, entity_id: str) -> float | None:
        """Return the numeric state of an entity, or None if unavailable."""
        state = self._state_cache.get(entity_id, {})
        raw = state.get("state")
        if raw is None or raw in ("unavailable", "unknown", ""):
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    async def run(self) -> None:
        """Poll HA states every _POLL_INTERVAL seconds with token fallback."""
        self._running = True
        while self._running:
            success = await self._fetch_states()
            if not success:
                await asyncio.sleep(10)
                continue
            await asyncio.sleep(_POLL_INTERVAL)

    async def stop(self) -> None:
        self._running = False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _fetch_states(self) -> bool:
        """Fetch all states. Returns True on success, False on error."""
        headers = {
            "Authorization": f"Bearer {self._active_token}",
            "Content-Type": "application/json",
        }
        try:
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(
                    HA_STATES_URL, timeout=aiohttp.ClientTimeout(total=10)
                ) as resp:
                    if resp.status == 401:
                        return await self._handle_unauthorized()
                    resp.raise_for_status()
                    states: list[dict] = await resp.json()

            self._update_cache(states)
            return True

        except aiohttp.ClientResponseError as exc:
            _LOGGER.warning("REST error [%d]: %s", exc.status, exc.message)
        except Exception as exc:
            _LOGGER.warning("REST error [%s]: %s", type(exc).__name__, exc)
        return False

    async def _handle_unauthorized(self) -> bool:
        """Try switching to long-lived token on 401. Returns True if switched."""
        if self._active_token == self._supervisor_token:
            if self._long_lived_token:
                _LOGGER.warning(
                    "SUPERVISOR_TOKEN rejected (401) – switching to long-lived token"
                )
                self._active_token = self._long_lived_token
                return await self._fetch_states()
            else:
                _LOGGER.error(
                    "SUPERVISOR_TOKEN rejected (401) and no long_lived_token configured. "
                    "Add a long-lived token in the addon configuration."
                )
        else:
            _LOGGER.error("Long-lived token also rejected (401) – check the token.")
        return False

    def _update_cache(self, states: list[dict]) -> None:
        """Update state cache and fire callbacks for changed entities."""
        loaded = 0
        for state in states:
            eid: str = state.get("entity_id", "")
            if eid not in self._entities:
                continue
            old = self._state_cache.get(eid)
            self._state_cache[eid] = state
            loaded += 1
            if old is None or old.get("state") != state.get("state"):
                asyncio.ensure_future(self._on_state_change(eid, state))

        _LOGGER.info(
            "States refreshed: %d/%d entities (token: %s)",
            loaded,
            len(self._entities),
            "supervisor" if self._active_token == self._supervisor_token else "long-lived",
        )

        if not self._ready.is_set():
            self._ready.set()
