"""WeatherClient: HA weather.get_forecasts, caching, derived summary values."""
from datetime import datetime, timedelta, timezone

import pytest
from aioresponses import aioresponses

from weather_client import WeatherClient, daylight_hours_approx, _HA_CONFIG_URL, _HA_FORECAST_URL


class TestDaylightHoursApprox:
    def test_summer_longer_than_winter_northern_hemisphere(self):
        summer = daylight_hours_approx(month=6, lat_deg=51.0)
        winter = daylight_hours_approx(month=12, lat_deg=51.0)
        assert summer > winter

    def test_equator_roughly_twelve_hours_year_round(self):
        for month in (3, 6, 9, 12):
            hours = daylight_hours_approx(month, lat_deg=0.0)
            assert 11.5 <= hours <= 12.5


class TestEnabled:
    def test_enabled_true_with_entity(self):
        assert WeatherClient("weather.x").enabled is True

    def test_enabled_false_without_entity(self):
        assert WeatherClient("").enabled is False


class TestFetchForecast:
    @pytest.mark.asyncio
    async def test_disabled_returns_none(self):
        client = WeatherClient("")
        assert await client.fetch_forecast() is None

    @pytest.mark.asyncio
    async def test_successful_fetch_returns_summary(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        today = datetime.now(timezone.utc).date()
        payload = {
            "service_response": {
                "weather.test": {
                    "forecast": [
                        {"datetime": f"{today}T12:00:00+00:00", "temperature": 20.0, "cloud_coverage": 10},
                        {"datetime": f"{today + timedelta(days=1)}T12:00:00+00:00", "temperature": 15.0, "cloud_coverage": 50},
                    ]
                }
            }
        }
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            summary = await client.fetch_forecast()
        assert summary is not None
        assert summary.temp_today_c == 20.0
        assert summary.temp_tomorrow_c == 15.0

    @pytest.mark.asyncio
    async def test_empty_forecast_returns_previous_cache(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload={"service_response": {"weather.test": {"forecast": []}}})
            summary = await client.fetch_forecast()
        assert summary is None   # no cache yet either

    @pytest.mark.asyncio
    async def test_non_200_returns_previous_cache(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.post(f"{_HA_FORECAST_URL}?return_response=true", status=500, body="boom")
            summary = await client.fetch_forecast()
        assert summary is None

    @pytest.mark.asyncio
    async def test_transport_error_returns_previous_cache(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.post(f"{_HA_FORECAST_URL}?return_response=true", exception=ConnectionError("boom"))
            summary = await client.fetch_forecast()
        assert summary is None

    @pytest.mark.asyncio
    async def test_cache_hit_skips_new_request(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        today = datetime.now(timezone.utc).date()
        payload = {"service_response": {"weather.test": {"forecast": [
            {"datetime": f"{today}T12:00:00+00:00", "temperature": 20.0, "cloud_coverage": 10},
        ]}}}
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            first = await client.fetch_forecast()
            second = await client.fetch_forecast()   # no new mock registered -> must use cache
        assert first is second

    @pytest.mark.asyncio
    async def test_skips_slot_with_unparsable_datetime(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        payload = {"service_response": {"weather.test": {"forecast": [
            {"datetime": "not-a-date", "temperature": 5.0, "cloud_coverage": 0},
        ]}}}
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            summary = await client.fetch_forecast()
        assert summary.slots == []

    @pytest.mark.asyncio
    async def test_lat_fetch_failure_falls_back_to_default(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        today = datetime.now(timezone.utc).date()
        payload = {"service_response": {"weather.test": {"forecast": [
            {"datetime": f"{today}T12:00:00+00:00", "temperature": 20.0, "cloud_coverage": 10},
        ]}}}
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, status=500)
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            summary = await client.fetch_forecast()
        assert summary is not None
        assert summary.daylight_hours > 0   # computed with the 51.0 fallback lat

    @pytest.mark.asyncio
    async def test_no_token_uses_default_latitude(self):
        client = WeatherClient("weather.test", supervisor_token="")
        today = datetime.now(timezone.utc).date()
        payload = {"service_response": {"weather.test": {"forecast": [
            {"datetime": f"{today}T12:00:00+00:00", "temperature": 20.0, "cloud_coverage": 10},
        ]}}}
        with aioresponses() as m:
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            summary = await client.fetch_forecast()
        assert summary is not None


class TestHourlyTemps:
    @pytest.mark.asyncio
    async def test_hourly_temps_keyed_by_utc_hour(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        payload = {"service_response": {"weather.test": {"forecast": [
            {"datetime": "2026-12-01T10:00:00+01:00", "temperature": -2.5},
            {"datetime": "2026-12-01T11:00:00+01:00", "temperature": -1.0},
            {"datetime": "2026-12-01T12:00:00+01:00"},
        ]}}}
        with aioresponses() as m:
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=payload)
            temps = await client.fetch_hourly_temps()
        assert temps[datetime(2026, 12, 1, 9, tzinfo=timezone.utc)] == -2.5
        tz1 = timezone(timedelta(hours=1))
        assert client.hourly_temp_at(datetime(2026, 12, 1, 11, 45, tzinfo=tz1)) == -1.0
        assert client.hourly_temp_at(datetime(2026, 12, 1, 12, 0, tzinfo=tz1)) is None

    @pytest.mark.asyncio
    async def test_hourly_error_keeps_cache_and_throttles(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.post(f"{_HA_FORECAST_URL}?return_response=true", status=500, body="boom")
            assert await client.fetch_hourly_temps() == {}
            # second call within the cache window does not hit HA again
            assert await client.fetch_hourly_temps() == {}

    @pytest.mark.asyncio
    async def test_hourly_disabled(self):
        assert await WeatherClient("").fetch_hourly_temps() == {}


class TestTomorrowMean:
    """temp_tomorrow_mean_c must be the same quantity as daily_stats'
    avg_outdoor_temp_c (a 0–24 h local daily mean) – not the daily high."""

    @staticmethod
    def _tomorrow_local(hour: int) -> datetime:
        tomorrow = datetime.now().astimezone().date() + timedelta(days=1)
        return datetime.combine(tomorrow, datetime.min.time()).astimezone() + timedelta(hours=hour)

    def _daily_payload(self, high, low=None):
        slot = {"datetime": self._tomorrow_local(12).astimezone(timezone.utc).isoformat(),
                "temperature": high, "cloud_coverage": 50}
        if low is not None:
            slot["templow"] = low
        return {"service_response": {"weather.test": {"forecast": [slot]}}}

    @pytest.mark.asyncio
    async def test_mean_from_hourly_forecast(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        client._hourly = {
            self._tomorrow_local(h).astimezone(timezone.utc): (4.0 if h < 12 else 10.0)
            for h in range(24)
        }
        client._hourly_time = datetime.now(timezone.utc)   # cache valid -> no hourly request
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=self._daily_payload(11.0, 2.0))
            summary = await client.fetch_forecast()
        assert summary.temp_tomorrow_c == 11.0        # daily high, unchanged
        assert summary.temp_tomorrow_low_c == 2.0
        assert summary.temp_tomorrow_mean_c == 7.0    # hourly mean, not the high

    @pytest.mark.asyncio
    async def test_mean_falls_back_to_high_low_average(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=self._daily_payload(11.0, 3.0))
            summary = await client.fetch_forecast()   # hourly request unmocked -> {}
        assert summary.temp_tomorrow_mean_c == 7.0

    @pytest.mark.asyncio
    async def test_no_mean_from_high_alone(self):
        client = WeatherClient("weather.test", supervisor_token="tok")
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true", payload=self._daily_payload(11.0))
            summary = await client.fetch_forecast()
        assert summary.temp_tomorrow_c == 11.0
        assert summary.temp_tomorrow_mean_c is None

    @pytest.mark.asyncio
    async def test_tomorrow_is_the_local_date(self):
        """A slot shortly after local midnight is tomorrow, even when its UTC
        timestamp still falls on today's UTC date."""
        client = WeatherClient("weather.test", supervisor_token="tok")
        slot = {"datetime": self._tomorrow_local(0).astimezone(timezone.utc).isoformat(),
                "temperature": 6.0, "templow": 1.0, "cloud_coverage": 0}
        with aioresponses() as m:
            m.get(_HA_CONFIG_URL, payload={"latitude": 51.0})
            m.post(f"{_HA_FORECAST_URL}?return_response=true",
                   payload={"service_response": {"weather.test": {"forecast": [slot]}}})
            summary = await client.fetch_forecast()
        assert summary.temp_tomorrow_c == 6.0
        assert summary.temp_tomorrow_mean_c == 3.5
