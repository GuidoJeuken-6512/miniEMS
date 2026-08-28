"""device_registry.py: indexing HA's device/entity registry lists."""
import json
from pathlib import Path

import pytest

from device_registry import RegistryDevice, RegistryEntity, RegistrySnapshot

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "ha_registry_snapshot.json").read_text()
)


class TestRegistryEntityFromRaw:
    def test_device_class_falls_back_to_original_device_class(self):
        entity = RegistryEntity.from_raw({
            "entity_id": "sensor.x", "device_class": None,
            "original_device_class": "energy_storage",
        })
        assert entity.device_class == "energy_storage"

    def test_explicit_device_class_override_wins(self):
        entity = RegistryEntity.from_raw({
            "entity_id": "sensor.x", "device_class": "power",
            "original_device_class": "energy_storage",
        })
        assert entity.device_class == "power"

    def test_missing_fields_default_to_none(self):
        entity = RegistryEntity.from_raw({"entity_id": "sensor.x"})
        assert entity.device_id is None
        assert entity.platform is None
        assert entity.translation_key is None


class TestRegistryDeviceFromRaw:
    def test_name_by_user_wins_over_name(self):
        device = RegistryDevice.from_raw({
            "id": "abc", "manufacturer": "Deye", "model": "SG0*LP3",
            "name": "deye8k", "name_by_user": "My Inverter",
        })
        assert device.name == "My Inverter"

    def test_falls_back_to_name_when_no_user_name(self):
        device = RegistryDevice.from_raw({"id": "abc", "name": "deye8k", "name_by_user": None})
        assert device.name == "deye8k"

    def test_model_is_kept_verbatim_even_when_a_glob_string(self):
        # HA does not glob-expand this - the literal string is what's stored.
        device = RegistryDevice.from_raw({"id": "abc", "manufacturer": "Deye", "model": "SG0*LP3"})
        assert device.model == "SG0*LP3"

    def test_null_model_is_kept_as_none_not_a_string(self):
        device = RegistryDevice.from_raw({"id": "abc", "manufacturer": "Octopus Energy Germany"})
        assert device.model is None


class TestFromListsRobustness:
    def test_empty_lists(self):
        snap = RegistrySnapshot.from_lists([], [])
        assert snap.devices == {}
        assert snap.entities == {}

    def test_none_lists(self):
        snap = RegistrySnapshot.from_lists(None, None)
        assert snap.devices == {}
        assert snap.entities == {}

    def test_skips_devices_without_an_id(self):
        snap = RegistrySnapshot.from_lists([{"manufacturer": "Deye"}], [])
        assert snap.devices == {}

    def test_skips_entities_without_an_entity_id(self):
        snap = RegistrySnapshot.from_lists([], [{"device_id": "abc"}])
        assert snap.entities == {}

    def test_skips_non_dict_entries(self):
        snap = RegistrySnapshot.from_lists(["not-a-dict"], [42])
        assert snap.devices == {}
        assert snap.entities == {}


class TestEntitiesOfAndDeviceOf:
    def test_entities_of_returns_all_entities_on_a_device(self):
        snap = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye"}],
            [
                {"entity_id": "sensor.a", "device_id": "dev1"},
                {"entity_id": "sensor.b", "device_id": "dev1"},
                {"entity_id": "sensor.c", "device_id": "dev2"},
            ],
        )
        ids = {e.entity_id for e in snap.entities_of("dev1")}
        assert ids == {"sensor.a", "sensor.b"}

    def test_entities_of_unknown_device_is_empty(self):
        snap = RegistrySnapshot.from_lists([], [])
        assert snap.entities_of("nonexistent") == []

    def test_device_of_resolves_the_owning_device(self):
        snap = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"}],
            [{"entity_id": "sensor.a", "device_id": "dev1"}],
        )
        device = snap.device_of("sensor.a")
        assert device is not None
        assert device.manufacturer == "Deye"

    def test_device_of_unregistered_entity_is_none(self):
        snap = RegistrySnapshot.from_lists([], [])
        assert snap.device_of("sensor.unknown") is None

    def test_device_of_device_less_entity_is_none(self):
        snap = RegistrySnapshot.from_lists([], [{"entity_id": "sun.sun", "device_id": None}])
        assert snap.device_of("sun.sun") is None


class TestDevicesMatching:
    def test_exact_manufacturer_and_model_match(self):
        snap = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3"},
             {"id": "dev2", "manufacturer": "Deye", "model": "SG0*LP1"}],
            [],
        )
        matches = snap.devices_matching("Deye", ["SG0*LP3", "SG0*HP3"])
        assert [d.device_id for d in matches] == ["dev1"]

    def test_no_match_returns_empty_list(self):
        snap = RegistrySnapshot.from_lists([{"id": "dev1", "manufacturer": "SMA"}], [])
        assert snap.devices_matching("Deye", ["SG0*LP3"]) == []

    def test_wrong_manufacturer_with_matching_model_string_is_not_a_match(self):
        """model is compared exactly, but manufacturer must ALSO match - a
        model string collision alone must never bind the wrong device."""
        snap = RegistrySnapshot.from_lists(
            [{"id": "dev1", "manufacturer": "SomeOtherBrand", "model": "SG0*LP3"}], [],
        )
        assert snap.devices_matching("Deye", ["SG0*LP3"]) == []


class TestRealFixture:
    """Against the redacted live capture – see fixtures/ha_registry_snapshot.json."""

    def test_builds_snapshot_from_the_real_payload(self):
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        assert len(snap.devices) == 17

    def test_deye_device_matches_by_manufacturer_and_literal_glob_model(self):
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        matches = snap.devices_matching("Deye", ["SG0*LP3", "SG0*HP3"])
        assert len(matches) == 1
        assert matches[0].model == "SG0*LP3"

    def test_lambda_devices_have_a_firmware_string_as_model(self):
        """The gotcha this fixture exists to preserve: model is not always a
        real model name - here it's a firmware version, useless as a key."""
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        lambda_devices = [d for d in snap.devices.values() if d.manufacturer == "Lambda"]
        assert len(lambda_devices) == 4
        assert all(d.model == "V0.0.8-3K" for d in lambda_devices)

    def test_octopus_and_openweather_have_null_model(self):
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        octopus = next(d for d in snap.devices.values() if d.manufacturer == "Octopus Energy Germany")
        weather = next(d for d in snap.devices.values() if d.manufacturer == "OpenWeather")
        assert octopus.model is None
        assert weather.model is None

    def test_deye_entities_resolve_back_to_the_deye_device(self):
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        deye_device = snap.devices_matching("Deye", ["SG0*LP3", "SG0*HP3"])[0]
        entities = snap.entities_of(deye_device.device_id)
        assert len(entities) > 0
        assert all(snap.device_of(e.entity_id).device_id == deye_device.device_id for e in entities)

    def test_solarman_entities_carry_a_translation_key(self):
        """translation_key is the deterministic bridge to a Solarman YAML
        item's name: - the one fact worth mining offline (see
        docs/roadmap/v3.0-geraeteprofile.md)."""
        snap = RegistrySnapshot.from_lists(_FIXTURE["devices"], _FIXTURE["entities"])
        solarman_entities = [e for e in snap.entities.values() if e.platform == "solarman"]
        assert len(solarman_entities) > 0
        assert all(e.translation_key for e in solarman_entities)
