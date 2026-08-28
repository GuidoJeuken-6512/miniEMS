"""device_profile.py: loading and matching device profiles."""
import json
from pathlib import Path

import pytest

from device_profile import DeviceProfile, load_profiles, match_profiles
from device_registry import RegistrySnapshot

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ha_registry_snapshot.json").read_text()
)
_REAL_PROFILES_DIR = Path(__file__).parent.parent / "rootfs" / "usr" / "bin" / "profiles"


def _write_profile(tmp_path: Path, name: str, content: str) -> Path:
    d = tmp_path / "inverter"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(content)
    return f


_MINIMAL_DEYE_PROFILE = """
schema: 1
id: deye_test
class: inverter
match:
  manufacturer: Deye
  model: ["SG0*LP3"]
  entity_hints: [battery_max_charging_current]
roles:
  pv_power: {translation_key: [pv_power]}
  battery_power: {translation_key: [battery_power]}
signs:
  battery_power: {mode: signed, positive: discharge}
control:
  battery_charge_limit:
    quantity: power
    domain: number
    actuator_unit: A
    via: dc_current
    voltage_role: battery_voltage
    fallback_limits: {min: 0, max: 350, step: 1}
modes:
  IDLE: {grid_charge_enable: false, charge: max, discharge: max}
caveats:
  - "some caveat"
"""


class TestLoadOneProfile:
    def test_parses_a_well_formed_profile(self, tmp_path):
        path = _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        assert len(profiles) == 1
        p = profiles[0]
        assert p.profile_id == "deye_test"
        assert p.class_name == "inverter"
        assert p.manufacturer == "Deye"
        assert p.models == ("SG0*LP3",)
        assert p.entity_hints == ("battery_max_charging_current",)
        assert p.roles["pv_power"].translation_keys == ("pv_power",)
        assert p.signs["battery_power"].mode == "signed"
        assert p.signs["battery_power"].positive == "discharge"
        assert p.control["battery_charge_limit"].via == "dc_current"
        assert p.control["battery_charge_limit"].fallback_limits == {"min": 0, "max": 350, "step": 1}
        assert p.modes["IDLE"]["charge"] == "max"
        assert p.caveats == ("some caveat",)
        assert p.verified is False   # default

    def test_missing_manufacturer_is_rejected(self, tmp_path):
        _write_profile(tmp_path, "bad.yaml", "schema: 1\nclass: inverter\nmatch: {model: [X]}\n")
        assert load_profiles(tmp_path) == []

    def test_missing_model_and_entity_hints_is_rejected(self, tmp_path):
        _write_profile(tmp_path, "bad.yaml",
                        "schema: 1\nclass: inverter\nmatch: {manufacturer: Deye}\n")
        assert load_profiles(tmp_path) == []

    def test_model_without_hints_is_accepted(self, tmp_path):
        _write_profile(tmp_path, "ok.yaml",
                        "schema: 1\nclass: inverter\nmatch: {manufacturer: Deye, model: [X]}\n")
        assert len(load_profiles(tmp_path)) == 1

    def test_entity_hints_without_model_is_accepted(self, tmp_path):
        _write_profile(tmp_path, "ok.yaml",
                        "schema: 1\nclass: inverter\nmatch: {manufacturer: Deye, entity_hints: [x]}\n")
        profiles = load_profiles(tmp_path)
        assert len(profiles) == 1
        assert profiles[0].models == ()

    def test_single_string_model_becomes_a_one_tuple(self, tmp_path):
        _write_profile(tmp_path, "ok.yaml",
                        "schema: 1\nclass: inverter\nmatch: {manufacturer: Deye, model: SG0*LP3}\n")
        assert load_profiles(tmp_path)[0].models == ("SG0*LP3",)

    def test_malformed_yaml_is_skipped_not_raised(self, tmp_path):
        _write_profile(tmp_path, "bad.yaml", "not: valid: yaml: [[[")
        assert load_profiles(tmp_path) == []


class TestLoadProfilesDirectory:
    def test_nonexistent_directory_yields_empty_list(self, tmp_path):
        assert load_profiles(tmp_path / "does_not_exist") == []

    def test_loads_from_nested_class_subdirectories(self, tmp_path):
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        (tmp_path / "battery").mkdir()
        (tmp_path / "battery" / "pylontech.yaml").write_text(
            "schema: 1\nclass: battery\nmatch: {manufacturer: Pylontech, model: [X]}\n"
        )
        profiles = load_profiles(tmp_path)
        assert {p.class_name for p in profiles} == {"inverter", "battery"}


