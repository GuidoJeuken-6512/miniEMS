"""Phase C endpoints: overrides, entity picker, validation on save, pages."""
import json

import pytest
from fastapi.testclient import TestClient

import web_server
from config_loader import Config
from overrides import OverrideStore
from web_server import create_app

STATES = [
    {"entity_id": "sensor.pv", "state": "100", "attributes": {"unit_of_measurement": "W"}},
    {"entity_id": "sensor.pv_kwh", "state": "3", "attributes": {"unit_of_measurement": "kWh"}},
]


class FakeEntities:
    def __init__(self, states):
        self._states = states

    async def states(self):
        return self._states


@pytest.fixture
def files(tmp_path, monkeypatch):
    config_file = tmp_path / "config.json"
    monkeypatch.setattr(web_server, "CONFIG_FILE", str(config_file))
    monkeypatch.setattr(web_server, "OPTIONS_FILE", str(tmp_path / "options.json"))
    return config_file


def app(**kw):
    defaults = dict(overrides=OverrideStore(), entities=FakeEntities(STATES))
    defaults.update(kw)
    return TestClient(create_app({"mode": "Idle"}, Config(), supervisor_token="t", store=None, **defaults))


class TestOverrideApi:
    def test_set_read_and_clear(self):
        c = app()
        r = c.post("/api/override", json={"device": "battery", "value": "hold", "minutes": 30})
        assert r.status_code == 200 and r.json()["battery"]["value"] == "hold"
        assert c.get("/api/status").json()["overrides"]["battery"]["value"] == "hold"
        r = c.post("/api/override", json={"clear": "all"})
        assert r.json()["battery"]["value"] == "auto"

    def test_validation(self):
        c = app()
        assert c.post("/api/override", json={"device": "battery", "value": "fly"}).status_code == 400
        assert c.post("/api/override", json={"device": "ev", "value": "now", "minutes": 5}).status_code == 400
        assert c.post("/api/override", json={"minutes": 999}).status_code == 400
        assert c.post("/api/override", json={}).status_code == 400
        assert c.post("/api/override", json={"minutes": 60}).json()["minutes"] == 60

    def test_status_carries_control_mode(self):
        status = app().get("/api/status").json()
        assert status["control_mode"]["charge_priority"] == "battery"

    def test_charge_priority_switch(self, files):
        c = app()
        assert c.post("/api/control_mode", json={"charge_priority": "ev"}).json()["charge_priority"] == "ev"
        assert json.loads(files.read_text())["charge_priority"] == "ev"
        assert c.post("/api/control_mode", json={"charge_priority": "car"}).status_code == 400


class TestEntityPicker:
    def test_candidates(self):
        r = app().get("/api/entities", params={"field": "pv_power_entity"})
        assert [e["entity_id"] for e in r.json()["entities"]] == ["sensor.pv"]

    def test_unavailable_list(self):
        r = app(entities=FakeEntities(None)).get("/api/entities", params={"field": "x"})
        assert r.status_code == 503

    def test_save_refuses_bad_entities(self, files, monkeypatch):
        monkeypatch.setattr(web_server.asyncio, "create_task", lambda coro: coro.close())
        c = app()
        r = c.post("/api/config", json={"pv_power_entity": "sensor.pv_kwh"})
        assert r.status_code == 422
        assert r.json()["problems"][0]["field"] == "pv_power_entity"
        assert not files.exists()
        r = c.post("/api/config", json={"pv_power_entity": "sensor.pv_kwh", "_force": True})
        assert r.status_code == 200
        assert json.loads(files.read_text())["pv_power_entity"] == "sensor.pv_kwh"
        assert "_force" not in json.loads(files.read_text())

    def test_save_accepts_good_entities(self, files, monkeypatch):
        monkeypatch.setattr(web_server.asyncio, "create_task", lambda coro: coro.close())
        assert app().post("/api/config", json={"pv_power_entity": "sensor.pv"}).status_code == 200


@pytest.mark.parametrize("path", ["/plan", "/costs", "/consumers", "/system", "/", "/heatpump"])
def test_pages_render_with_the_new_navigation(path):
    r = app().get(path)
    assert r.status_code == 200
    assert 'href="consumers"' in r.text and 'href="costs"' in r.text
