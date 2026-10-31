"""battery_projection.py: per-slot battery state and refill price."""
import pytest

from battery_projection import BatteryContext, project_battery


def test_battery_drains_with_load_and_flags_empty():
    out = project_battery([0.30] * 4, [0.0] * 4, [1.0] * 4,
                          BatteryContext(stored_kwh=1.5, free_kwh=5.0, eta=0.9))
    assert [s.has_energy for s in out] == [True, True, False, False]
    assert not any(s.full for s in out)


def test_pv_surplus_fills_battery():
    out = project_battery([0.30] * 3, [3.0] * 3, [0.5] * 3,
                          BatteryContext(stored_kwh=4.0, free_kwh=3.0, eta=0.9))
    assert out[0].full is False
    assert out[2].full is True


def test_refill_price_is_next_cheapest_rate_over_eta():
    out = project_battery([0.30, 0.10, 0.25, 0.30], [0.0] * 4, [0.0] * 4,
                          BatteryContext(stored_kwh=5.0, free_kwh=5.0, eta=0.8))
    assert out[0].refill_eur_kwh == pytest.approx(0.10 / 0.8)
    assert out[2].refill_eur_kwh == pytest.approx(0.30 / 0.8)   # cheapest still ahead
    assert out[3].refill_eur_kwh == pytest.approx(0.10 / 0.8)   # none ahead → horizon min


def test_grid_charge_window_blocks_discharge_only_with_battery_control():
    rates = [0.30, 0.10, 0.10, 0.30]
    with_ctrl = project_battery(rates, [0.0] * 4, [2.0] * 4,
                                BatteryContext(stored_kwh=3.0, free_kwh=5.0, eta=0.9,
                                               grid_charging=True))
    assert [s.blocked for s in with_ctrl] == [False, True, True, False]
    # 3 − 2 in slot 0, then no discharge while blocked → 1 kWh left at slot 3
    assert with_ctrl[3].has_energy is True
    without = project_battery(rates, [0.0] * 4, [2.0] * 4,
                              BatteryContext(stored_kwh=3.0, free_kwh=5.0, eta=0.9))
    assert not any(s.blocked for s in without)
    assert without[2].has_energy is False      # drained by slots 0 and 1


def test_battery_kwh_refilled_by_later_pv_costs_feed_in():
    # half-full battery in the morning, big PV surplus at noon spills
    pv = [0.0, 0.0, 3.0, 3.0, 3.0, 0.0]
    load = [0.5] * 6
    out = project_battery([0.30] * 6, pv, load,
                          BatteryContext(stored_kwh=5.0, free_kwh=3.0, eta=0.9),
                          feed_in_eur_kwh=0.08)
    assert out[0].refilled_by_pv and out[0].refill_eur_kwh == pytest.approx(0.08)
    assert not out[5].refilled_by_pv                       # nothing spills after the last slot
    assert out[5].refill_eur_kwh == pytest.approx(0.30 / 0.9)


def test_no_pv_refill_when_battery_runs_empty_first():
    pv = [0.0, 0.0, 0.0, 4.0, 4.0]
    load = [1.0, 1.0, 1.0, 0.0, 0.0]
    out = project_battery([0.30] * 5, pv, load,
                          BatteryContext(stored_kwh=1.5, free_kwh=1.0, eta=0.9),
                          feed_in_eur_kwh=0.08)
    assert not out[0].refilled_by_pv                       # empty at slot 2 → gap from grid
    assert out[0].refill_eur_kwh == pytest.approx(0.30 / 0.9)
