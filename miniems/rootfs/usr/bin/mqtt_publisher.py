"""MQTT Discovery publisher for miniEMS.

Publishes all sensors as proper HA entities with unique_id, device grouping,
and long-term statistics support.  Queries the Supervisor for broker credentials.

Falls back silently to REST (HASensorPublisher) if MQTT is unavailable.
"""
import logging
import os
from dataclasses import dataclass
from typing import Any

import aiohttp
import aiomqtt

from const import (
    MQTT_DISCOVERY_PREFIX,
    MQTT_STATE_TOPIC_PREFIX,
    SUPERVISOR_MQTT_URL,
    VERSION,
)

_LOGGER = logging.getLogger(__name__)

# Device info shared by all sensors
_DEVICE = {
    "identifiers": ["miniems_addon"],
    "name": "miniEMS",
    "model": f"miniEMS {VERSION}",
    "manufacturer": "miniEMS Addon",
}

# (status_key, entity_name, unit, device_class, state_class, icon)
# entity_name: short label shown under the device card (device name "miniEMS" is prepended by HA)
_SENSOR_DEFS: list[tuple[str, str, str | None, str | None, str, str]] = [
    ("mode",                   "Mode",               None,  None,        "measurement",    "mdi:home-lightning-bolt"),
    ("today_grid_cost_eur",    "Today Grid Cost",    "€",   "monetary",  "total_increasing","mdi:currency-eur"),
    ("today_pv_saved_eur",     "Today PV Savings",   "€",   "monetary",  "total_increasing","mdi:piggy-bank"),
    ("today_pv_used_kwh",      "Today PV Used",      "kWh", "energy",    "total_increasing","mdi:solar-power"),
    ("today_grid_import_kwh",  "Today Grid Import",  "kWh", "energy",    "total_increasing","mdi:transmission-tower-import"),
    ("today_load_total_kwh",   "Today Load Total",   "kWh", "energy",    "total_increasing","mdi:lightning-bolt"),
    ("today_load_cost_eur",    "Today Load Cost",    "€",   "monetary",  "total_increasing","mdi:cash"),
    ("week_grid_cost_eur",     "Week Grid Cost",     "€",   "monetary",  "measurement",    "mdi:calendar-week"),
    ("week_pv_saved_eur",      "Week PV Savings",    "€",   "monetary",  "measurement",    "mdi:piggy-bank-outline"),
    ("month_grid_cost_eur",    "Month Grid Cost",    "€",   "monetary",  "measurement",    "mdi:calendar-month"),
    ("month_pv_savings_eur",   "Month PV Savings",   "€",   "monetary",  "measurement",    "mdi:calendar-month"),
    ("month_load_cost_eur",    "Month Load Cost",    "€",   "monetary",  "measurement",    "mdi:calendar-month"),
    ("year_grid_cost_eur",     "Year Grid Cost",     "€",   "monetary",  "measurement",    "mdi:calendar"),
    ("year_pv_savings_eur",    "Year PV Savings",    "€",   "monetary",  "measurement",    "mdi:calendar"),
    ("year_load_cost_eur",     "Year Load Cost",      "€",  "monetary",  "measurement",    "mdi:calendar"),
    ("predicted_load_kwh",               "Predicted Load",               "kWh", "energy",   "measurement",      "mdi:chart-line"),
    ("predicted_pv_kwh",                 "Predicted PV Yield",           "kWh", "energy",   "measurement",      "mdi:weather-sunny"),
    ("battery_kwh_freetochange",         "Battery Free to Charge",       "kWh", "energy",   "measurement",      "mdi:battery-plus"),
    ("battery_kwh_useable",              "Battery Useable",              "kWh", "energy",   "measurement",      "mdi:battery-minus"),
    ("today_cost_without_grid_charge",   "Today Cost Without Grid Charge","€",  "monetary", "total_increasing", "mdi:cash-minus"),
    ("today_cost_fix_price_tarif",       "Today Cost Fix Price Tariff",  "€",   "monetary", "total_increasing", "mdi:cash"),
    ("today_feed_in_kwh",                "Today Feed-in",                "kWh", "energy",   "total_increasing", "mdi:solar-power-variant"),
    ("today_feed_in_revenue_eur",        "Today Feed-in Revenue",        "€",   "monetary", "total_increasing", "mdi:cash-plus"),
    ("today_grid_charge_kwh",            "Today Grid Charge",            "kWh", "energy",   "total_increasing", "mdi:battery-charging"),
    ("today_kwh_high_rate",   "Today kWh High Rate",   "kWh", "energy", "total_increasing", "mdi:lightning-bolt"),
    ("today_kwh_medium_rate", "Today kWh Medium Rate", "kWh", "energy", "total_increasing", "mdi:lightning-bolt-circle"),
    ("today_kwh_low_rate",    "Today kWh Low Rate",    "kWh", "energy", "total_increasing", "mdi:leaf"),
    ("month_kwh_high_rate",   "Month kWh High Rate",   "kWh", "energy", "total_increasing", "mdi:lightning-bolt"),
    ("month_kwh_medium_rate", "Month kWh Medium Rate", "kWh", "energy", "total_increasing", "mdi:lightning-bolt-circle"),
    ("month_kwh_low_rate",    "Month kWh Low Rate",    "kWh", "energy", "total_increasing", "mdi:leaf"),
]


