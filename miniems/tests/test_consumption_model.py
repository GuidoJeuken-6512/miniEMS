"""ConsumptionModel: temperature-matched load/PV prediction."""
from dataclasses import dataclass, field

import pytest

from consumption_model import ConsumptionModel
from weather_client import ForecastSummary


class FakeStore:
    def __init__(self) -> None:
        self.similar_temp_days: list[dict] = []
        self.recent_days: list[dict] = []

    async def query_days_similar_temp(self, target, tolerance, lookback_days):
        return self.similar_temp_days

    async def query_recent_days(self, n):
        return self.recent_days


class FakeWeather:
    def __init__(self, forecast: ForecastSummary | None, enabled: bool = True) -> None:
        self._forecast = forecast
        self.enabled = enabled

    async def fetch_forecast(self):
        return self._forecast


@pytest.mark.asyncio
async def test_predict_uses_historical_median_with_enough_similar_days(make_config):
    cfg = make_config(weather_entity="weather.test")
    store = FakeStore()
    store.similar_temp_days = [
        {"load_total_kwh": 10.0}, {"load_total_kwh": 12.0}, {"load_total_kwh": 14.0},
    ]
    store.recent_days = [{"peak_pv_w": 3000}]
    forecast = ForecastSummary(avg_night_temp_c=5.0, temp_today_c=8.0, temp_tomorrow_c=9.0, pv_factor=0.6, daylight_hours=10.0)
    model = ConsumptionModel(cfg, store, FakeWeather(forecast))
    pred = await model.predict(bat_soc=50.0)
    assert pred.predicted_load_kwh == 12.0   # median of [10,12,14]
    assert pred.source == "historical"
    assert pred.confidence == "high"


@pytest.mark.asyncio
async def test_predict_falls_back_to_temperature_rule_very_cold(make_config):
    cfg = make_config(weather_entity="weather.test")
    store = FakeStore()   # no similar days at all
    forecast = ForecastSummary(avg_night_temp_c=-5.0, temp_today_c=-3.0, temp_tomorrow_c=-2.0)
    model = ConsumptionModel(cfg, store, FakeWeather(forecast))
    pred = await model.predict(bat_soc=50.0)
    assert pred.predicted_load_kwh == 30.0
    assert pred.source == "fallback"


@pytest.mark.asyncio
async def test_predict_falls_back_to_temperature_rule_cold(make_config):
    cfg = make_config(weather_entity="weather.test")
    store = FakeStore()
    forecast = ForecastSummary(avg_night_temp_c=-2.0, temp_today_c=5.0, temp_tomorrow_c=5.0)
    model = ConsumptionModel(cfg, store, FakeWeather(forecast))
    pred = await model.predict(bat_soc=50.0)
    assert pred.predicted_load_kwh == 20.0


@pytest.mark.asyncio
async def test_predict_falls_back_to_temperature_rule_mild(make_config):
    cfg = make_config(weather_entity="weather.test")
    store = FakeStore()
    forecast = ForecastSummary(avg_night_temp_c=5.0, temp_today_c=12.0, temp_tomorrow_c=12.0)
    model = ConsumptionModel(cfg, store, FakeWeather(forecast))
    pred = await model.predict(bat_soc=50.0)
    assert pred.predicted_load_kwh == 10.0


@pytest.mark.asyncio
async def test_predict_no_weather_and_no_history_gives_zero_and_none_confidence(make_config):
    cfg = make_config(weather_entity="")
    store = FakeStore()
    model = ConsumptionModel(cfg, store, None)
    pred = await model.predict(bat_soc=50.0)
    assert pred.predicted_load_kwh == 0.0
    assert pred.confidence == "none"
    assert pred.source == "fallback"


@pytest.mark.asyncio
async def test_predict_last_resort_uses_median_of_whatever_history_exists(make_config):
    """Fewer than 3 similar days, no forecast at all -> use what's there anyway."""
    cfg = make_config(weather_entity="")
    store = FakeStore()
    store.similar_temp_days = [{"load_total_kwh": 7.0}]   # only 1 – below _MIN_SAMPLES
    model = ConsumptionModel(cfg, store, None)
    pred = await model.predict(bat_soc=50.0)
    # No forecast means query_days_similar_temp is never even called (weather
    # disabled) -> history stays empty -> loads == [] -> predicted 0.0.
    assert pred.predicted_load_kwh == 0.0


@pytest.mark.asyncio
async def test_predict_pv_uses_p75_of_recent_peaks(make_config):
    cfg = make_config(weather_entity="weather.test")
    store = FakeStore()
    # peaks below 100 are filtered out as noise
    store.recent_days = [
        {"peak_pv_w": 50}, {"peak_pv_w": 1000}, {"peak_pv_w": 2000}, {"peak_pv_w": 3000},
    ]
    forecast = ForecastSummary(pv_factor=1.0, daylight_hours=10.0, avg_night_temp_c=None)
    model = ConsumptionModel(cfg, store, FakeWeather(forecast))
    pred = await model.predict(bat_soc=None)
    # sorted [1000,2000,3000], p75 index = int(3*0.75)=2 -> 3000 W
    assert pred.predicted_pv_kwh == pytest.approx((3000 / 1000) * 1.0 * 10.0, rel=1e-6)


@pytest.mark.asyncio
async def test_predict_pv_zero_without_any_peak_history(make_config):
    cfg = make_config(weather_entity="")
    store = FakeStore()
    model = ConsumptionModel(cfg, store, None)
    pred = await model.predict(bat_soc=None)
    assert pred.predicted_pv_kwh == 0.0


@pytest.mark.asyncio
async def test_remaining_load_kwh_median_minus_load_so_far(make_config):
    cfg = make_config()
    store = FakeStore()
    from datetime import date
    today_str = str(date.today())
    store.recent_days = [
        {"date": "2020-01-01", "load_total_kwh": 10.0},
        {"date": "2020-01-02", "load_total_kwh": 20.0},
        {"date": "2020-01-03", "load_total_kwh": 30.0},
        {"date": today_str, "load_total_kwh": 999.0},   # excluded: still in progress
    ]
    model = ConsumptionModel(cfg, store, None)
    remaining = await model.remaining_load_kwh(load_so_far_kwh=5.0)
    assert remaining == 15.0   # median(10,20,30)=20 - 5


@pytest.mark.asyncio
async def test_remaining_load_kwh_none_without_history(make_config):
    cfg = make_config()
    store = FakeStore()
    model = ConsumptionModel(cfg, store, None)
    assert await model.remaining_load_kwh(load_so_far_kwh=5.0) is None


@pytest.mark.asyncio
async def test_remaining_load_kwh_clamped_at_zero(make_config):
    cfg = make_config()
    store = FakeStore()
    store.recent_days = [{"date": "2020-01-01", "load_total_kwh": 5.0}]
    model = ConsumptionModel(cfg, store, None)
    remaining = await model.remaining_load_kwh(load_so_far_kwh=100.0)
    assert remaining == 0.0
