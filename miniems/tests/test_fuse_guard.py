"""B1 – main-fuse guard and §14a dimming."""
import pytest

from fuse_guard import HOLD_SEC, TRIP_FACTOR, FuseGuard, dimming_share_w
from resources import LoadDemand, ResourceCoordinator

PHASES = "sensor.l1_a,sensor.l2_a,sensor.l3_a"
POWERS = "sensor.l1_w,sensor.l2_w,sensor.l3_w"


def run(guard: FuseGuard, seconds: float, currents, powers=None, start=0.0, step=10.0):
    t = start
    while t <= start + seconds:
        guard.update(t, list(currents), powers)
        t += step
    return t - step


class TestThermalModel:
    def test_short_peak_does_not_throttle(self):
        g = FuseGuard(35, 90)
        run(g, 600, [10, 10, 10])
        g.update(610, [38, 10, 10])          # inrush, below the trip factor
        assert not g.status(610).throttling
        g.update(620, [10, 10, 10])
        assert g.headroom_a() > 0

    def test_sustained_overload_throttles(self):
        g = FuseGuard(35, 90)                 # working limit 31.5 A
        t = run(g, 600, [34, 10, 10])
        s = g.status(t)
        assert s.throttling
        assert g.headroom_a() < 0
        assert "thermal" in s.reason

    def test_single_reading_above_trip_factor_cuts_at_once(self):
        g = FuseGuard(35, 90)
        run(g, 300, [10, 10, 10])
        g.update(310, [TRIP_FACTOR * 35 + 1, 10, 10])
        assert g.status(310).tripped
        assert g.limit_w(310, own_w=5000) == 0.0

    def test_no_increase_during_hold(self):
        g = FuseGuard(35, 90)
        t = run(g, 600, [34, 10, 10])
        # Load drops back – but within HOLD_SEC nothing may rise.
        g.update(t + 1, [5, 5, 5])
        assert g.in_hold(t + 1)
        assert g.limit_w(t + 1, own_w=2000) <= 2000
        assert not g.in_hold(t + HOLD_SEC + 1)

    def test_exporting_phase_is_never_the_reason(self):
        g = FuseGuard(35, 90)
        t = run(g, 600, [34, 10, 10], powers=[-7800, 2000, 2000])
        assert not g.status(t).throttling
        assert g.headroom_a() > 0

    def test_stale_readings_never_mean_zero(self):
        g = FuseGuard(35, 90)
        run(g, 600, [12, 12, 12])
        g.update(700, [None, None, None])
        thermal = g.thermal_a()
        assert all(a is not None and a > 12 for a in thermal)   # base + margin
        fresh = FuseGuard(35, 90)
        fresh.update(0, [None, None, None])
        assert fresh.thermal_a() == [17.5, 17.5, 17.5]          # half the rated current

    def test_headroom_gives_power_per_phase_count(self):
        g = FuseGuard(35, 90, voltage_v=230)
        run(g, 600, [21.5, 21.5, 21.5])      # 10 A headroom on every phase
        assert g.limit_w(1000, own_w=0, phases=3) == pytest.approx(3 * 230 * 10, rel=0.02)
        assert g.limit_w(1000, own_w=0, phases=1) == pytest.approx(230 * 10, rel=0.02)


class TestDimming:
    def test_share_follows_priority(self):
        share = dimming_share_w(4200, {"ev": 11000, "grid_charge": 3000}, ["grid_charge", "ev"])
        assert share == {"grid_charge": 3000, "ev": 1200}

    def test_never_more_than_demand(self):
        assert dimming_share_w(4200, {"ev": 1000}, ["ev"]) == {"ev": 1000}


class TestCoordinator:
    def _coord(self, make_config, fake_ws, **kw):
        cfg = make_config(fuse_guard_enabled=True, fuse_rated_a=35.0,
                          phase_current_entities=PHASES, phase_power_entities=POWERS, **kw)
        return ResourceCoordinator(cfg, fake_ws)

    def test_inactive_gives_no_caps(self, make_config, fake_ws):
        coord = ResourceCoordinator(make_config(), fake_ws)
        assert not coord.active
        assert coord.caps(0, [LoadDemand("ev", 0, 11000)]) == {"ev": None}

    def test_missing_rating_keeps_guard_off(self, make_config, fake_ws):
        cfg = make_config(fuse_guard_enabled=True, phase_current_entities=PHASES)
        assert ResourceCoordinator(cfg, fake_ws).guard is None

    def test_headroom_goes_to_first_load(self, make_config, fake_ws):
        coord = self._coord(make_config, fake_ws)
        for e, v in zip(PHASES.split(","), (21.5, 21.5, 21.5)):
            fake_ws.values[e] = v
        for t in range(0, 600, 10):
            coord.tick(t)
        caps = coord.caps(600, [LoadDemand("grid_charge", 0, 5000, 3),
                                LoadDemand("ev", 0, 11000, 3)])
        assert caps["grid_charge"] == pytest.approx(5000)
        assert caps["ev"] == pytest.approx(3 * 230 * 10 - 5000, rel=0.05)

    def test_deficit_is_taken_from_the_last_load(self, make_config, fake_ws):
        coord = self._coord(make_config, fake_ws)
        for e in PHASES.split(","):
            fake_ws.values[e] = 34.0
        for t in range(0, 600, 10):
            coord.tick(t, controlled_w=8000)
        caps = coord.caps(600, [LoadDemand("grid_charge", 4000, 4000, 3),
                                LoadDemand("ev", 4000, 11000, 3)])
        assert caps["grid_charge"] == pytest.approx(4000)
        assert caps["ev"] < 4000

    def test_dimming_caps_the_sum(self, make_config, fake_ws):
        cfg = make_config(grid_dimming_entity="binary_sensor.steuerbox", grid_dimming_limit_w=4200)
        coord = ResourceCoordinator(cfg, fake_ws)
        fake_ws.state_cache["binary_sensor.steuerbox"] = {"state": "on"}
        caps = coord.caps(0, [LoadDemand("ev", 0, 11000), LoadDemand("grid_charge", 0, 9000)])
        assert caps["ev"] + caps["grid_charge"] == pytest.approx(4200)
        fake_ws.state_cache["binary_sensor.steuerbox"] = {"state": "off"}
        caps = coord.caps(0, [LoadDemand("ev", 0, 11000)])
        assert caps["ev"] is None
