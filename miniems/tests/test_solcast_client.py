"""SolcastClient: thin reader over the HA state cache."""
from solcast_client import SolcastClient


def test_remaining_today_kwh_reads_configured_entity(make_config, fake_ws):
    cfg = make_config(solcast_remaining_today_entity="sensor.solcast_remaining")
    fake_ws.values["sensor.solcast_remaining"] = 3.2
    client = SolcastClient(cfg, fake_ws)
    assert client.remaining_today_kwh == 3.2


def test_remaining_today_kwh_none_when_entity_blank(make_config, fake_ws):
    cfg = make_config(solcast_remaining_today_entity="")
    client = SolcastClient(cfg, fake_ws)
    assert client.remaining_today_kwh is None


def test_today_kwh_reads_configured_entity(make_config, fake_ws):
    cfg = make_config(solcast_today_entity="sensor.solcast_today")
    fake_ws.values["sensor.solcast_today"] = 12.5
    client = SolcastClient(cfg, fake_ws)
    assert client.today_kwh == 12.5


def test_tomorrow_kwh_reads_configured_entity(make_config, fake_ws):
    cfg = make_config(solcast_tomorrow_entity="sensor.solcast_tomorrow")
    fake_ws.values["sensor.solcast_tomorrow"] = 8.1
    client = SolcastClient(cfg, fake_ws)
    assert client.tomorrow_kwh == 8.1


def test_unavailable_entity_returns_none(make_config, fake_ws):
    cfg = make_config(solcast_today_entity="sensor.solcast_today")
    # not set in fake_ws.values -> get_state_value returns None
    client = SolcastClient(cfg, fake_ws)
    assert client.today_kwh is None
