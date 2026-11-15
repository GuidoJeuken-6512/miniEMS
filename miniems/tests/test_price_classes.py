"""A1 – relative price classes (price_classes.py) and their use in the gates."""
from datetime import date, datetime, timedelta, timezone

import pytest

from price_classes import classes_for, classify_day
from price_curve import PriceCurve

from test_ems_controller import make_controller
from test_price_curve import _AttrWS, _octopus_timeslots

TZ = timezone(timedelta(hours=1))
DAY = date(2026, 11, 16)

L, S, H = 0.274414, 0.3444, 0.3944


def _octopus_curve() -> PriceCurve:
    return PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.price")


def _classes(curve, hour=12, **kw):
    params = dict(cheap_hours=6.0, hysteresis_min_eur=0.005, hysteresis_max_eur=0.02,
                  flat_spread_eur=0.03)
    params.update(kw)
    return classes_for(curve, datetime(2026, 11, 16, hour, tzinfo=TZ), **params)


class TestOctopusDay:
    """The step tariff must come out exactly as today's low/medium/high."""

    @pytest.fixture
    def classes(self):
        return _classes(_octopus_curve())

    def test_limit_is_the_low_rate(self, classes):
        assert classes.limit_eur == pytest.approx(L)
        assert not classes.flat

    @pytest.mark.parametrize("hour,tier", [
        (0, "medium"), (2, "low"), (5, "low"), (6, "medium"), (12, "low"), (15, "low"),
        (16, "medium"), (18, "high"), (20, "high"), (21, "medium"), (23, "medium"),
    ])
    def test_tier_of_every_window(self, classes, hour, tier):
        rate = _octopus_curve().rate_at(datetime(2026, 11, 16, hour, 30, tzinfo=TZ))
        assert classes.tier(rate) == tier

    def test_only_niedrig_is_low(self, classes):
        assert classes.is_low(L)
        assert not classes.is_low(S)
        assert not classes.is_low(H)

    def test_late_evening_is_not_all_low(self):
        """A per-day limit: at 22:00 the rest of the day must not become cheap."""
        classes = _classes(_octopus_curve(), hour=22)
        assert not classes.is_low(S)

    def test_more_cheap_hours_than_niedrig_reaches_standard(self):
        classes = _classes(_octopus_curve(), cheap_hours=10)
        assert classes.is_low(S)
        assert not classes.is_low(H)


class TestShapes:
    def test_flat_day_has_nothing_low(self):
        rates = [0.30 + 0.001 * (i % 3) for i in range(96)]
        classes = classify_day(rates, DAY, 6, 0.005, 0.02, 0.03)
        assert classes.flat
        assert not classes.is_low(min(rates))
        assert classes.tier(min(rates)) == "medium"

    def test_spot_day_cheapest_six_hours_are_low(self):
        hourly = [27, 26, 25, 25, 26, 28, 32, 35, 33, 29, 24, 20,
                  17, 16, 18, 22, 28, 34, 40, 42, 38, 33, 30, 28]
        rates = [h / 100.0 for h in hourly for _ in range(4)]
        classes = classify_day(rates, DAY, 6, 0.005, 0.02, 0.03)
        low_hours = [i for i, h in enumerate(hourly) if classes.is_low(h / 100.0)]
        assert len(low_hours) == 6
        assert set(low_hours) <= {2, 3, 10, 11, 12, 13, 14, 15}
        assert classes.tier(0.42) == "high"
        assert classes.tier(0.28) == "medium"

    def test_too_few_prices_gives_none(self):
        assert classify_day([0.3] * 4, DAY, 6, 0.005, 0.02, 0.03) is None

    def test_no_curve_gives_none(self):
        assert _classes(None) is None

    def test_no_peak_above_median_means_never_high(self):
        rates = [0.20] * 48 + [0.30] * 48
        classes = classify_day(rates, DAY, 6, 0.005, 0.02, 0.03)
        assert classes.tier(0.30) == "medium"


class TestGates:
    """cost_optimizer.is_cheap_rate()/price_tier() follow the classes when set."""

    def test_optimizer_uses_classes(self, make_config):
        from cost_optimizer import CostOptimizer
        opt = CostOptimizer(make_config(cheap_rate_threshold_eur=0.10), store=None)
        assert opt.is_cheap_rate(L) is False          # fixed threshold: 27 ct is not cheap
        opt.set_price_classes(_classes(_octopus_curve()))
        assert opt.is_cheap_rate(L) is True
        assert opt.price_tier(H) == "high"
        opt.set_price_classes(None)
        assert opt.is_cheap_rate(L) is False

    def test_controller_off_leaves_optimizer_untouched(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, price_classes_enabled=False)
        fake_ws.attributes[ctrl._cfg.electricity_price_entity] = _octopus_timeslots()
        ctrl._update_price_classes(datetime(2026, 11, 16, 3, tzinfo=TZ), L)
        assert not hasattr(ctrl._optimizer, "_classes_set")

    def test_controller_on_sets_classes_and_hot_water_rate(self, make_config, fake_ws):
        class Opt:
            price_classes = None

            def set_price_classes(self, c):
                self.price_classes = c

        class HW:
            rate = "unset"

            def set_cheap_rate(self, r):
                self.rate = r

        ctrl = make_controller(make_config, fake_ws, price_classes_enabled=True)
        ctrl._optimizer = Opt()
        ctrl._hot_water = HW()
        fake_ws.attributes[ctrl._cfg.electricity_price_entity] = _octopus_timeslots()
        ctrl._update_price_classes(datetime(2026, 11, 16, 3, tzinfo=TZ), L)
        assert ctrl._optimizer.price_classes.limit_eur == pytest.approx(L)
        assert ctrl._hot_water.rate == pytest.approx(L + 1e-6)
        assert ctrl._price_classes_dict()["limit_eur"] == pytest.approx(L, abs=1e-5)


