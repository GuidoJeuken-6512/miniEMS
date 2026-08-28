"""device_resolver.py: the priority chain that binds roles to entity ids."""
import json
from pathlib import Path

import pytest

from device_profile import DeviceProfile, ProfileMatch, RoleHint, load_profiles
from device_registry import RegistryDevice, RegistrySnapshot
from device_resolver import _pick_best_device_match, resolve_class
from energy_dashboard import EnergyDashboardMap, SignSpec
from role_catalog import RoleCatalog, RoleSpec

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ha_registry_snapshot.json").read_text()
)
_PRODUCTION_CONFIG = json.loads(
    (Path(__file__).parent / "fixtures" / "production_config.json").read_text()
)
_REAL_PROFILES_DIR = Path(__file__).parent.parent / "rootfs" / "usr" / "bin" / "profiles"
_REAL_ROLES_YAML = Path(__file__).parent.parent / "rootfs" / "usr" / "bin" / "roles.yaml"


def _catalog(**roles: RoleSpec) -> RoleCatalog:
    return RoleCatalog(classes={"inverter": roles})


def _registry(devices: list[dict], entities: list[dict]) -> RegistrySnapshot:
    return RegistrySnapshot.from_lists(devices, entities)


class TestResolutionPriority:
    """Source 1 (config) beats 2 (energy dashboard) beats 3 (profile) beats
    4 (heuristic) - each isolated by giving only that one source a candidate,
    then by giving two at once and checking which wins."""

    def test_config_override_wins_over_everything(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.heuristic_pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        energy_map = EnergyDashboardMap(candidates={"pv_power": "sensor.dashboard_pv"})
        result = resolve_class(
            "inverter", catalog=catalog,
            overrides={"inverter.pv_power": "sensor.override_pv"},
            energy_map=energy_map, registry=registry, profiles=[], config_flags={},
        )
        assert result.entity_for("pv_power") == "sensor.override_pv"
        assert result.bindings["pv_power"].source == "config"

    def test_energy_dashboard_wins_over_profile_and_heuristic(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.heuristic_pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        energy_map = EnergyDashboardMap(candidates={"pv_power": "sensor.dashboard_pv"})
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=energy_map, registry=registry, profiles=[], config_flags={},
        )
        assert result.entity_for("pv_power") == "sensor.dashboard_pv"
        assert result.bindings["pv_power"].source == "energy_dashboard"

    def test_profile_wins_over_heuristic(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        profile = DeviceProfile(
            profile_id="deye", class_name="inverter", manufacturer="Deye", models=("SG0*LP3",),
            roles={"pv_power": RoleHint(translation_keys=("pv_power",))},
        )
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=[profile], config_flags={},
        )
        assert result.entity_for("pv_power") == "sensor.pv"
        assert result.bindings["pv_power"].source == "profile"

    def test_heuristic_is_last_resort(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "Unmatched"}],
            [{"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=[], config_flags={},
        )
        assert result.entity_for("pv_power") == "sensor.pv"
        assert result.bindings["pv_power"].source == "heuristic"

    def test_heuristic_narrows_by_device_class_when_domain_alone_is_ambiguous(self):
        """The real battery_capacity gotcha: two sensor-domain entities
        share a hint, only device_class tells them apart."""
        catalog = _catalog(cap=RoleSpec(
            name="cap", class_name="inverter", domain=("sensor",),
            device_class=("energy_storage",), hints=("battery_capacity",),
        ))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "Unmatched"}],
            [
                {"entity_id": "sensor.cap_energy", "device_id": "dev1",
                 "translation_key": "battery_capacity", "original_device_class": "energy_storage"},
                {"entity_id": "sensor.cap_other", "device_id": "dev1",
                 "translation_key": "battery_capacity", "original_device_class": "something_else"},
            ],
        )
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=[], config_flags={},
        )
        assert result.entity_for("cap") == "sensor.cap_energy"

    def test_heuristic_narrows_by_unit_when_device_class_alone_is_still_ambiguous(self):
        catalog = _catalog(x=RoleSpec(
            name="x", class_name="inverter", domain=("sensor",),
            device_class=("power",), unit=("W",), hints=("power_thing",),
        ))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "Unmatched"}],
            [
                {"entity_id": "sensor.watts", "device_id": "dev1", "translation_key": "power_thing",
                 "original_device_class": "power", "unit_of_measurement": "W"},
                {"entity_id": "sensor.kilowatts", "device_id": "dev1", "translation_key": "power_thing",
                 "original_device_class": "power", "unit_of_measurement": "kW"},
            ],
        )
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=[], config_flags={},
        )
        assert result.entity_for("x") == "sensor.watts"

    def test_no_candidate_anywhere_leaves_role_unbound(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", required=True,
                                              hints=("pv_power",)))
        registry = _registry([], [])
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=[], config_flags={},
        )
        assert "pv_power" not in result.bindings
        assert result.unresolved_required == ("pv_power",)


class TestConflicts:
    def test_conflict_recorded_when_two_sources_disagree(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.heuristic_pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        energy_map = EnergyDashboardMap(candidates={"pv_power": "sensor.dashboard_pv"})
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=energy_map, registry=registry, profiles=[], config_flags={},
        )
        assert len(result.conflicts) == 1
        conflict = result.conflicts[0]
        assert conflict.role == "pv_power"
        assert conflict.chosen.entity_id == "sensor.dashboard_pv"
        assert conflict.rejected[0].entity_id == "sensor.heuristic_pv"

    def test_no_conflict_when_sources_agree_on_the_same_entity(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        registry = _registry(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        energy_map = EnergyDashboardMap(candidates={"pv_power": "sensor.pv"})
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=energy_map, registry=registry, profiles=[], config_flags={},
        )
        assert result.conflicts == ()

    def test_no_conflict_when_only_one_source_has_a_candidate(self):
        catalog = _catalog(pv_power=RoleSpec(name="pv_power", class_name="inverter", hints=("pv_power",)))
        energy_map = EnergyDashboardMap(candidates={"pv_power": "sensor.dashboard_pv"})
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=energy_map, registry=_registry([], []), profiles=[], config_flags={},
        )
        assert result.conflicts == ()


