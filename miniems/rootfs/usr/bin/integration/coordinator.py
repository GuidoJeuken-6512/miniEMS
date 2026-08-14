"""DataUpdateCoordinator for miniEMS – polls /api/status on the addon."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import aiohttp
from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

_LOGGER = logging.getLogger(__name__)


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