class TestMatchesDevice:
    def test_exact_manufacturer_and_model(self):
        profile = DeviceProfile(profile_id="x", class_name="inverter",
                                 manufacturer="Deye", models=("SG0*LP3",))
        from device_registry import RegistryDevice
        device = RegistryDevice(device_id="d1", manufacturer="Deye", model="SG0*LP3", name="deye8k")
        assert profile.matches_device(device) is True

    def test_wrong_manufacturer_does_not_match(self):
        from device_registry import RegistryDevice
        profile = DeviceProfile(profile_id="x", class_name="inverter",
                                 manufacturer="Deye", models=("SG0*LP3",))
        device = RegistryDevice(device_id="d1", manufacturer="SMA", model="SG0*LP3", name="x")
        assert profile.matches_device(device) is False

    def test_profile_without_models_never_matches_on_model_alone(self):
        from device_registry import RegistryDevice
        profile = DeviceProfile(profile_id="x", class_name="inverter", manufacturer="Deye")
        device = RegistryDevice(device_id="d1", manufacturer="Deye", model="SG0*LP3", name="x")
        assert profile.matches_device(device) is False


class TestMatchProfiles:
    def test_matches_by_class_and_manufacturer_model(self, tmp_path):
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        registry = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [
                {"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"},
                {"entity_id": "sensor.bp", "device_id": "dev1", "translation_key": "battery_power"},
                {"entity_id": "number.bmcc", "device_id": "dev1",
                 "translation_key": "battery_max_charging_current"},
            ],
        )
        matches = match_profiles(profiles, "inverter", registry)
        assert len(matches) == 1
        assert matches[0].strong is True
        assert matches[0].resolved_role_count == 2   # pv_power, battery_power

    def test_wrong_class_is_not_matched(self, tmp_path):
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        registry = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}], [],
        )
        assert match_profiles(profiles, "battery", registry) == []

    def test_model_match_without_all_hints_is_weak(self, tmp_path):
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        # Device matches manufacturer/model but lacks the entity_hints entity.
        registry = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        matches = match_profiles(profiles, "inverter", registry)
        assert len(matches) == 1
        assert matches[0].strong is False

    def test_empty_duplicate_device_is_enumerated_but_scores_lower(self, tmp_path):
        """The exact scenario from the roadmap doc: two devices share
        (manufacturer, model), one is an empty duplicate. match_profiles()
        enumerates BOTH (a model match alone is still a - weak - candidate);
        discarding the duplicate by resolved-role-count is
        device_resolver._pick_best_device_match()'s job, tested there."""
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        registry = RegistrySnapshot.from_lists(
            [
                {"id": "real", "manufacturer": "Deye", "model": "SG0*LP3"},
                {"id": "empty_dupe", "manufacturer": "Deye", "model": "SG0*LP3"},
            ],
            [
                {"entity_id": "sensor.pv", "device_id": "real", "translation_key": "pv_power"},
                {"entity_id": "sensor.bp", "device_id": "real", "translation_key": "battery_power"},
                {"entity_id": "number.bmcc", "device_id": "real",
                 "translation_key": "battery_max_charging_current"},
                # empty_dupe has no entities at all
            ],
        )
        matches = match_profiles(profiles, "inverter", registry)
        assert len(matches) == 2
        by_id = {m.device.device_id: m for m in matches}
        assert by_id["real"].strong is True
        assert by_id["real"].resolved_role_count == 2
        assert by_id["empty_dupe"].strong is False   # model matched, but entity_hints entity absent
        assert by_id["empty_dupe"].resolved_role_count == 0

    def test_entity_hints_only_profile_matches_without_a_model_but_stays_weak(self, tmp_path):
        """Per the roadmap doc's scoring rules: hints-only matches (no model
        in the profile at all) are ALWAYS weak, never strong/auto-bindable -
        unlike "model matches, hints missing", which is weak only because
        something's missing. A hints-only profile has nothing stronger to
        fall back to by definition."""
        _write_profile(tmp_path, "hintsonly.yaml",
                        "schema: 1\nclass: inverter\nmatch: {manufacturer: Deye, "
                        "entity_hints: [pv_power]}\nroles: {}\n")
        profiles = load_profiles(tmp_path)
        registry = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SomeUnlistedModel"}],
            [{"entity_id": "sensor.pv", "device_id": "dev1", "translation_key": "pv_power"}],
        )
        matches = match_profiles(profiles, "inverter", registry)
        assert len(matches) == 1
        assert matches[0].strong is False


