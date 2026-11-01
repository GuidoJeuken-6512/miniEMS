"""HA weather.get_forecasts client for miniEMS.

Fetches daily forecast data from a HA weather entity (e.g. weather.openweathermap)
via the HA service call API.  No external API key or coordinates needed — the
existing HA OpenWeatherMap integration provides all required data.

Derived values:
  - temp_tomorrow_mean_c – tomorrow's 0–24 h local mean, from the hourly
                           forecast; (high + low) / 2 of the daily slot as
                           fallback. Same quantity as daily_stats'
                           avg_outdoor_temp_c, which the load forecast
                           matches it against.
  - temp_tomorrow_low_c  – templow of tomorrow's daily slot
  - pv_factor         – cloud_coverage of upcoming day slots → 0.0–1.0 PV yield
  - daylight_hours    – from HA instance latitude (fetched once, cached)

Cache TTL: 30 minutes (matching typical HA forecast update rate).
"""
import logging
import math
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import aiohttp

from const import WEATHER_HOURLY_CACHE_SEC

_LOGGER = logging.getLogger(__name__)

_CACHE_TTL_SEC = 30 * 60   # 30 minutes
# Fewer hourly values for tomorrow than this → hourly mean not trusted.
_MIN_HOURS_FOR_MEAN = 18
_HA_FORECAST_URL = "http://hassio/homeassistant/api/services/weather/get_forecasts"
_HA_CONFIG_URL   = "http://supervisor/core/api/config"


@dataclass
class WeatherSlot:
    dt: datetime
    temp_c: float
    cloud_pct: float
    rain_mm: float = 0.0
    temp_low_c: float | None = None


@dataclass
class ForecastSummary:
    slots: list[WeatherSlot] = field(default_factory=list)
    temp_tomorrow_mean_c: float | None = None
    pv_factor: float = 0.5
    daylight_hours: float = 12.0
    temp_today_c: float | None = None      # daytime high today
    temp_tomorrow_c: float | None = None   # daytime high tomorrow
    temp_tomorrow_low_c: float | None = None


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
        # Hourly temperatures {hour start (UTC): °C}, v3.3.0
        self._hourly: dict[datetime, float] = {}
        self._hourly_time: datetime | None = None

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
        await self.fetch_hourly_temps()   # cached; feeds tomorrow's daily mean
        summary = await self._summarise(slots)
        self._cache = summary
        self._cache_time = now
        _LOGGER.info(
            "Forecast updated (%s): tomorrow mean=%s°C low=%s°C pv_factor=%.2f daylight=%.1f h",
            self._entity,
            f"{summary.temp_tomorrow_mean_c:.1f}" if summary.temp_tomorrow_mean_c is not None else "n/a",
            f"{summary.temp_tomorrow_low_c:.1f}" if summary.temp_tomorrow_low_c is not None else "n/a",
            summary.pv_factor,
            summary.daylight_hours,
        )
        return summary

    # ------------------------------------------------------------------

    async def fetch_hourly_temps(self) -> dict[datetime, float]:
        """Hourly temperature forecast as {hour start in UTC: °C}.

        Feeds the load forecast (load_forecast.py) – the heat pump's demand is
        driven by the hourly outdoor temperature, which a daily high cannot
        represent. Empty dict when the entity offers no hourly forecast; the
        last good result is kept on transient errors.
        """
        if not self.enabled:
            return {}
        now = datetime.now(timezone.utc)
        if (self._hourly_time is not None
                and (now - self._hourly_time).total_seconds() < WEATHER_HOURLY_CACHE_SEC):
            return self._hourly
        self._hourly_time = now   # also throttles retries after an error
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    _HA_FORECAST_URL,
                    params={"return_response": "true"},
                    json={"entity_id": self._entity, "type": "hourly"},
                    headers={
                        "Authorization": f"Bearer {self._token}",
                        "Content-Type": "application/json",
                    },
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    if resp.status != 200:
                        body = await resp.text()
                        _LOGGER.warning("weather.get_forecasts hourly %d: %s",
                                        resp.status, body[:200])
                        return self._hourly
                    data = await resp.json()
        except Exception as exc:
            _LOGGER.warning("weather.get_forecasts hourly failed: %s", exc)
            return self._hourly

        raw = data.get("service_response", data).get(self._entity, {}).get("forecast", [])
        temps: dict[datetime, float] = {}
        for entry in raw or []:
            try:
                dt = datetime.fromisoformat(str(entry["datetime"]).replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    dt = dt.replace(tzinfo=timezone.utc)
                t = entry.get("temperature")
                if t is None:
                    continue
                key = dt.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
                temps[key] = float(t)
            except Exception as exc:
                _LOGGER.debug("Skipping hourly forecast slot: %s", exc)
        if temps:
            self._hourly = temps
        else:
            _LOGGER.warning("weather.get_forecasts: no hourly forecast for %s", self._entity)
        return self._hourly

    def hourly_temp_at(self, moment: datetime) -> float | None:
        """Cached hourly forecast temperature for the hour containing `moment`."""
        key = moment.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)
        return self._hourly.get(key)

    def _hourly_mean_for(self, day) -> float | None:
        """Mean of the cached hourly forecast over local `day` 0–24 h."""
        values = [t for k, t in self._hourly.items() if k.astimezone().date() == day]
        if len(values) < _MIN_HOURS_FOR_MEAN:
            return None
        return round(sum(values) / len(values), 2)

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
                    temp_low_c=(float(entry["templow"])
                                if entry.get("templow") is not None else None),
                ))
            except Exception as exc:
                _LOGGER.debug("Skipping forecast slot: %s", exc)
        return slots

    async def _summarise(self, slots: list[WeatherSlot]) -> ForecastSummary:
        lat = await self._fetch_lat()
        month = datetime.now(timezone.utc).month
        dl = daylight_hours_approx(month, lat)

        # Local dates: a daily slot stamped e.g. 23:00 UTC belongs to the next
        # local day, and the stored daily stats are cut at local midnight.
        today = datetime.now().astimezone().date()
        tomorrow = today + timedelta(days=1)
        today_slots = [s for s in slots if s.dt.astimezone().date() == today]
        tomorrow_slots = [s for s in slots if s.dt.astimezone().date() == tomorrow]

        temp_today = float(today_slots[0].temp_c) if today_slots else None
        tomorrow_slot = tomorrow_slots[0] if tomorrow_slots else None
        temp_tomorrow = float(tomorrow_slot.temp_c) if tomorrow_slot else None
        temp_low = tomorrow_slot.temp_low_c if tomorrow_slot else None

        mean = self._hourly_mean_for(tomorrow)
        if mean is None and temp_tomorrow is not None and temp_low is not None:
            mean = round((temp_tomorrow + temp_low) / 2, 2)

        # pv_factor: average clear-sky fraction of all available day slots
        if slots:
            clear_frac = sum(1 - s.cloud_pct / 100 for s in slots) / len(slots)
        else:
            clear_frac = 0.5
        pv_factor = round(clear_frac * min(1.0, dl / 12.0), 3)

        return ForecastSummary(
            slots=slots,
            temp_tomorrow_mean_c=mean,
            pv_factor=pv_factor,
            daylight_hours=dl,
            temp_today_c=temp_today,
            temp_tomorrow_c=temp_tomorrow,
            temp_tomorrow_low_c=temp_low,
        )