@dataclass
class _MQTTConfig:
    host: str
    port: int
    username: str
    password: str


def _state_topic(key: str) -> str:
    return f"{MQTT_STATE_TOPIC_PREFIX}/sensor/{key}/state"


def _discovery_topic(key: str) -> str:
    return f"{MQTT_DISCOVERY_PREFIX}/sensor/miniems_{key}/config"


def _discovery_payload(key: str, name: str, unit: str | None, device_class: str | None,
                       state_class: str, icon: str) -> dict:
    payload: dict[str, Any] = {
        "unique_id":   f"miniems_{key}",
        "name":        name,
        "state_topic": _state_topic(key),
        "icon":        icon,
        "state_class": state_class,
        "device":      _DEVICE,
    }
    if unit:
        payload["unit_of_measurement"] = unit
    if device_class:
        payload["device_class"] = device_class
    return payload


class MQTTPublisher:
    """Publishes miniEMS sensor states via MQTT Discovery."""

    def __init__(self, supervisor_token: str) -> None:
        self._sup_token = supervisor_token
        self._cfg: _MQTTConfig | None = None
        self._available = False

    async def setup(self) -> bool:
        """Query Supervisor for MQTT broker credentials. Returns True if available."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(
                    SUPERVISOR_MQTT_URL,
                    headers={"Authorization": f"Bearer {self._sup_token}"},
                    timeout=aiohttp.ClientTimeout(total=5),
                ) as resp:
                    if resp.status != 200:
                        raw = await resp.text()
                        _LOGGER.warning(
                            "MQTT service not available (HTTP %d): %s – using REST fallback",
                            resp.status, raw[:200],
                        )
                        return False
                    data = await resp.json(content_type=None)
            _LOGGER.info("Supervisor MQTT response: %s", data)
            svc = data.get("data", data)
            self._cfg = _MQTTConfig(
                host=svc.get("host", "core-mosquitto"),
                port=int(svc.get("port", 1883)),
                username=svc.get("username", ""),
                password=svc.get("password", ""),
            )
            _LOGGER.info("Connecting to MQTT broker %s:%d (user=%s)", self._cfg.host, self._cfg.port, self._cfg.username)
            await self._publish_discovery()
            self._available = True
            _LOGGER.info("MQTT Discovery published – %d sensors registered", len(_SENSOR_DEFS))
            return True
        except Exception as exc:
            _LOGGER.warning("MQTT not available (%s: %s) – using REST fallback", type(exc).__name__, exc)
            return False

    @property
    def available(self) -> bool:
        return self._available

    async def publish(self, status: dict[str, Any]) -> None:
        """Publish current sensor states via MQTT."""
        if not self._available or not self._cfg:
            return
        try:
            async with aiomqtt.Client(
                hostname=self._cfg.host,
                port=self._cfg.port,
                username=self._cfg.username,
                password=self._cfg.password,
            ) as client:
                for key, *_ in _SENSOR_DEFS:
                    value = status.get(key)
                    if value is None:
                        continue
                    await client.publish(
                        _state_topic(key),
                        payload=str(value),
                        retain=True,
                    )
        except Exception as exc:
            _LOGGER.debug("MQTT publish error: %s", exc)

    # ------------------------------------------------------------------

    async def _publish_discovery(self) -> None:
        """Publish MQTT Discovery configs for all sensors."""
        if not self._cfg:
            return
        async with aiomqtt.Client(
            hostname=self._cfg.host,
            port=self._cfg.port,
            username=self._cfg.username,
            password=self._cfg.password,
        ) as client:
            import json
            for key, name, unit, device_class, state_class, icon in _SENSOR_DEFS:
                payload = _discovery_payload(key, name, unit, device_class, state_class, icon)
                await client.publish(
                    _discovery_topic(key),
                    payload=json.dumps(payload),
                    retain=True,
                )