class TestDatedParsing:
    """A9 (Einlesen): the unit is decided for the whole list, more shapes."""

    @staticmethod
    def _rates(values, start=datetime(2026, 11, 16, 0, tzinfo=TZ), key="value"):
        return [{"start": (start + timedelta(hours=i)).isoformat(), key: v}
                for i, v in enumerate(values)]

    def test_small_ct_values_stay_ct(self):
        """1.5 ct at noon must not become 1.50 €/kWh (old per-value rule)."""
        ws = _AttrWS({"rates": self._rates([25.0, 12.0, 1.5, -3.0])})
        curve = PriceCurve.from_entity(ws, "sensor.p", "rates")
        noon = datetime(2026, 11, 16, 2, 30, tzinfo=TZ)
        assert curve.rate_at(noon) == pytest.approx(0.015)
        assert curve.rate_at(noon + timedelta(hours=1)) == pytest.approx(-0.03)

    def test_eur_list_kept(self):
        ws = _AttrWS({"rates": self._rates([0.25, 0.30, -0.01])})
        curve = PriceCurve.from_entity(ws, "sensor.p", "rates")
        assert curve.rate_at(datetime(2026, 11, 16, 2, 10, tzinfo=TZ)) == pytest.approx(-0.01)

    def test_eur_per_mwh_detected(self):
        ws = _AttrWS({"rates": self._rates([85.0, 120.0, 95.0, -5.0])})
        curve = PriceCurve.from_entity(ws, "sensor.p", "rates")
        assert curve.rate_at(datetime(2026, 11, 16, 1, 10, tzinfo=TZ)) == pytest.approx(0.12)

    def test_explicit_unit_wins(self):
        ws = _AttrWS({"rates": self._rates([1.0, 1.5, 0.5])})
        curve = PriceCurve.from_entity(ws, "sensor.p", "rates", unit="ct")
        assert curve.rate_at(datetime(2026, 11, 16, 0, 10, tzinfo=TZ)) == pytest.approx(0.01)

    def test_nord_pool_raw_today_and_tomorrow(self):
        today = self._rates([0.20, 0.30])
        tomorrow = self._rates([0.10], start=datetime(2026, 11, 17, 0, tzinfo=TZ))
        ws = _AttrWS({"raw_today": today, "raw_tomorrow": tomorrow})
        curve = PriceCurve.from_entity(ws, "sensor.p")
        assert curve.is_dated
        assert curve.rate_at(datetime(2026, 11, 17, 0, 30, tzinfo=TZ)) == pytest.approx(0.10)

    def test_entsoe_time_key(self):
        prices = [{"time": (datetime(2026, 11, 16, 0, tzinfo=TZ) + timedelta(hours=i)).isoformat(),
                   "price": v} for i, v in enumerate([0.21, 0.22])]
        curve = PriceCurve.from_entity(_AttrWS({"prices": prices}), "sensor.p")
        assert curve.rate_at(datetime(2026, 11, 16, 1, 5, tzinfo=TZ)) == pytest.approx(0.22)

    def test_adjust_applies_fees_and_vat(self):
        ws = _AttrWS({"rates": self._rates([0.10, 0.20])})
        curve = PriceCurve.from_entity(ws, "sensor.p", "rates", adjust=lambda p: (p + 0.15) * 1.19)
        assert curve.rate_at(datetime(2026, 11, 16, 0, 5, tzinfo=TZ)) == pytest.approx(0.2975)

    def test_adjust_applies_to_timeslots(self):
        curve = PriceCurve.from_entity(_AttrWS(_octopus_timeslots()), "sensor.p",
                                       adjust=lambda p: p + 0.01)
        assert curve.rate_at(datetime(2026, 11, 16, 3, tzinfo=TZ)) == pytest.approx(L + 0.01)


class TestSensorPrice:
    def test_unchanged_by_default(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        fake_ws.values[ctrl._cfg.electricity_price_entity] = 0.2744
        assert ctrl._sensor_price() == pytest.approx(0.2744)

    def test_spot_components(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws, price_is_spot=True,
                               price_fee_eur_kwh=0.15, price_vat_pct=19.0, price_unit="ct")
        fake_ws.values[ctrl._cfg.electricity_price_entity] = 10.0
        assert ctrl._sensor_price() == pytest.approx((0.10 + 0.15) * 1.19)
