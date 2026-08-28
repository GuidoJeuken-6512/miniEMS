"""web_server.py: FastAPI routes, config coercion, raw file editor."""
import json

import pytest
from fastapi.testclient import TestClient

import web_server
from config_loader import Config
from web_server import _coerce, create_app


@pytest.fixture(autouse=True)
def _isolated_files(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    options_file = tmp_path / "options.json"
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(web_server, "OPTIONS_FILE", str(options_file))
    return config_file, options_file


@pytest.fixture
def client():
    status = {"mode": "Idle"}
    app = create_app(status, Config(), supervisor_token="sup-token", store=None)
    return TestClient(app)


class TestCoerce:
    def test_bool_field_from_string(self):
        assert _coerce("battery_control_enabled", "true") is True
        assert _coerce("battery_control_enabled", "false") is False

    def test_bool_field_passthrough_bool(self):
        assert _coerce("battery_control_enabled", True) is True

    def test_int_field_parses_string(self):
        assert _coerce("battery_min_soc", "42") == 42

    def test_int_field_bad_value_defaults_to_zero(self):
        assert _coerce("battery_min_soc", "not-a-number") == 0

    def test_float_field_parses_string(self):
        assert _coerce("cheap_rate_threshold_eur", "0.15") == 0.15

    def test_float_field_bad_value_defaults_to_zero(self):
        assert _coerce("cheap_rate_threshold_eur", "nope") == 0.0

    def test_unknown_field_becomes_string(self):
        assert _coerce("pv_power_entity", 123) == "123"

    def test_none_value_for_string_field_becomes_empty_string(self):
        assert _coerce("pv_power_entity", None) == ""


class TestDashboardAndPages:
    def test_dashboard_renders(self, client):
        r = client.get("/")
        assert r.status_code == 200

    def test_settings_page_renders(self, client):
        r = client.get("/settings")
        assert r.status_code == 200

    def test_log_page_renders(self, client):
        r = client.get("/log")
        assert r.status_code == 200

    def test_database_page_renders(self, client):
        r = client.get("/database")
        assert r.status_code == 200

    def test_config_json_page_renders(self, client):
        r = client.get("/config-json")
        assert r.status_code == 200

    def test_options_json_page_renders(self, client):
        r = client.get("/options-json")
        assert r.status_code == 200

    def test_devices_page_renders(self, client):
        r = client.get("/devices")
        assert r.status_code == 200


class TestApiDevices:
    async def _fake_snapshot_ok(self, long_lived_token=""):
        return (
            {"energy_sources": [
                {"type": "solar", "stat_rate": "sensor.pv_power"},
                {"type": "battery", "power_config": {"stat_rate": "sensor.batt_power"},
                 "stat_soc": "sensor.batt_soc", "capacity": 10.0},
            ]},
            [{"id": "dev1", "manufacturer": "Deye", "model": "SG0*LP3", "name": "deye8k"}],
            [
                {"entity_id": "sensor.pv_power", "device_id": "dev1"},
                {"entity_id": "sensor.batt_power", "device_id": "dev1"},
            ],
        )

    def test_returns_parsed_energy_dashboard_and_devices(self, client, monkeypatch):
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        r = client.get("/api/devices")
        data = r.json()
        assert "error" not in data
        assert data["energy_dashboard"]["candidates"]["pv_power"] == "sensor.pv_power"
        assert data["energy_dashboard"]["candidates"]["battery_power"] == "sensor.batt_power"
        assert data["energy_dashboard"]["signs"]["pv_power"] == {"mode": "unsigned", "positive": None}
        assert data["energy_dashboard"]["battery_capacity_kwh"] == 10.0
        assert len(data["devices"]) == 1
        assert data["devices"][0]["manufacturer"] == "Deye"
        assert data["devices"][0]["model"] == "SG0*LP3"
        assert data["devices"][0]["entity_count"] == 2

    def test_resolution_shows_energy_dashboard_source_for_pv_power(self, client, monkeypatch):
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        r = client.get("/api/devices")
        data = r.json()
        binding = data["resolution"]["bindings"]["pv_power"]
        assert binding["entity_id"] == "sensor.pv_power"
        assert binding["source"] == "energy_dashboard"
        assert data["resolution"]["matched_device_id"] == "dev1"

    def test_resolution_prefers_config_override_over_energy_dashboard(self, monkeypatch):
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        cfg = Config()
        cfg.pv_power_entity = "sensor.my_custom_override"
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        binding = data["resolution"]["bindings"]["pv_power"]
        assert binding["entity_id"] == "sensor.my_custom_override"
        assert binding["source"] == "config"
        # The energy-dashboard candidate is still offered -> recorded as a conflict.
        conflict_roles = {c["role"] for c in data["resolution"]["conflicts"]}
        assert "pv_power" in conflict_roles

    def test_resolution_reports_unresolved_required_control_roles(self, monkeypatch):
        async def _fake(long_lived_token=""):
            return {}, [], []   # nothing resolvable at all

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _fake)
        cfg = Config()
        cfg.battery_control_enabled = True
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        assert "battery_charge_limit" in data["resolution"]["unresolved_required"]
        assert "pv_power" in data["resolution"]["unresolved_required"]

    def test_ws_error_yields_error_field_not_a_5xx(self, client, monkeypatch):
        import ha_ws_api

        async def _raise(long_lived_token=""):
            raise ha_ws_api.HAWebSocketError("no token available")

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _raise)
        r = client.get("/api/devices")
        assert r.status_code == 200
        assert "error" in r.json()

    def test_passes_configured_long_lived_token(self, monkeypatch):
        seen = {}

        async def _fake(long_lived_token=""):
            seen["token"] = long_lived_token
            return {}, [], []

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _fake)
        cfg = Config()
        cfg.long_lived_token = "my-llt"
        app = create_app({}, cfg, "tok", None)
        TestClient(app).get("/api/devices")
        assert seen["token"] == "my-llt"

    def test_no_energy_sources_yields_empty_candidates(self, client, monkeypatch):
        async def _fake(long_lived_token=""):
            return {}, [], []

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _fake)
        r = client.get("/api/devices")
        data = r.json()
        assert data["energy_dashboard"]["candidates"] == {}
        assert data["devices"] == []


