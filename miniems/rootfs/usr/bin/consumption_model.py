"""Consumption and PV yield prediction model for miniEMS.

Uses SQLite history + HA weather forecast to predict:
  - predicted_load_kwh  – expected daily energy consumption (dashboard only)
  - predicted_pv_kwh    – expected PV yield for the next day (dashboard only)
  - remaining_load_kwh  – expected *remaining* consumption for the rest of
                          today, feeds EMSController._should_hold_pv_charge()

Temperature-based matching: find historical days with similar night-temp and
use their median load as the prediction.  Falls back to explicit temperature
rules when fewer than 3 similar days exist.

Prediction source labels:
  "historical"  – median of temperature-matched DB days
  "fallback"    – temperature rules (no historical data available)
"""
import logging
import statistics
from dataclasses import dataclass
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore
    from weather_client import ForecastSummary, WeatherClient

_LOGGER = logging.getLogger(__name__)

_LOOKBACK_DAYS = 60          # history window for temperature matching
_TEMP_WINDOW = 4.0           # ±°C tolerance for "similar" day
_MIN_SAMPLES = 3             # minimum matches before using temp-based prediction
_REMAINING_LOAD_LOOKBACK_DAYS = 14   # window for the same-day remaining-load median


@dataclass
class Prediction:
    predicted_load_kwh: float
    predicted_pv_kwh: float
    confidence: str          # "high" | "low" | "none"
    source: str              # "historical" | "fallback"
    temp_today_c: float | None = None
    temp_tomorrow_c: float | None = None


class ConsumptionModel:
    """Predicts load and PV yield; recommends whether to grid-charge."""

    def __init__(
        self,
        config: "Config",
        store: "EnergyStore",
        weather: "WeatherClient | None",
    ) -> None:
        self._cfg = config
        self._store = store
        self._weather = weather

    async def predict(self, bat_soc: float | None) -> Prediction:
        """Compute predictions and the grid-charge recommendation."""
        forecast: "ForecastSummary | None" = None
        if self._weather and self._weather.enabled:
            forecast = await self._weather.fetch_forecast()

        predicted_load, pred_source = await self._predict_load(forecast)
        predicted_pv = await self._predict_pv(forecast)

        if predicted_load == 0:
            confidence = "none"
        elif forecast and forecast.avg_night_temp_c is not None:
            confidence = "high"
        else:
            confidence = "low"

        _LOGGER.debug(
            "Prediction: load=%.2f kWh (%s), pv=%.2f kWh (%s)",
            predicted_load, pred_source, predicted_pv, confidence,
        )

        return Prediction(
            predicted_load_kwh=round(predicted_load, 2),
            predicted_pv_kwh=round(predicted_pv, 2),
            confidence=confidence,
            source=pred_source,
            temp_today_c=forecast.temp_today_c if forecast else None,
            temp_tomorrow_c=forecast.temp_tomorrow_c if forecast else None,
        )

    async def remaining_load_kwh(self, load_so_far_kwh: float) -> float | None:
        """Estimate today's still-to-come household consumption.

        Median of `load_total_kwh` from the last _REMAINING_LOAD_LOOKBACK_DAYS
        *complete* days (today excluded – it is still in progress), minus what
        has already been measured today.

        Deliberately not temperature-matched like _predict_load(): that path
        needs a configured weather forecast and ≥3 similar days, and there is
        no evidence it would beat a plain multi-day median for this narrower
        same-day question – it would just add a weather dependency for no
        proven gain. The median (rather than a single "yesterday" value)
        still smooths out one atypical prior day.

        Returns None when no historical day exists yet (fresh install) –
        callers must treat that as "unknown", not as zero remaining load.
        """
        days = await self._store.query_recent_days(_REMAINING_LOAD_LOOKBACK_DAYS)
        today_str = str(date.today())
        totals = [
            d["load_total_kwh"] for d in days
            if d["date"] != today_str and (d.get("load_total_kwh") or 0) > 0
        ]
        if not totals:
            return None
        predicted_total = statistics.median(totals)
        return max(0.0, predicted_total - load_so_far_kwh)

    # ------------------------------------------------------------------

    async def _predict_load(self, forecast: "ForecastSummary | None") -> tuple[float, str]:
        """Return (predicted_kwh, source_label)."""
        history: list[dict] = []

        if (
            forecast is not None
            and forecast.avg_night_temp_c is not None
            and self._cfg.weather_entity
        ):
            target = forecast.avg_night_temp_c
            history = await self._store.query_days_similar_temp(
                target, _TEMP_WINDOW, _LOOKBACK_DAYS
            )
            _LOGGER.debug("Temp-matched %d similar days (target=%.1f°C)", len(history), target)

        loads = [r["load_total_kwh"] for r in history if (r.get("load_total_kwh") or 0) > 0]
        if len(loads) >= _MIN_SAMPLES:
            return statistics.median(loads), "historical"

        # Temperature fallback rules (when historical data insufficient)
        if forecast is not None:
            # avg_night_temp_c serves as "minimum" proxy; temp_tomorrow_c as "maximum"
            min_t = forecast.avg_night_temp_c
            max_t = forecast.temp_tomorrow_c
            if min_t is not None and max_t is not None:
                if min_t < 0 and max_t < 0:
                    _LOGGER.debug("Fallback rule: very cold day → 30 kWh")
                    return 30.0, "fallback"
                if min_t < 0 and max_t < 10:
                    _LOGGER.debug("Fallback rule: cold day → 20 kWh")
                    return 20.0, "fallback"
                if min_t > 0 and max_t < 15:
                    _LOGGER.debug("Fallback rule: mild day → 10 kWh")
                    return 10.0, "fallback"

        # Last resort: use whatever data we have
        if loads:
            return statistics.median(loads), "fallback"

        return 0.0, "fallback"

    async def _predict_pv(self, forecast: "ForecastSummary | None") -> float:
        recent = await self._store.query_recent_days(14)
        peaks = [(r.get("peak_pv_w") or 0) for r in recent if (r.get("peak_pv_w") or 0) > 100]
        if not peaks:
            return 0.0

        # 75th-percentile of recent peak PV power (conservative)
        peaks.sort()
        p75 = peaks[int(len(peaks) * 0.75)]

        if forecast:
            pv_factor = forecast.pv_factor
            dl = forecast.daylight_hours
        else:
            import datetime
            from weather_client import daylight_hours_approx
            month = datetime.date.today().month
            dl = daylight_hours_approx(month, 51.0)
            pv_factor = 0.5   # neutral assumption without forecast

        return round(max(0.0, (p75 / 1000) * pv_factor * dl), 2)
