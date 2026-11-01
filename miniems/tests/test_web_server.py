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

    def test_dict_field_passthrough(self):
        assert _coerce("entity_overrides", {"inverter.pv_power": "sensor.x"}) == {
            "inverter.pv_power": "sensor.x"
        }

    def test_dict_field_non_dict_value_becomes_empty_dict(self):
        """Regression guard: without this, a stray non-dict POST value for
        entity_overrides would fall through to the str(value) branch and
        corrupt the field (docs/roadmap/v3.0-geraeteprofile.md, 'Migration'
        – 'Mine im selben Schritt entschärfen')."""
        assert _coerce("entity_overrides", "not-a-dict") == {}
        assert _coerce("entity_overrides", None) == {}


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
        binding = data["resolutions"]["inverter"]["bindings"]["pv_power"]
        assert binding["entity_id"] == "sensor.pv_power"
        assert binding["source"] == "energy_dashboard"
        assert data["resolutions"]["inverter"]["matched_device_id"] == "dev1"

    def test_resolution_status_is_one_of_the_three_values(self, client, monkeypatch):
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        r = client.get("/api/devices")
        data = r.json()
        assert data["resolutions"]["inverter"]["status"] in ("ok", "degraded", "unresolved")

    def test_resolution_status_unresolved_when_a_required_role_is_missing(self, monkeypatch):
        async def _fake(long_lived_token=""):
            return {}, [], []

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _fake)
        cfg = Config()
        cfg.battery_control_enabled = True
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        assert data["resolutions"]["inverter"]["status"] == "unresolved"

    def test_resolution_prefers_config_override_over_energy_dashboard(self, monkeypatch):
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        cfg = Config()
        cfg.pv_power_entity = "sensor.my_custom_override"
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        binding = data["resolutions"]["inverter"]["bindings"]["pv_power"]
        assert binding["entity_id"] == "sensor.my_custom_override"
        assert binding["source"] == "config"
        # The energy-dashboard candidate is still offered -> recorded as a conflict.
        conflict_roles = {c["role"] for c in data["resolutions"]["inverter"]["conflicts"]}
        assert "pv_power" in conflict_roles

    def test_resolution_prefers_entity_overrides_over_legacy_field_diff(self, monkeypatch):
        """entity_overrides (explicit, "<class>.<role>" keyed) is the more
        deliberate of the two "config" sources and must win when both are
        set for the same role – docs/roadmap/v3.0-geraeteprofile.md,
        'Auflösungskette'."""
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        cfg = Config()
        cfg.pv_power_entity = "sensor.legacy_override"
        cfg.entity_overrides = {"inverter.pv_power": "sensor.explicit_override"}
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        binding = data["resolutions"]["inverter"]["bindings"]["pv_power"]
        assert binding["entity_id"] == "sensor.explicit_override"
        assert binding["source"] == "config"

    def test_resolution_reports_unresolved_required_control_roles(self, monkeypatch):
        async def _fake(long_lived_token=""):
            return {}, [], []   # nothing resolvable at all

        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", _fake)
        cfg = Config()
        cfg.battery_control_enabled = True
        app = create_app({}, cfg, "tok", None)
        r = TestClient(app).get("/api/devices")
        data = r.json()
        assert "battery_charge_limit" in data["resolutions"]["inverter"]["unresolved_required"]
        assert "pv_power" in data["resolutions"]["inverter"]["unresolved_required"]

    def test_resolutions_include_energy_meter_class(self, client, monkeypatch):
        """docs/roadmap/v3.0-geraeteprofile.md, I6/I7: the preview resolves
        every implemented class, not just inverter."""
        monkeypatch.setattr(web_server.ha_ws_api, "get_registry_snapshot", self._fake_snapshot_ok)
        r = client.get("/api/devices")
        data = r.json()
        assert "energy_meter" in data["resolutions"]
        assert data["resolutions"]["energy_meter"]["bindings"] == {}   # fixture has no Shelly

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


class _FakeHotWater:
    enabled = True

    def __init__(self):
        self.active = False

    async def request_adhoc(self):
        self.active = True
        return {"active": True, "target_c": 57.0}

    async def cancel_adhoc(self):
        self.active = False
        return {"active": False, "target_c": 57.0}

    def status(self):
        return {"adhoc": {"active": self.active}}


class _FakeDevice:
    """Inverter / hot-water stand-in that records restores and resets."""

    enabled = True

    def __init__(self, cfg, enabled_key, sim_key):
        self.cfg, self.enabled_key, self.sim_key = cfg, enabled_key, sim_key
        self.restored_while_live: list[bool] = []
        self.resets = 0

    async def restore_safe_defaults(self):
        self.restored_while_live.append(
            getattr(self.cfg, self.enabled_key) and not getattr(self.cfg, self.sim_key))

    def reset_write_state(self):
        self.resets += 1

    def status(self):
        return {}


