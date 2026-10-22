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


# --- pv_slots (v3.3.0) -------------------------------------------------------

from datetime import datetime, timedelta, timezone  # noqa: E402

import pytest  # noqa: E402

_DETAILED = [   # real shape, excerpt from a live ha-solcast-solar attribute
    {"period_start": "2026-10-22T15:00:00+02:00", "pv_estimate": 1.5068,
     "pv_estimate10": 0.3879, "pv_estimate90": 4.1833},
    {"period_start": "2026-10-22T15:30:00+02:00", "pv_estimate": 1.5356,
     "pv_estimate10": 0.3576, "pv_estimate90": 4.6079},
    {"period_start": "2026-10-22T18:00:00+02:00", "pv_estimate": 0.0495,
     "pv_estimate10": 0.0165, "pv_estimate90": 0},
]
_T15 = datetime(2026, 10, 22, 15, 0, tzinfo=timezone(timedelta(hours=2)))


def _client(make_config, fake_ws, attr="detailedForecast", entries=None):
    cfg = make_config(solcast_today_entity="sensor.today", solcast_tomorrow_entity="")
    fake_ws.attributes["sensor.today"] = {attr: entries if entries is not None else _DETAILED}
    return SolcastClient(cfg, fake_ws)


def test_pv_slots_kw_to_kwh_per_half_hour(make_config, fake_ws):
    slots = _client(make_config, fake_ws).pv_slots("estimate")
    end, kwh = slots[_T15]
    assert end == _T15 + timedelta(minutes=30)
    assert kwh == pytest.approx(1.5068 * 0.5)


def test_pv_slots_scenarios(make_config, fake_ws):
    c = _client(make_config, fake_ws)
    assert c.pv_slots("estimate10")[_T15][1] == pytest.approx(0.3879 * 0.5)
    blend = c.pv_slots("blend", 0.7)[_T15][1]
    assert blend == pytest.approx((0.7 * 1.5068 + 0.3 * 0.3879) * 0.5)


def test_pv_slots_gap_does_not_stretch_slot(make_config, fake_ws):
    slots = _client(make_config, fake_ws).pv_slots("estimate")
    t1530 = _T15 + timedelta(minutes=30)
    assert slots[t1530][0] == t1530 + timedelta(minutes=30)   # not until 18:00


def test_pv_slots_missing_attribute_is_empty(make_config, fake_ws):
    cfg = make_config(solcast_today_entity="sensor.today", solcast_tomorrow_entity="")
    assert SolcastClient(cfg, fake_ws).pv_slots() == {}


def test_pv_slots_hourly_fallback(make_config, fake_ws):
    entries = [{"period_start": "2026-10-22T15:00:00+02:00", "pv_estimate": 2.0,
                "pv_estimate10": 1.0}]
    slots = _client(make_config, fake_ws, attr="detailedHourly", entries=entries).pv_slots("estimate")
    assert slots[_T15] == (_T15 + timedelta(hours=1), pytest.approx(2.0))


def test_pv_slots_sanitises_bad_values(make_config, fake_ws):
    entries = [
        {"period_start": "2026-10-22T15:00:00+02:00", "pv_estimate": -1, "pv_estimate10": 0},
        {"period_start": "kaputt", "pv_estimate": 1},
        {"period_start": "2026-10-22T16:00:00+02:00", "pv_estimate": 1.0, "pv_estimate10": 3.0},
        "nonsense",
    ]
    slots = _client(make_config, fake_ws, entries=entries).pv_slots("estimate10")
    assert slots[_T15][1] == 0.0
    assert slots[_T15 + timedelta(hours=1)][1] == pytest.approx(0.5)   # est10 capped at est
