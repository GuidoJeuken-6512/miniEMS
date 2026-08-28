"""BatteryModel: free-to-charge / useable kWh from SoC."""
from battery_model import BatteryModel


def test_free_to_charge_kwh_mid_range(make_config):
    cfg = make_config(battery_capacity_kwh=10.0, battery_max_soc=95, battery_min_soc=15)
    model = BatteryModel(cfg)
    # 50% SoC -> (95-50)/100 * 10 = 4.5 kWh free
    assert model.free_to_charge_kwh(50.0) == 4.5


def test_free_to_charge_kwh_clamped_at_zero_above_max_soc(make_config):
    cfg = make_config(battery_capacity_kwh=10.0, battery_max_soc=95)
    model = BatteryModel(cfg)
    assert model.free_to_charge_kwh(100.0) == 0.0
    assert model.free_to_charge_kwh(95.0) == 0.0


def test_useable_kwh_mid_range(make_config):
    cfg = make_config(battery_capacity_kwh=10.0, battery_min_soc=15)
    model = BatteryModel(cfg)
    # 50% SoC -> (50-15)/100 * 10 = 3.5 kWh useable
    assert model.useable_kwh(50.0) == 3.5


def test_useable_kwh_clamped_at_zero_below_min_soc(make_config):
    cfg = make_config(battery_capacity_kwh=10.0, battery_min_soc=15)
    model = BatteryModel(cfg)
    assert model.useable_kwh(10.0) == 0.0
    assert model.useable_kwh(0.0) == 0.0


def test_capacity_kwh_overridable_at_runtime(make_config):
    """The dashboard/decision path can swap in a live capacity sensor reading."""
    cfg = make_config(battery_capacity_kwh=10.0, battery_max_soc=100)
    model = BatteryModel(cfg)
    model.capacity_kwh = 20.0
    assert model.free_to_charge_kwh(50.0) == 10.0