_MINIMAL_SHELLY_PROFILE = """
schema: 1
id: shelly_test
class: energy_meter
match:
  manufacturer: Shelly
  model: ["Shelly 3EM"]
  group_by_config_entry: true
roles:
  l1_power: {unique_id_suffix: [emeter_0-power]}
  l2_power: {unique_id_suffix: [emeter_1-power]}
"""


def _write_energy_meter_profile(tmp_path, name, content):
    d = tmp_path / "energy_meter"
    d.mkdir(exist_ok=True)
    f = d / name
    f.write_text(content)
    return f


class TestGroupByConfigEntry:
    """docs/roadmap/v3.0-geraeteprofile.md, I7: a Shelly Gen1 3EM registers
    as 4 separate devices (1 parent + 3 phase sub-devices) sharing one
    config_entry_id, with no parent_device_id link between them at all –
    group_by_config_entry pools their entities into one match instead of
    scoring 4 (tied, unresolvable) candidates."""

    def _registry(self):
        return RegistrySnapshot.from_lists(
            [
                {"id": "parent", "manufacturer": "Shelly", "model": "Shelly 3EM",
                 "config_entry_id": "entry1"},
                {"id": "phase_a", "manufacturer": "Shelly", "model": "Shelly 3EM",
                 "config_entry_id": "entry1"},
                {"id": "phase_b", "manufacturer": "Shelly", "model": "Shelly 3EM",
                 "config_entry_id": "entry1"},
                {"id": "unrelated", "manufacturer": "Shelly", "model": "Shelly 1 Mini Gen3",
                 "config_entry_id": "entry2"},
            ],
            [
                {"entity_id": "switch.relay", "device_id": "parent",
                 "unique_id": "MAC-relay_0"},
                {"entity_id": "sensor.phase_a_power", "device_id": "phase_a",
                 "unique_id": "MAC-emeter_0-power"},
                # The collision this design has to get right: a substring
                # match on "emeter_0-power" would also hit this entity.
                {"entity_id": "sensor.phase_a_power_factor", "device_id": "phase_a",
                 "unique_id": "MAC-emeter_0-powerFactor"},
                {"entity_id": "sensor.phase_b_power", "device_id": "phase_b",
                 "unique_id": "MAC-emeter_1-power"},
                {"entity_id": "switch.other", "device_id": "unrelated",
                 "unique_id": "OTHERMAC-switch:0"},
            ],
        )

    def test_pools_entities_across_the_whole_group_into_one_match(self, tmp_path):
        _write_energy_meter_profile(tmp_path, "shelly.yaml", _MINIMAL_SHELLY_PROFILE)
        profiles = load_profiles(tmp_path)
        matches = match_profiles(profiles, "energy_meter", self._registry())
        assert len(matches) == 1   # not 3 – the group collapses to one match
        assert matches[0].strong is True
        assert matches[0].resolved_role_count == 2   # l1_power, l2_power
        assert len(matches[0].entities) == 4          # every entity in the group, pooled

    def test_unrelated_config_entry_is_not_pulled_into_the_group(self, tmp_path):
        _write_energy_meter_profile(tmp_path, "shelly.yaml", _MINIMAL_SHELLY_PROFILE)
        profiles = load_profiles(tmp_path)
        matches = match_profiles(profiles, "energy_meter", self._registry())
        uids = {e.unique_id for e in matches[0].entities}
        assert "OTHERMAC-switch:0" not in uids

    def test_representative_device_is_deterministic(self, tmp_path):
        """Doesn't matter *which* of the 4 devices is picked to represent
        the group – but it must be the same one on every call, since it
        ends up as ClassResolution.matched_device_id."""
        _write_energy_meter_profile(tmp_path, "shelly.yaml", _MINIMAL_SHELLY_PROFILE)
        profiles = load_profiles(tmp_path)
        ids = {match_profiles(profiles, "energy_meter", self._registry())[0].device.device_id
               for _ in range(5)}
        assert len(ids) == 1

    def test_ungrouped_profile_is_unaffected(self, tmp_path):
        """A profile without group_by_config_entry (every existing inverter/
        battery profile) must still score one match per device, exactly as
        before – config_entry_id sharing is opt-in per profile."""
        _write_profile(tmp_path, "deye.yaml", _MINIMAL_DEYE_PROFILE)
        profiles = load_profiles(tmp_path)
        registry = RegistrySnapshot.from_lists(
            [
                {"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3",
                 "config_entry_id": "shared"},
                {"id": "dev2", "manufacturer": "Deye", "model": "SG0*LP3",
                 "config_entry_id": "shared"},
            ],
            [
                {"entity_id": "sensor.pv1", "device_id": "dev1", "translation_key": "pv_power"},
                {"entity_id": "number.bmcc1", "device_id": "dev1",
                 "translation_key": "battery_max_charging_current"},
                {"entity_id": "sensor.pv2", "device_id": "dev2", "translation_key": "pv_power"},
                {"entity_id": "number.bmcc2", "device_id": "dev2",
                 "translation_key": "battery_max_charging_current"},
            ],
        )
        matches = match_profiles(profiles, "inverter", registry)
        assert len(matches) == 2   # NOT collapsed into one, unlike the grouped case above


