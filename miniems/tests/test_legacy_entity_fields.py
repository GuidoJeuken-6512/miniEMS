"""legacy_entity_fields.py: bridging Config.*_entity fields to (class, role)."""
from pathlib import Path

import pytest

from config_loader import Config
from legacy_entity_fields import INVERTER_ENTITY_FIELDS, inverter_overrides
from role_catalog import load_role_catalog

_REAL_ROLES_YAML = Path(__file__).parent.parent / "rootfs" / "usr" / "bin" / "roles.yaml"


class TestInverterOverrides:
    def test_default_valued_field_yields_no_override(self):
        defaults = Config()
        config = Config()
        assert inverter_overrides(config, defaults) == {}

    def test_changed_field_becomes_an_override(self):
        defaults = Config()
        config = Config()
        config.pv_power_entity = "sensor.my_custom_pv"
        overrides = inverter_overrides(config, defaults)
        assert overrides == {"inverter.pv_power": "sensor.my_custom_pv"}

    def test_blanked_field_yields_no_override(self):
        defaults = Config()
        config = Config()
        config.battery_state_entity = ""
        assert inverter_overrides(config, defaults) == {}

    def test_multiple_changed_fields(self):
        defaults = Config()
        config = Config()
        config.pv_power_entity = "sensor.custom_pv"
        config.grid_power_entity = "sensor.custom_grid"
        overrides = inverter_overrides(config, defaults)
        assert overrides == {
            "inverter.pv_power": "sensor.custom_pv",
            "inverter.grid_power": "sensor.custom_grid",
        }

    def test_unmapped_fields_are_ignored(self):
        """A field with no entry in INVERTER_ENTITY_FIELDS (e.g. a plain
        threshold) must never raise, even if changed."""
        defaults = Config()
        config = Config()
        config.cheap_rate_threshold_eur = 0.99
        assert inverter_overrides(config, defaults) == {}


class TestMappingIntegrity:
    def test_every_config_field_referenced_actually_exists(self):
        config = Config()
        for field_name in INVERTER_ENTITY_FIELDS:
            assert hasattr(config, field_name), f"Config has no field {field_name!r}"

    def test_every_role_referenced_exists_in_the_real_role_catalog(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        inverter_roles = catalog.roles_for("inverter")
        for field_name, role in INVERTER_ENTITY_FIELDS.items():
            assert role in inverter_roles, (
                f"legacy_entity_fields.py maps {field_name!r} -> {role!r}, "
                f"but roles.yaml has no such inverter role"
            )
