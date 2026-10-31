"""HA service calls with write confirmation, shared by every actuator.

Extracted from inverter_controller.py so the hot-water controller drives its
heat-pump setpoint exactly like the inverter's three channels: HTTP success
never counts as confirmation, only the real entity state does, and an
unconfirmed write is re-sent every INVERTER_WRITE_CONFIRM_TIMEOUT_SEC.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from typing import Any

import aiohttp

from const import (
    HA_SERVICES_URL,
    INVERTER_SERVICE_CALL_TIMEOUT_SEC,
    INVERTER_WRITE_CONFIRM_TIMEOUT_SEC,
    INVERTER_WRITE_ERROR_WINDOW_SEC,
)
from write_channel import WriteChannel, WriteSpec

_LOGGER = logging.getLogger(__name__)


class HAServiceCaller:
    """POSTs to HA's services API; falls back to the long-lived token on 401."""

    def __init__(self, supervisor_token: str, long_lived_token: str = "") -> None:
        self._sup_token = supervisor_token
        self._llt = long_lived_token
        self.active_token = supervisor_token

    async def call(
        self, domain: str, service: str, data: dict[str, Any], *, _retry: bool = False
    ) -> bool | None:
        """True = accepted, False = actively rejected, None = outcome unknown.

        None is deliberately distinct from False: a write whose response never
        arrived has very likely still been applied, and the caller confirms it
        against the real entity state rather than guessing from the HTTP result.
        """
        url = f"{HA_SERVICES_URL}/{domain}/{service}"
        headers = {"Authorization": f"Bearer {self.active_token}", "Content-Type": "application/json"}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url, json=data, headers=headers,
                    timeout=aiohttp.ClientTimeout(total=INVERTER_SERVICE_CALL_TIMEOUT_SEC),
                ) as resp:
                    if (resp.status == 401 and not _retry
                            and self.active_token == self._sup_token and self._llt):
                        self.active_token = self._llt
                        return await self.call(domain, service, data, _retry=True)
                    if resp.status not in (200, 201):
                        _LOGGER.warning("Service call %s.%s failed: HTTP %d", domain, service, resp.status)
                        return False
                    _LOGGER.debug("Service %s.%s OK for %s", domain, service, data.get("entity_id"))
                    return True
        except (asyncio.TimeoutError, aiohttp.ServerTimeoutError):
            # str() on a TimeoutError is empty – a bare colon in the log helps nobody.
            return None
        except Exception as exc:
            _LOGGER.error(
                "Service call error %s.%s [%s]: %s", domain, service, type(exc).__name__, exc
            )
            return None


class ConfirmedWriter:
    """Sends a write and keeps re-sending it until the real state confirms it."""

    def __init__(self, caller: HAServiceCaller) -> None:
        self.caller = caller
        # Write-confirm lifecycle events since the last pop_events().
        self.events: list[dict[str, Any]] = []
        # Monotonic timestamps of real (HTTP-rejected) failures.
        self.error_times: deque[float] = deque()

    @property
    def write_errors(self) -> int:
        """Real failures within the last INVERTER_WRITE_ERROR_WINDOW_SEC."""
        cutoff = time.monotonic() - INVERTER_WRITE_ERROR_WINDOW_SEC
        while self.error_times and self.error_times[0] < cutoff:
            self.error_times.popleft()
        return len(self.error_times)

    def pop_events(self) -> list[dict[str, Any]]:
        events, self.events = self.events, []
        return events

    async def write(
        self,
        ch: WriteChannel,
        spec: WriteSpec,
        matched: bool,
        sim: bool,
        label: str,
    ) -> None:
        """Send `spec.domain.spec.service(spec.payload)`, deduped on `spec.expected`.

        `matched` is the caller's fresh comparison of the *real* HA state
        against the target. HTTP success alone never marks a write confirmed.
        """
        now = time.monotonic()
        ch.label = label
        target = spec.expected
        domain, service, data = spec.domain, spec.service, spec.payload

        if ch.target != target:
            ch.target = target
            ch.confirmed = False
            ch.sent_at = None
            ch.pending_since = now

        if ch.confirmed:
            return

        if matched:
            if ch.pending_since is not None:
                self.events.append({
                    "channel": label,
                    "target": target,
                    "outcome": "confirmed",
                    "latency_sec": round(now - ch.pending_since, 1),
                })
            ch.confirmed = True
            ch.sent_at = None
            ch.pending_since = None
            return

        if ch.sent_at is not None and (now - ch.sent_at) < INVERTER_WRITE_CONFIRM_TIMEOUT_SEC:
            return   # write in flight – give this cycle a chance to land

        if sim:
            # Nothing is sent, so nothing could ever confirm it – done right away.
            # Not logged as a write-confirm event: simulated writes would corrupt
            # the real-world latency data those events are meant to collect.
            _LOGGER.info("[SIM] %s.%s(%s)", domain, service, data)
            ch.confirmed = True
            ch.sent_at = None
            ch.pending_since = None
            return

        ok = await self.caller.call(domain, service, data)
        ch.sent_at = now
        if ok is False:
            self.error_times.append(now)
            self.events.append({
                "channel": label,
                "target": target,
                "outcome": "failed",
                "latency_sec": round(now - ch.pending_since, 1) if ch.pending_since else None,
            })
            _LOGGER.error("%s write FAILED (target=%s) – retrying next tick", label, target)
        elif ok is None:
            # Timed out: HA very likely applied it anyway (the Modbus round-trip
            # just outlasts our HTTP timeout) – the entity state decides next tick.
            _LOGGER.info(
                "%s write timed out after %ds (target=%s) – HA may well have applied it; "
                "confirming against the entity state next tick",
                label, INVERTER_SERVICE_CALL_TIMEOUT_SEC, target,
            )
        else:
            _LOGGER.debug("%s write sent (target=%s), awaiting confirmation", label, target)
