"""OpenWeatherMap forecast client for miniEMS.

Fetches the next 24 h weather forecast (8 × 3 h slots) and derives:
  - avg_night_temp_c  – average temperature for 18:00–06:00 UTC slots
  - pv_factor         – expected PV yield factor 0.0–1.0
  - daylight_hours    – approximate astronomical day length

Caches the API response for 3 hours to stay within the free OWM quota.
If no API key is configured the client is disabled and returns None silently.
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timezone

import aiohttp

_LOGGER = logging.getLogger(__name__)

_OWM_URL = "https://api.openweathermap.org/data/2.5/forecast"
_CACHE_TTL_SEC = 3 * 3600  # 3 hours


@dataclass
class WeatherSlot:
    dt: datetime
    temp_c: float
    cloud_pct: float   # 0–100
    rain_mm: float = 0.0


@dataclass
class ForecastSummary:
    slots: list[WeatherSlot] = field(default_factory=list)
    avg_night_temp_c: float | None = None   # mean of 18:00–06:00 UTC slots
    pv_factor: float = 0.5                  # 0.0–1.0 expected PV yield factor
    daylight_hours: float = 12.0            # astronomical day length


def daylight_hours_approx(month: int, lat_deg: float) -> float:
    """Approximate day length using solar declination (±20 min accuracy)."""
    day_of_year = (month - 1) * 30 + 15   # mid-month approximation
    lat = math.radians(lat_deg)
    decl = math.radians(23.45 * math.sin(math.radians(360 * (284 + day_of_year) / 365)))
    cos_ha = -math.tan(lat) * math.tan(decl)
    cos_ha = max(-1.0, min(1.0, cos_ha))
    return round(2 * math.degrees(math.acos(cos_ha)) / 15, 1)


class WeatherClient:
    """Fetches and caches OpenWeatherMap 24 h forecast."""

    def __init__(self, api_key: str, lat: float, lon: float) -> None:
        self._api_key = api_key
        self._lat = lat
        self._lon = lon
        self._cache: ForecastSummary | None = None
        self._cache_time: datetime | None = None

    @property
    def enabled(self) -> bool:
        return bool(self._api_key)

    async def fetch_forecast(self) -> ForecastSummary | None:
        """Return forecast summary, hitting OWM only if cache is stale."""
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
                async with session.get(
                    _OWM_URL,
                    params={
                        "lat": self._lat,
                        "lon": self._lon,
                        "appid": self._api_key,
                        "units": "metric",
                        "cnt": 8,   # 8 × 3 h = 24 h ahead
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning("OWM API %d: %s", resp.status, body[:200])
                        return self._cache   # return stale cache on error
                    data = await resp.json()
        except Exception as exc:
            _LOGGER.warning("OWM fetch failed: %s", exc)
            return self._cache

        slots = self._parse_slots(data)
        summary = self._summarise(slots)
        self._cache = summary
        self._cache_time = now
        _LOGGER.info(
            "OWM forecast updated: night_temp=%s°C pv_factor=%.2f daylight=%.1f h",
            f"{summary.avg_night_temp_c:.1f}" if summary.avg_night_temp_c is not None else "n/a",
            summary.pv_factor,
            summary.daylight_hours,
        )
        return summary

    # ------------------------------------------------------------------

    def _parse_slots(self, data: dict) -> list[WeatherSlot]:
        slots = []
        for entry in data.get("list", []):
            dt = datetime.fromtimestamp(entry["dt"], tz=timezone.utc)
            slots.append(WeatherSlot(
                dt=dt,
                temp_c=entry["main"]["temp"],
                cloud_pct=entry.get("clouds", {}).get("all", 0),
                rain_mm=entry.get("rain", {}).get("3h", 0.0),
            ))
        return slots

    def _summarise(self, slots: list[WeatherSlot]) -> ForecastSummary:
        month = datetime.now().month
        dl = daylight_hours_approx(month, self._lat)

        night_temps = [s.temp_c for s in slots if s.dt.hour >= 18 or s.dt.hour < 6]
        avg_night = sum(night_temps) / len(night_temps) if night_temps else None

        day_slots = [s for s in slots if 6 <= s.dt.hour < 18]
        if day_slots:
            clear_frac = sum(1 - s.cloud_pct / 100 for s in day_slots) / len(day_slots)
        else:
            clear_frac = 0.5   # neutral if no daytime slots in window

        pv_factor = round(clear_frac * min(1.0, dl / 12.0), 3)

        return ForecastSummary(
            slots=slots,
            avg_night_temp_c=avg_night,
            pv_factor=pv_factor,
            daylight_hours=dl,
        )