class TestRealProfilesAgainstRealFixture:
    """Against the shipped profiles/*.yaml and the redacted live registry
    capture – see tests/fixtures/ha_registry_snapshot.json."""

    def test_deye_profile_loads(self):
        profiles = load_profiles(_REAL_PROFILES_DIR)
        deye = [p for p in profiles if p.profile_id == "deye_sg0x_lp3"]
        assert len(deye) == 1
        assert deye[0].verified is True

    def test_battery_profile_loads_and_is_unverified(self):
        profiles = load_profiles(_REAL_PROFILES_DIR)
        pylontech = [p for p in profiles if p.profile_id == "pylontech_force"]
        assert len(pylontech) == 1
        assert pylontech[0].verified is False

    def test_deye_profile_strongly_matches_the_real_deye_device(self):
        profiles = load_profiles(_REAL_PROFILES_DIR)
        registry = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        matches = match_profiles(profiles, "inverter", registry)
        deye_matches = [m for m in matches if m.profile.profile_id == "deye_sg0x_lp3"]
        assert len(deye_matches) == 1
        assert deye_matches[0].strong is True
        assert deye_matches[0].resolved_role_count > 10   # most of the profile's roles

    def test_shelly_profile_loads_and_is_verified(self):
        profiles = load_profiles(_REAL_PROFILES_DIR)
        shelly = [p for p in profiles if p.profile_id == "shelly_gen1_3em"]
        assert len(shelly) == 1
        assert shelly[0].verified is True
        assert shelly[0].class_name == "energy_meter"

    def test_shelly_profile_matches_once_across_the_4_grouped_devices(self):
        """The real fixture's Shelly 3EM is exactly the shape this profile
        exists for: 4 registry devices, one config_entry_id."""
        profiles = load_profiles(_REAL_PROFILES_DIR)
        registry = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        matches = match_profiles(profiles, "energy_meter", registry)
        shelly_matches = [m for m in matches if m.profile.profile_id == "shelly_gen1_3em"]
        assert len(shelly_matches) == 1
        assert shelly_matches[0].strong is True
        assert shelly_matches[0].resolved_role_count == 9   # l1..l3 power/voltage/current

    def test_shelly_1_mini_gen3_is_not_matched_by_the_3em_profile(self):
        """The other real Shelly in the fixture is a plain switch (no power
        sensor at all) on a different config_entry_id – must never be
        pulled into the 3EM's group."""
        profiles = load_profiles(_REAL_PROFILES_DIR)
        registry = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        matches = match_profiles(profiles, "energy_meter", registry)
        shelly_matches = [m for m in matches if m.profile.profile_id == "shelly_gen1_3em"]
        uids = {e.unique_id for e in shelly_matches[0].entities}
        assert not any(u and u.startswith("CC8DA2469D80") for u in uids)
