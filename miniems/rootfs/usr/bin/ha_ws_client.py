"""Home Assistant state client – REST API via Supervisor proxy with token fallback."""
import asyncio
import logging
import os
from collections.abc import Callable, Awaitable
from datetime import datetime, timezone
from typing import Any

import aiohttp

from const import HA_STATES_URL, HA_POLL_INTERVAL_SEC, HA_RETRY_INTERVAL_SEC

_LOGGER = logging.getLogger(__name__)
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
        on_state_change: StateCallback | None = None,
        long_lived_token: str = "",
    ) -> None:
        self._entities = set(entities)
        self._on_state_change = on_state_change
        self._supervisor_token: str = os.environ.get("SUPERVISOR_TOKEN", "")
        self._long_lived_token: str = long_lived_token
        self._active_token: str = self._supervisor_token  # start with supervisor token
        self._state_cache: dict[str, dict[str, Any]] = {}
        # Per-entity timestamp of HA's last update, for staleness detection
        self._state_ts: dict[str, datetime] = {}
        self._ready = asyncio.Event()
        self._running: bool = False
        self._last_loaded: int = -1
        self._last_missing: set[str] = set()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def state_cache(self) -> dict[str, dict[str, Any]]:
        return self._state_cache

    async def wait_ready(self) -> None:
        """Wait until initial states have been loaded from HA."""
        await self._ready.wait()

    def get_state_value(self, entity_id: str, max_age_sec: float | None = None) -> float | None:
        """Return the numeric state of an entity, or None if unavailable.

        When max_age_sec is given, a value HA has not updated within that
        window is treated as unavailable.  Callers that must keep counting
        regardless (e.g. energy accounting) simply omit the argument.
        """
        if max_age_sec is not None and self.is_stale(entity_id, max_age_sec):
            return None
        state = self._state_cache.get(entity_id, {})
        raw = state.get("state")
        if raw is None or raw in ("unavailable", "unknown", ""):
            return None
        try:
            return float(raw)
        except (ValueError, TypeError):
            return None

    def get_state_age_sec(self, entity_id: str) -> float | None:
        """Seconds since HA last updated this entity; None if never received."""
        ts = self._state_ts.get(entity_id)
        if ts is None:
            return None
        return max(0.0, (datetime.now(timezone.utc) - ts).total_seconds())

    def is_stale(self, entity_id: str, max_age_sec: float) -> bool:
        """True when the value is older than max_age_sec or was never received."""
        age = self.get_state_age_sec(entity_id)
        return True if age is None else age > max_age_sec

    def is_stale_daily(self, entity_id: str, grace_sec: float) -> bool:
        """True when a once-per-day value was not written on the current day.

        For a value the source rewrites at every date rollover, "was it written
        today?" is answerable exactly, where "how old is it?" never is: the same
        daily forecast total is fresh at 09:00 and still correct at 23:00, so any
        age threshold is wrong for one of those two. Solcast writes its daily
        totals within five minutes of *local* midnight, so a timestamp from
        yesterday means the integration has stopped – regardless of the hour.

        The comparison runs in local time on purpose: the source rolls the day
        over in the installation's timezone, while _parse_ts stores UTC. Under
        CEST that is a two-hour difference, i.e. a UTC comparison would be wrong
        every night between 22:00 and 00:00 UTC.

        `grace_sec` covers the few minutes right after midnight in which the
        source legitimately has not rewritten the value yet.
        """
        ts = self._state_ts.get(entity_id)
        if ts is None:
            return True
        now = datetime.now().astimezone()
        if ts.astimezone(now.tzinfo).date() >= now.date():
            return False
        midnight = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return (now - midnight).total_seconds() > grace_sec

    async def run(self) -> None:
        """Poll HA states every HA_POLL_INTERVAL_SEC seconds with token fallback."""
        self._running = True
        while self._running:
            success = await self._fetch_states()
            if not success:
                await asyncio.sleep(HA_RETRY_INTERVAL_SEC)
                continue
            await asyncio.sleep(HA_POLL_INTERVAL_SEC)

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

    @staticmethod
    def _parse_ts(state: dict[str, Any]) -> datetime | None:
        """Parse HA's last_updated/last_changed into an aware UTC datetime."""
        raw = state.get("last_updated") or state.get("last_changed")
        if not raw:
            return None
        try:
            ts = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
        except ValueError:
            return None
        return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)

    def _update_cache(self, states: list[dict]) -> None:
        """Update state cache and fire callbacks for changed entities."""
        loaded = 0
        seen: set[str] = set()
        for state in states:
            eid: str = state.get("entity_id", "")
            if eid not in self._entities:
                continue
            old = self._state_cache.get(eid)
            self._state_cache[eid] = state
            ts = self._parse_ts(state)
            if ts is not None:
                self._state_ts[eid] = ts
            seen.add(eid)
            loaded += 1
            if (old is None or old.get("state") != state.get("state")) and self._on_state_change is not None:
                asyncio.ensure_future(self._on_state_change(eid, state))

        token_name = "supervisor" if self._active_token == self._supervisor_token else "long-lived"
        missing = self._entities - seen
        # Log at INFO only when something actually changed; otherwise this line
        # would repeat every poll interval (~5760x/day).
        if loaded != self._last_loaded or missing != self._last_missing:
            _LOGGER.info(
                "States refreshed: %d/%d entities (token: %s)",
                loaded, len(self._entities), token_name,
            )
            if missing:
                _LOGGER.warning(
                    "%d configured entities do not exist in Home Assistant: %s",
                    len(missing), ", ".join(sorted(missing)),
                )
            self._last_loaded = loaded
            self._last_missing = missing
        else:
            _LOGGER.debug(
                "States refreshed: %d/%d entities (token: %s)",
                loaded, len(self._entities), token_name,
            )

        if not self._ready.is_set():
            self._ready.set()
