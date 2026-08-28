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
