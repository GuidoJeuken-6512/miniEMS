"""HA weather.get_forecasts client for miniEMS.

Fetches daily forecast data from a HA weather entity (e.g. weather.openweathermap)
via the HA service call API.  No external API key or coordinates needed — the
existing HA OpenWeatherMap integration provides all required data.

Derived values:
  - avg_night_temp_c  – templow of the next day's forecast slot
  - pv_factor         – cloud_coverage of upcoming day slots → 0.0–1.0 PV yield
  - daylight_hours    – from HA instance latitude (fetched once, cached)

Cache TTL: 30 minutes (matching typical HA forecast update rate).
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp

_LOGGER = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30 * 60   # 30 minutes
_HA_FORECAST_URL = "http://hassio/homeassistant/api/services/weather/get_forecasts"
_HA_CONFIG_URL   = "http://supervisor/core/api/config"


@dataclass
class WeatherSlot:
    dt: datetime
    temp_c: float
    cloud_pct: float
    rain_mm: float = 0.0


@dataclass
class ForecastSummary:
    slots: list[WeatherSlot] = field(default_factory=list)
    avg_night_temp_c: float | None = None
    pv_factor: float = 0.5
    daylight_hours: float = 12.0
    temp_today_c: float | None = None      # daytime high today
    temp_tomorrow_c: float | None = None   # daytime high tomorrow


def daylight_hours_approx(month: int, lat_deg: float) -> float:
    """Approximate day length using solar declination (±20 min accuracy)."""
    day_of_year = (month - 1) * 30 + 15
    lat = math.radians(lat_deg)
    decl = math.radians(23.45 * math.sin(math.radians(360 * (284 + day_of_year) / 365)))
    cos_ha = -math.tan(lat) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    return round(2 * math.degrees(math.acos(cos_ha)) / 15, 1)


class WeatherClient:
    """Fetches daily forecast from a HA weather entity via service call."""

    def __init__(self, weather_entity: str, supervisor_token: str = "") -> None:
        self._entity = weather_entity
        self._token = supervisor_token
        self._cache: ForecastSummary | None = None
        self._cache_time: datetime | None = None
        self._lat: float | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._entity)

    async def _fetch_lat(self) -> float:
        if self._lat is not None:
            return self._lat
        if self._token:
            try:
                async with aiohttp.ClientSession() as session:
                    async with session.get(
                        _HA_CONFIG_URL,
                        headers={"Authorization": f"Bearer {self._token}"},
                        timeout=aiohttp.ClientTimeout(total=3),
                    ) as resp:
                        if resp.status == 200:
                            data = await resp.json()
                            self._lat = float(data.get("latitude", 51.0))
                            _LOGGER.debug("HA latitude: %.4f", self._lat)
                            return self._lat
            except Exception as exc:
                _LOGGER.debug("Could not fetch HA latitude: %s", exc)
        self._lat = 51.0
        return self._lat

    async def fetch_forecast(self) -> ForecastSummary | None:
        if not self.enabled:
            return None

        now = datetime.now(timezone.utc)
        if (
            self._cache is not None
            and self._cache_time is not None
            and (now - self._cache_time).total_seconds() < _CACHE_TTL_SEC
        ):
            return self._cache

        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _HA_FORECAST_URL,
                    params={"return_response": "true"},
                    json={"entity_id": self._entity, "type": "daily"},
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning("weather.get_forecasts %d: %s", resp.status, body[:200])
                        return self._cache
                    data = await resp.json()
        except Exception as exc:
            _LOGGER.warning("weather.get_forecasts failed: %s", exc)
            return self._cache

        raw_slots = data.get("service_response", data).get(self._entity, {}).get("forecast", [])
        if not raw_slots:
            _LOGGER.warning("weather.get_forecasts: empty forecast for %s", self._entity)
            return self._cache

        slots = self._parse_slots(raw_slots)
        summary = await self._summarise(slots)
        self._cache = summary
        self._cache_time = now
        _LOGGER.info(
            "Forecast updated (%s): night_temp=%s°C pv_factor=%.2f daylight=%.1f h",
            self._entity,
            f"{summary.avg_night_temp_c:.1f}" if summary.avg_night_temp_c is not None else "n/a",
            summary.pv_factor,
            summary.daylight_hours,
        )
        return summary

    # ------------------------------------------------------------------

    def _parse_slots(self, raw: list[dict]) -> list[WeatherSlot]:
        slots = []
        for entry in raw:
            try:
                dt = datetime.fromisoformat(entry["datetime"])
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                slots.append(WeatherSlot(
                    dt=dt,
                    temp_c=float(entry.get("temperature") or 0),
                    cloud_pct=float(entry.get("cloud_coverage") or 0),
                    rain_mm=float(entry.get("precipitation") or 0),
                ))
            except Exception as exc:
                _LOGGER.debug("Skipping forecast slot: %s", exc)
        return slots

    async def _summarise(self, slots: list[WeatherSlot]) -> ForecastSummary:
        lat = await self._fetch_lat()
        month = datetime.now(timezone.utc).month
        dl = daylight_hours_approx(month, lat)

        today = datetime.now(timezone.utc).date()
        today_slots = [s for s in slots if s.dt.date() == today]
        tomorrow_slots = [s for s in slots if s.dt.date() > today]

        temp_today = float(today_slots[0].temp_c) if today_slots else None
        temp_tomorrow = float(tomorrow_slots[0].temp_c) if tomorrow_slots else None
        # avg_night_temp: daytime high of tomorrow as proxy for load matching
        avg_night = temp_tomorrow

        # pv_factor: average clear-sky fraction of all available day slots
        if slots:
            clear_frac = sum(1 - s.cloud_pct / 100 for s in slots) / len(slots)
        else:
            clear_frac = 0.5
        pv_factor = round(clear_frac * min(1.0, dl / 12.0), 3)

        return ForecastSummary(
            slots=slots,
            avg_night_temp_c=avg_night,
            pv_factor=pv_factor,
            daylight_hours=dl,
            temp_today_c=temp_today,
            temp_tomorrow_c=temp_tomorrow,
        )