class TestRequiredIfFlags:
    def test_required_if_false_does_not_report_unresolved(self):
        catalog = _catalog(charge_limit=RoleSpec(
            name="charge_limit", class_name="inverter", required_if="battery_control_enabled",
        ))
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=_registry([], []), profiles=[],
            config_flags={"battery_control_enabled": False},
        )
        assert result.unresolved_required == ()

    def test_required_if_true_reports_unresolved(self):
        catalog = _catalog(charge_limit=RoleSpec(
            name="charge_limit", class_name="inverter", required_if="battery_control_enabled",
        ))
        result = resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=_registry([], []), profiles=[],
            config_flags={"battery_control_enabled": True},
        )
        assert result.unresolved_required == ("charge_limit",)


class TestPickBestDeviceMatch:
    def _match(self, device_id, strong, count):
        profile = DeviceProfile(profile_id="p", class_name="inverter", manufacturer="Deye", models=("X",))
        device = RegistryDevice(device_id=device_id, manufacturer="Deye", model="X", name=None)
        return ProfileMatch(profile=profile, device=device, strong=strong, resolved_role_count=count)

    def test_no_matches_returns_none(self):
        assert _pick_best_device_match([]) is None

    def test_single_match_wins_regardless_of_strength(self):
        m = self._match("d1", strong=False, count=0)
        assert _pick_best_device_match([m]) is m

    def test_strong_beats_weak(self):
        weak = self._match("weak", strong=False, count=99)
        strong = self._match("strong", strong=True, count=1)
        assert _pick_best_device_match([weak, strong]) is strong

    def test_among_equal_strength_most_resolved_roles_wins(self):
        """The exact scenario from the roadmap doc: the empty duplicate
        device is discarded purely by role count, no special-case code."""
        real = self._match("real", strong=True, count=17)
        empty_dupe = self._match("empty_dupe", strong=True, count=0)
        assert _pick_best_device_match([real, empty_dupe]) is real

    def test_true_tie_returns_none_not_a_guess(self):
        a = self._match("a", strong=True, count=5)
        b = self._match("b", strong=True, count=5)
        assert _pick_best_device_match([a, b]) is None


class TestGoldenResolutionAgainstProduction:
    """Regression test: the resolver, using the real shipped roles.yaml and
    profiles/, must reproduce the entity ids the real production config.json
    already has for every role the current inverter profile covers. This is
    the roadmap doc's strongest verification point, automated - see
    docs/roadmap/v3.0-geraeteprofile.md, "Verifikation"."""

    @pytest.fixture
    def resolution(self):
        from role_catalog import load_role_catalog
        catalog = load_role_catalog(_REAL_ROLES_YAML)
        profiles = load_profiles(_REAL_PROFILES_DIR)
        registry = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        return resolve_class(
            "inverter", catalog=catalog, overrides={},
            energy_map=EnergyDashboardMap(), registry=registry, profiles=profiles,
            config_flags={"battery_control_enabled": True},
        )

    # role -> the production config.json field that must have the same value.
    _ROLE_TO_CONFIG_FIELD = {
        "pv_power": "pv_power_entity",
        "battery_soc": "battery_soc_entity",
        "battery_power": "battery_power_entity",
        "battery_voltage": "battery_voltage_entity",
        "grid_power": "grid_power_entity",
        "load_power": "load_power_entity",
        "grid_import_energy": "grid_import_energy_entity",
        "grid_import_energy_total": "grid_import_total_entity",
        "feed_in_energy": "feed_in_energy_entity",
        "feed_in_energy_total": "feed_in_total_entity",
        "load_consumption_energy": "load_consumption_entity",
        "load_consumption_energy_total": "load_consumption_total_entity",
        "today_production": "today_production_entity",
        "today_losses": "today_losses_entity",
        "power_losses": "power_losses_entity",
        "battery_state": "battery_state_entity",
        "grid_charge_enable": "grid_charge_switch_entity",
        "battery_charge_limit": "inverter_charge_current_entity",
        "battery_discharge_limit": "battery_discharging_current_entity",
    }

    @pytest.mark.parametrize("role,config_field", list(_ROLE_TO_CONFIG_FIELD.items()))
    def test_role_matches_production_config_field(self, resolution, role, config_field):
        expected = _PRODUCTION_CONFIG[config_field]
        resolved = resolution.entity_for(role)
        assert resolved == expected, (
            f"role {role!r} resolved to {resolved!r}, production config.json's "
            f"{config_field!r} is {expected!r}"
        )

    def test_no_required_role_is_unresolved(self, resolution):
        assert resolution.unresolved_required == ()

    def test_battery_capacity_resolves_to_the_sensor_not_the_number_entity(self, resolution):
        """Both sensor.deye8k_battery_capacity and number.deye8k_battery_capacity
        share the translation_key "battery_capacity" - the domain filter in
        roles.yaml (battery_capacity_kwh: domain: [sensor]) must disambiguate."""
        assert resolution.entity_for("battery_capacity_kwh") == "sensor.deye8k_battery_capacity"

    def test_resolved_device_is_the_real_deye_device(self, resolution):
        assert resolution.matched_device_id is not None
