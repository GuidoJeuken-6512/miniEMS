"""role_catalog.py: loading and querying roles.yaml."""
from pathlib import Path

import pytest

from role_catalog import RoleCatalog, RoleSpec, load_role_catalog

_REAL_ROLES_YAML = Path(__file__).parent.parent / "rootfs" / "usr" / "bin" / "roles.yaml"


class TestIsRequired:
    def test_plain_required_true(self):
        spec = RoleSpec(name="x", class_name="inverter", required=True)
        assert spec.is_required({}) is True

    def test_required_if_flag_true(self):
        spec = RoleSpec(name="x", class_name="inverter", required_if="battery_control_enabled")
        assert spec.is_required({"battery_control_enabled": True}) is True

    def test_required_if_flag_false(self):
        spec = RoleSpec(name="x", class_name="inverter", required_if="battery_control_enabled")
        assert spec.is_required({"battery_control_enabled": False}) is False

    def test_required_if_flag_missing_defaults_false(self):
        spec = RoleSpec(name="x", class_name="inverter", required_if="battery_control_enabled")
        assert spec.is_required({}) is False

    def test_neither_required_nor_required_if(self):
        spec = RoleSpec(name="x", class_name="inverter")
        assert spec.is_required({"battery_control_enabled": True}) is False


class TestLoadRoleCatalog:
    def test_missing_file_yields_empty_catalog(self, tmp_path):
        catalog = load_role_catalog(tmp_path / "nonexistent.yaml")
        assert catalog.classes == {}

    def test_malformed_yaml_yields_empty_catalog(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("classes: [this is not a mapping :::")
        catalog = load_role_catalog(f)
        assert catalog.classes == {}

    def test_empty_file_yields_empty_catalog(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("")
        catalog = load_role_catalog(f)
        assert catalog.classes == {}

    def test_parses_a_minimal_role(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("""
version: 1
classes:
  inverter:
    roles:
      pv_power:
        required: true
        kind: measurement
        quantity: power
        domain: [sensor]
        device_class: power
        unit: [W, kW]
        sign: {mode: unsigned}
        hints: [pv_power, solar_power]
""")
        catalog = load_role_catalog(f)
        spec = catalog.role("inverter", "pv_power")
        assert spec is not None
        assert spec.required is True
        assert spec.kind == "measurement"
        assert spec.quantity == "power"
        assert spec.domain == ("sensor",)
        assert spec.device_class == ("power",)
        assert spec.unit == ("W", "kW")
        assert spec.sign.mode == "unsigned"
        assert spec.hints == ("pv_power", "solar_power")

    def test_single_string_domain_becomes_a_one_tuple(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("""
classes:
  inverter:
    roles:
      x: {domain: sensor}
""")
        catalog = load_role_catalog(f)
        assert catalog.role("inverter", "x").domain == ("sensor",)

    def test_sign_with_from_to_role(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("""
classes:
  inverter:
    roles:
      grid_power:
        sign: {mode: split, from_role: grid_import_power, to_role: grid_export_power}
""")
        catalog = load_role_catalog(f)
        sign = catalog.role("inverter", "grid_power").sign
        assert sign.mode == "split"
        assert sign.from_role == "grid_import_power"
        assert sign.to_role == "grid_export_power"

    def test_role_without_sign_is_none(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("classes:\n  inverter:\n    roles:\n      x: {}\n")
        catalog = load_role_catalog(f)
        assert catalog.role("inverter", "x").sign is None

    def test_non_dict_role_entry_is_skipped(self, tmp_path):
        f = tmp_path / "roles.yaml"
        f.write_text("classes:\n  inverter:\n    roles:\n      x: not-a-dict\n")
        catalog = load_role_catalog(f)
        assert catalog.roles_for("inverter") == {}

    def test_unknown_class_returns_empty_roles(self):
        catalog = RoleCatalog(classes={"inverter": {}})
        assert catalog.roles_for("nonexistent") == {}
        assert catalog.role("nonexistent", "pv_power") is None


class TestRequiredRoles:
    def test_returns_only_currently_required_role_names(self):
        catalog = RoleCatalog(classes={"inverter": {
            "pv_power": RoleSpec(name="pv_power", class_name="inverter", required=True),
            "battery_charge_limit": RoleSpec(
                name="battery_charge_limit", class_name="inverter",
                required_if="battery_control_enabled",
            ),
            "battery_voltage": RoleSpec(name="battery_voltage", class_name="inverter"),
        }})
        assert set(catalog.required_roles("inverter", {"battery_control_enabled": False})) == {"pv_power"}
        assert set(catalog.required_roles("inverter", {"battery_control_enabled": True})) == {
            "pv_power", "battery_charge_limit",
        }


class TestRealRolesYaml:
    """Against the actual shipped roles.yaml – catches typos/schema drift
    that a synthetic fixture wouldn't."""

    def test_loads_without_error(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        assert catalog.classes != {}

    def test_all_five_classes_present(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        assert set(catalog.classes.keys()) == {"inverter", "battery", "ev", "energy_meter", "load_actor"}

    def test_inverter_required_roles_match_the_base_sensors(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        required = set(catalog.required_roles("inverter", {"battery_control_enabled": False}))
        assert required == {"pv_power", "grid_power", "load_power", "battery_soc", "battery_power"}

    def test_battery_control_enabled_adds_actuator_roles(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        required = set(catalog.required_roles("inverter", {"battery_control_enabled": True}))
        assert "grid_charge_enable" in required
        assert "battery_charge_limit" in required
        assert "battery_discharge_limit" in required

    def test_battery_class_only_requires_soc(self):
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        assert catalog.required_roles("battery", {}) == ["battery_soc"]

    def test_every_role_has_hints_or_is_actuator_only(self):
        """A role with no hints and no config-derived candidate can never
        resolve via source 4 - not necessarily wrong, but worth knowing."""
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        for class_name, roles in catalog.classes.items():
            for role_name, spec in roles.items():
                assert spec.hints or spec.kind == "actuator", (
                    f"{class_name}.{role_name} has no hints and is not an actuator"
                )