class TestControlMode:
    def _app(self, **cfg_kw):
        cfg = Config(**cfg_kw)
        inv = _FakeDevice(cfg, "battery_control_enabled", "battery_control_simulation")
        hw = _FakeDevice(cfg, "hw_control_enabled", "hw_control_simulation")
        c = TestClient(create_app({}, cfg, supervisor_token="t", store=None,
                                  hot_water=hw, inverter=inv))
        return c, cfg, inv, hw

    def test_get(self):
        c, *_ = self._app(battery_control_enabled=True, battery_control_simulation=False,
                          hw_control_simulation=True)
        m = c.get("/api/control_mode").json()
        assert m["battery_live"] is True
        assert m["simulation"] is True and m["simulation_mixed"] is True

    def test_switch_applies_live_and_persists_without_restart(self, _isolated_files):
        config_file, _ = _isolated_files
        config_file.write_text(json.dumps({"battery_min_soc": 20}))
        c, cfg, inv, hw = self._app(battery_control_enabled=True, hw_control_enabled=True)
        r = c.post("/api/control_mode", json={"simulation": False})
        assert r.status_code == 200
        assert r.json()["battery_live"] and r.json()["hw_live"]
        assert cfg.battery_control_simulation is False and cfg.hw_control_simulation is False
        saved = json.loads(config_file.read_text())
        assert saved["battery_control_simulation"] is False
        assert saved["hw_control_simulation"] is False
        assert saved["battery_min_soc"] == 20   # rest kept
        assert inv.resets == 1 and hw.resets == 1   # sim "confirmations" forgotten
        assert inv.restored_while_live == []         # entering live: nothing to restore

    def test_leaving_live_restores_safe_state_first(self, _isolated_files):
        c, cfg, inv, hw = self._app(battery_control_enabled=True, battery_control_simulation=False,
                                    hw_control_enabled=True, hw_control_simulation=False)
        c.post("/api/control_mode", json={"simulation": True})
        assert inv.restored_while_live == [True]   # sent while real writes were still allowed
        assert hw.restored_while_live == [True]
        c2, cfg2, inv2, hw2 = self._app(battery_control_enabled=True,
                                        battery_control_simulation=False)
        c2.post("/api/control_mode", json={"battery_control": False})
        assert inv2.restored_while_live == [True]
        assert cfg2.battery_control_enabled is False

    def test_empty_request_rejected(self, _isolated_files):
        c, *_ = self._app()
        assert c.post("/api/control_mode", json={}).status_code == 400

    def test_history_backfill_switch_persists_and_runs_once(self, _isolated_files, monkeypatch):
        import web_server
        config_file, _ = _isolated_files
        runs = []

        async def fake_backfill(cfg, store):
            runs.append(cfg.history_backfill_enabled)
            return 0

        monkeypatch.setattr(web_server, "backfill_daily_stats", fake_backfill)
        cfg = Config()
        c = TestClient(create_app({}, cfg, supervisor_token="t", store=object()))
        assert c.get("/api/control_mode").json()["history_backfill"] is False

        r = c.post("/api/control_mode", json={"history_backfill": True})
        assert r.json()["history_backfill"] is True
        assert cfg.history_backfill_enabled is True
        assert json.loads(config_file.read_text())["history_backfill_enabled"] is True
        assert runs == [True]                       # ran right away, no restart

        c.post("/api/control_mode", json={"history_backfill": True})
        assert runs == [True]                       # already on: no second run
        c.post("/api/control_mode", json={"history_backfill": False})
        assert cfg.history_backfill_enabled is False and runs == [True]


class TestHeatPumpTab:
    def test_page_renders(self, client):
        r = client.get("/heatpump")
        assert r.status_code == 200
        assert "api/hot_water/adhoc" in r.text

    def test_nav_link_on_dashboard(self, client):
        assert 'href="heatpump"' in client.get("/").text

    def test_adhoc_start_and_cancel(self):
        hw = _FakeHotWater()
        c = TestClient(create_app({}, Config(), supervisor_token="t", store=None, hot_water=hw))
        assert c.post("/api/hot_water/adhoc", json={"action": "start"}).json()["active"] is True
        assert hw.active
        assert c.post("/api/hot_water/adhoc", json={"action": "cancel"}).json()["active"] is False
        assert c.post("/api/hot_water/adhoc", json={"action": "boil"}).status_code == 400

    def test_status_shows_adhoc_before_next_tick(self):
        hw = _FakeHotWater()
        store = {"hot_water": {"adhoc": {"active": False}}, "mode": "IDLE"}
        c = TestClient(create_app(store, Config(), supervisor_token="t", store=None, hot_water=hw))
        c.post("/api/hot_water/adhoc", json={"action": "start"})
        body = c.get("/api/status").json()
        assert body["hot_water"]["adhoc"]["active"] is True
        assert body["mode"] == "IDLE"

    def test_adhoc_without_controller(self, client):
        assert client.post("/api/hot_water/adhoc", json={"action": "start"}).status_code == 409

    def test_history_without_store(self, client):
        assert client.get("/api/hot_water/history").json() == {"hours": [], "segments": [], "draws": []}
