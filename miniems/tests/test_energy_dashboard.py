"""energy_dashboard.py: parsing energy/get_prefs into role candidates."""
import json
from pathlib import Path

import pytest

from energy_dashboard import SignSpec, parse_energy_prefs

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ha_registry_snapshot.json").read_text()
)


class TestEmptyOrMissingPayload:
    def test_none_payload_yields_empty_map(self):
        result = parse_energy_prefs(None)
        assert result.candidates == {}
        assert result.signs == {}
        assert result.split_candidates == {}
        assert result.battery_capacity_kwh is None

    def test_empty_dict_yields_empty_map(self):
        result = parse_energy_prefs({})
        assert result.candidates == {}

    def test_no_energy_sources_key_yields_empty_map(self):
        result = parse_energy_prefs({"device_consumption": []})
        assert result.candidates == {}

    def test_non_dict_sources_are_skipped(self):
        result = parse_energy_prefs({"energy_sources": ["not-a-dict", 42, None]})
        assert result.candidates == {}


class TestSolar:
    def test_pv_power_candidate_and_unsigned(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.total_prod", "stat_rate": "sensor.pv_power"},
        ]})
        assert result.candidates["pv_power"] == "sensor.pv_power"
        assert result.signs["pv_power"] == SignSpec(mode="unsigned")

    def test_solar_without_stat_rate_yields_no_candidate(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "solar", "stat_energy_from": "sensor.total_prod"},
        ]})
        assert "pv_power" not in result.candidates


class TestBattery:
    def test_power_config_stat_rate_is_signed_discharge_positive(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "battery", "power_config": {"stat_rate": "sensor.batt_power"},
             "stat_soc": "sensor.batt_soc", "capacity": 12.5},
        ]})
        assert result.candidates["battery_power"] == "sensor.batt_power"
        assert result.signs["battery_power"] == SignSpec(mode="signed", positive="discharge")
        assert result.candidates["battery_soc"] == "sensor.batt_soc"
        assert result.battery_capacity_kwh == 12.5

    def test_stat_rate_inverted_takes_precedence(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "battery", "power_config": {
                "stat_rate": "sensor.plain", "stat_rate_inverted": "sensor.inverted",
            }},
        ]})
        assert result.candidates["battery_power"] == "sensor.inverted"
        assert result.signs["battery_power"] == SignSpec(mode="inverted", positive="discharge")

    def test_falls_back_to_legacy_top_level_stat_rate(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "battery", "stat_rate": "sensor.legacy_power"},
        ]})
        assert result.candidates["battery_power"] == "sensor.legacy_power"
        assert result.signs["battery_power"] == SignSpec(mode="signed", positive="discharge")

    def test_capacity_int_is_coerced_to_float(self):
        result = parse_energy_prefs({"energy_sources": [{"type": "battery", "capacity": 10}]})
        assert result.battery_capacity_kwh == 10.0
        assert isinstance(result.battery_capacity_kwh, float)

    def test_no_soc_no_capacity_no_power_yields_nothing(self):
        result = parse_energy_prefs({"energy_sources": [{"type": "battery"}]})
        assert result.candidates == {}
        assert result.battery_capacity_kwh is None


class TestGrid:
    def test_single_signed_entity_import_positive(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "grid", "power_config": {"stat_rate": "sensor.grid_power"},
             "entity_energy_price": "sensor.price"},
        ]})
        assert result.candidates["grid_power"] == "sensor.grid_power"
        assert result.signs["grid_power"] == SignSpec(mode="signed", positive="import")
        assert result.candidates["price"] == "sensor.price"

    def test_split_from_to_yields_split_candidate_not_single(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "grid", "power_config": {
                "stat_rate_from": "sensor.grid_import_power",
                "stat_rate_to": "sensor.grid_export_power",
            }},
        ]})
        assert "grid_power" not in result.candidates
        assert result.split_candidates["grid_power"] == (
            "sensor.grid_import_power", "sensor.grid_export_power",
        )
        assert result.signs["grid_power"] == SignSpec(mode="split")

    def test_no_price_entity_is_fine(self):
        result = parse_energy_prefs({"energy_sources": [{"type": "grid"}]})
        assert "price" not in result.candidates


class TestUnknownSourceType:
    def test_gas_and_water_are_ignored_not_errors(self):
        result = parse_energy_prefs({"energy_sources": [
            {"type": "gas", "stat_energy_from": "sensor.gas"},
            {"type": "water", "stat_energy_from": "sensor.water"},
        ]})
        assert result.candidates == {}


class TestRealFixture:
    """Against the redacted live capture – see fixtures/ha_registry_snapshot.json."""

    def test_parses_the_real_production_style_payload(self):
        result = parse_energy_prefs(_FIXTURE["energy_prefs"])
        assert result.candidates["pv_power"] == "sensor.deye8k_power"
        assert result.candidates["battery_power"] == "sensor.deye8k_battery_power"
        assert result.candidates["battery_soc"] == "sensor.deye8k_battery"
        assert result.candidates["grid_power"] == "sensor.deye8k_grid_power"
        assert result.candidates["price"] == "sensor.octopus_a_10fc0646_electricity_price"
        assert result.battery_capacity_kwh == 25.0
        assert result.signs["grid_power"] == SignSpec(mode="signed", positive="import")
        assert result.signs["battery_power"] == SignSpec(mode="signed", positive="discharge")