class TestApiStatus:
    def test_returns_shared_status_dict(self):
        status = {"mode": "Grid Charging (Cheap Rate)"}
        app = create_app(status, Config(), "tok", None)
        client = TestClient(app)
        r = client.get("/api/status")
        assert r.json()["mode"] == "Grid Charging (Cheap Rate)"


class TestApiDatabase:
    def test_no_store_returns_error_shape(self, client):
        r = client.get("/api/database")
        assert r.json() == {"rows": [], "error": "Store not available"}

    def test_with_store_returns_rows(self):
        class FakeStore:
            async def query_all_days(self):
                return [{"date": "2026-08-27"}]
        app = create_app({}, Config(), "tok", FakeStore())
        r = TestClient(app).get("/api/database")
        assert r.json() == {"rows": [{"date": "2026-08-27"}]}


class TestApiConfig:
    def test_get_falls_back_to_in_memory_config(self, client):
        r = client.get("/api/config")
        data = r.json()
        assert data["pv_power_entity"] == Config().pv_power_entity

    def test_get_reads_from_disk_when_present(self, client, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({"battery_min_soc": 42, "_version": 1}))
        r = client.get("/api/config")
        assert r.json()["battery_min_soc"] == 42
        assert "_version" not in r.json()

    def test_post_saves_and_coerces_types(self, client, _isolated_files):
        config_file, _ = _isolated_files
        r = client.post("/api/config", json={"battery_min_soc": "20", "battery_control_enabled": "true"})
        assert r.status_code == 200
        assert r.json()["status"] == "restarting"
        saved = json.loads(config_file.read_text())
        assert saved["battery_min_soc"] == 20
        assert saved["battery_control_enabled"] is True

    def test_post_preserves_existing_unknown_keys(self, client, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({"custom_field": "keep-me"}))
        client.post("/api/config", json={"battery_min_soc": "20"})
        saved = json.loads(config_file.read_text())
        assert saved["custom_field"] == "keep-me"

    def test_post_ignores_underscore_prefixed_keys(self, client, _isolated_files):
        config_file, _ = _isolated_files
        client.post("/api/config", json={"_version": 999, "battery_min_soc": "20"})
        saved = json.loads(config_file.read_text())
        assert saved.get("_version") != 999

    def test_post_invalid_json_returns_400(self, client):
        r = client.post("/api/config", content="not json", headers={"content-type": "application/json"})
        assert r.status_code == 400


class TestRawFile:
    def test_get_unknown_file_404(self, client):
        r = client.get("/api/rawfile/nonsense")
        assert r.status_code == 404

    def test_get_missing_file_returns_empty_object(self, client):
        r = client.get("/api/rawfile/config")
        assert r.json() == {}

    def test_get_returns_file_contents(self, client, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({"a": 1}))
        r = client.get("/api/rawfile/config")
        assert r.json() == {"a": 1}

    def test_get_malformed_json_returns_500(self, client, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text("{not json")
        r = client.get("/api/rawfile/config")
        assert r.status_code == 500

    def test_post_unknown_file_404(self, client):
        r = client.post("/api/rawfile/nonsense", json={})
        assert r.status_code == 404

    def test_post_writes_file(self, client, _isolated_files):
        config_file, _ = _isolated_files
        r = client.post("/api/rawfile/config", json={"a": 2})
        assert r.status_code == 200
        assert json.loads(config_file.read_text()) == {"a": 2}

    def test_post_invalid_json_returns_400(self, client):
        r = client.post("/api/rawfile/config", content="not json", headers={"content-type": "application/json"})
        assert r.status_code == 400

    def test_post_options_file(self, client, _isolated_files):
        _, options_file = _isolated_files
        r = client.post("/api/rawfile/options", json={"b": 3})
        assert r.status_code == 200
        assert json.loads(options_file.read_text()) == {"b": 3}
