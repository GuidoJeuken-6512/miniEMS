"""Push miniEMS computed values as HA sensor states via Supervisor REST proxy.

Each EMS tick calls publish(status_dict).  Sensors appear in Home Assistant
with the prefix ``miniems_`` and friendly names prefixed with "miniEMS".
"""
import logging
import os
from typing import Any

import aiohttp

from const import HA_STATES_URL

_LOGGER = logging.getLogger(__name__)

_BASE_URL = HA_STATES_URL

# Only addon-calculated sensors are published.
# Input sensors (PV power, load, grid, battery, price) already exist in HA
# from the Deye/Octopus integrations — publishing duplicates is redundant.
# (entity_id, status_store key, HA attributes)
_SENSORS: list[tuple[str, str, dict]] = [
    (
        "sensor.miniems_mode",
        "mode",
        {"friendly_name": "miniEMS Mode", "icon": "mdi:home-lightning-bolt"},
    ),
    (
        "sensor.miniems_today_grid_cost_eur",
        "today_grid_cost_eur",
        {
            "friendly_name": "miniEMS Today Grid Cost",
            "unit_of_measurement": "€",
            "device_class": "monetary",
            "state_class": "total_increasing",
            "icon": "mdi:currency-eur",
        },
    ),
    (
        "sensor.miniems_today_pv_savings_eur",
        "today_pv_saved_eur",
        {
            "friendly_name": "miniEMS Today PV Savings",
            "unit_of_measurement": "€",
            "device_class": "monetary",
            "state_class": "total_increasing",
            "icon": "mdi:piggy-bank",
        },
    ),
    (
        "sensor.miniems_today_pv_used_kwh",
        "today_pv_used_kwh",
        {
            "friendly_name": "miniEMS Today PV Used",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:solar-power",
        },
    ),
    (
        "sensor.miniems_today_grid_import_kwh",
        "today_grid_import_kwh",
        {
            "friendly_name": "miniEMS Today Grid Import",
            "unit_of_measurement": "kWh",
            "device_class": "energy",
            "state_class": "total_increasing",
            "icon": "mdi:transmission-tower-import",
        },
    ),
    (
        "sensor.miniems_week_grid_cost_eur",
        "week_grid_cost_eur",
        {
            "friendly_name": "miniEMS Week Grid Cost",
            "unit_of_measurement": "€",
            "device_class": "monetary",
            "state_class": "measurement",
            "icon": "mdi:calendar-week",
        },
    ),
    (
        "sensor.miniems_week_pv_savings_eur",
        "week_pv_saved_eur",
        {
            "friendly_name": "miniEMS Week PV Savings",
            "unit_of_measurement": "€",
            "device_class": "monetary",
            "state_class": "measurement",
            "icon": "mdi:piggy-bank-outline",
        },
    ),
]


class HASensorPublisher:
    """Writes miniEMS sensor states into Home Assistant after every EMS tick."""

    def __init__(self, supervisor_token: str, long_lived_token: str = "") -> None:
        self._sup_token = supervisor_token
        self._llt = long_lived_token
        self._active_token = supervisor_token

    async def publish(self, status: dict[str, Any]) -> None:
        """Push all defined sensors to HA.  Silently skips unavailable values."""
        for entity_id, key, attributes in _SENSORS:
            value = status.get(key)
            if value is None:
                continue
            await self._post(entity_id, value, attributes)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _post(self, entity_id: str, state: Any, attributes: dict) -> None:
        url = f"{_BASE_URL}/{entity_id}"
        headers = {
            "Authorization": f"Bearer {self._active_token}",
            "Content-Type": "application/json",
        }
        payload = {"state": str(state), "attributes": attributes}
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    url,
                    json=payload,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status == 401:
                        await self._handle_401(entity_id, state, attributes)
                    elif resp.status not in (200, 201):
                        _LOGGER.warning(
                            "Failed to publish %s: HTTP %d", entity_id, resp.status
                        )
        except Exception as exc:
            _LOGGER.debug("Sensor publish error (%s): %s", entity_id, exc)

    async def _handle_401(
        self, entity_id: str, state: Any, attributes: dict
    ) -> None:
        if self._active_token == self._sup_token and self._llt:
            _LOGGER.warning(
                "SUPERVISOR_TOKEN rejected for sensor publisher – switching to long-lived token"
            )
            self._active_token = self._llt
            await self._post(entity_id, state, attributes)
        else:
            _LOGGER.error(
                "Auth failed for sensor publisher – verify homeassistant_api permission"
            )
