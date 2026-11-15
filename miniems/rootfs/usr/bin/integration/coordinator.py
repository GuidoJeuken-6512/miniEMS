"""DataUpdateCoordinator for miniEMS – polls /api/status on the addon."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .failsafe import FailsafeWatch

_LOGGER = logging.getLogger(__name__)

DOMAIN = "miniems"
# Fired on the HA event bus for automations (A10, later C7).
EVENT_ALERT = "miniems_alert"
_ISSUE_FAILSAFE = "failsafe_triggered"


class MiniEMSCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Manages a single polling session to the miniEMS /api/status endpoint."""

    def __init__(
        self,
        hass: HomeAssistant,
        base_url: str,
        poll_interval: int = 30,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name="miniEMS",
            update_interval=timedelta(seconds=poll_interval),
            always_update=True,
        )
        self._status_url = f"{base_url.rstrip('/')}/api/status"
        self._session: aiohttp.ClientSession | None = None
        # A10: resets the add-on's actuators when it stops reporting.
        self.failsafe = FailsafeWatch()

    async def _async_setup(self) -> None:
        """Create the persistent aiohttp session."""
        self._session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(connect=5, total=10)
        )

    async def async_shutdown(self) -> None:
        """Close session on integration unload."""
        if self._session and not self._session.closed:
            await self._session.close()
        await super().async_shutdown()

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch /api/status; on a long outage run the add-on's failsafe."""
        now = datetime.now(timezone.utc)
        try:
            data = await self._fetch_status()
        except UpdateFailed:
            actions = self.failsafe.due(now)
            if actions:
                await self._run_failsafe(actions, now)
            raise
        if self.failsafe.on_success(data, now):
            _LOGGER.info("miniEMS: addon reports again – failsafe cleared")
            ir.async_delete_issue(self.hass, DOMAIN, _ISSUE_FAILSAFE)
            self.hass.bus.async_fire(EVENT_ALERT, {"type": "failsafe_cleared"})
        return data

    async def _run_failsafe(self, actions: list[dict[str, Any]], now: datetime) -> None:
        """Run the add-on's reset calls once; never raises."""
        since = self.failsafe.last_ok
        _LOGGER.warning(
            "miniEMS: no status since %s – resetting %d actuator(s) to their safe values",
            since.isoformat() if since else "?", len(actions),
        )
        done = 0
        for action in actions:
            domain, service = action["service"].split(".", 1)
            try:
                await self.hass.services.async_call(domain, service, action["data"], blocking=False)
                done += 1
            except Exception as err:   # one failing call must not stop the others
                _LOGGER.error("miniEMS failsafe: %s(%s) failed: %s",
                              action["service"], action["data"], err)
        self.hass.bus.async_fire(EVENT_ALERT, {
            "type": "failsafe",
            "since": since.isoformat() if since else None,
            "actions": done,
        })
        ir.async_create_issue(
            self.hass, DOMAIN, _ISSUE_FAILSAFE,
            is_fixable=False, is_persistent=False,
            severity=ir.IssueSeverity.ERROR,
            translation_key=_ISSUE_FAILSAFE,
            translation_placeholders={"minutes": f"{self.failsafe.spec['timeout_min']:.0f}"
                                      if self.failsafe.spec else "?"},
        )

    async def _fetch_status(self) -> dict[str, Any]:
        """Fetch /api/status from the miniEMS addon.

        Raises UpdateFailed on any error so HA marks all entities unavailable.
        """
        if not self._session or self._session.closed:
            self._session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(connect=5, total=10)
            )
        try:
            async with self._session.get(self._status_url) as resp:
                if resp.status != 200:
                    raise UpdateFailed(f"miniEMS API returned HTTP {resp.status}")
                data: dict[str, Any] = await resp.json()
                if not isinstance(data, dict):
                    raise UpdateFailed("miniEMS API returned unexpected format")
                last_updated_raw = data.get("last_updated")
                if last_updated_raw:
                    try:
                        last_updated = datetime.fromisoformat(last_updated_raw)
                        if last_updated.tzinfo is None:
                            last_updated = last_updated.replace(tzinfo=timezone.utc)
                        age_sec = (datetime.now(timezone.utc) - last_updated).total_seconds()
                        if age_sec > 300:
                            raise UpdateFailed(
                                f"miniEMS data is stale ({age_sec:.0f}s old) – addon loop may be stuck"
                            )
                    except UpdateFailed:
                        raise
                    except Exception:
                        pass
                return data
        except aiohttp.ClientConnectorError as err:
            raise UpdateFailed(f"Cannot connect to miniEMS addon: {err}") from err
        except aiohttp.ServerTimeoutError as err:
            raise UpdateFailed(f"miniEMS addon timed out: {err}") from err
        except UpdateFailed:
            raise
        except Exception as err:
            raise UpdateFailed(f"miniEMS update error: {err}") from err
