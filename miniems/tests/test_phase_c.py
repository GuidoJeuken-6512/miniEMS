"""Phase C – plain text, overrides, timeline, fidelity, savings, entity check."""
from datetime import date, datetime, timedelta, timezone

import pytest
from freezegun import freeze_time

import store as store_module
from const import EMSMode
from ems_controller import ModeDecision
from ha_entities import candidates, validate
from overrides import MAX_MINUTES, OverrideError, OverrideStore
from plan_fidelity import ChannelReading, PlanJournal
from reason_text import explain_all, explain_device, explain_mode
from savings import MIN_COVERAGE, PROFILE_WEIGHTS, baseline_cost, day_saving, summarise
from store import EnergyStore
from timeline import build_timeline, next_action

from test_ems_controller import make_controller
from test_ems_controller_update import _NIEDRIG_0206_TIMESLOTS, build_controller

TZ = timezone(timedelta(hours=1))
T0 = datetime(2026, 11, 16, 12, 0, tzinfo=TZ)


@pytest.fixture
async def db(tmp_path, monkeypatch):
    monkeypatch.setattr(store_module, "DB_FILE", str(tmp_path / "test.db"))
    s = EnergyStore()
    await s.open()
    yield s
    await s.close()


# ----------------------------------------------------------------------
# C1
# ----------------------------------------------------------------------

class TestReasonText:
    def test_bridge_plan_sentence_with_prices(self):
        ctx = {"price_eur": 0.18, "peak_eur": 0.31}
        assert explain_mode("Grid Charging (Cheap Rate)", "bridge plan: 3.2 kWh by 05:00", ctx, "de") == \
            "Netzladen: 3,2 kWh bis 05:00, weil später bis 31,0 ct statt jetzt 18,0 ct."
        assert explain_mode("Grid Charging (Cheap Rate)", "bridge plan: 3.2 kWh by 05:00", ctx, "en") == \
            "Grid charging: 3.2 kWh by 05:00, because later up to 31.0 ct instead of 18.0 ct now."

    def test_hold_for_peak(self):
        text = explain_mode("Hold for Peak (No Discharge)",
                            "hold for peak: 4.2 kWh needed later at >= 31 ct, 3.0 kWh available", {}, "de")
        assert "4,2 kWh" in text and "31 ct" in text and "3,0 kWh" in text

    def test_unknown_reason_is_passed_through(self):
        assert explain_mode("Idle", "something new", {}, "en") == "Self-use: something new"

    def test_override_reason(self):
        assert explain_mode("Grid Charging (Cheap Rate)", "override: grid_charge until 14:30", {}, "de") == \
            "Von Hand: Netzladen bis 14:30."

    def test_ev_and_heating(self):
        assert explain_device("ev", "pv only: pv surplus 2347 W – start 1-phase", {}, "de") == \
            "Startet mit PV-Überschuss (2347 W), 1-phasig."
        assert explain_device("heating", "price high – compressor lock until 15:30", {}, "de") == \
            "Teurer Strom – Vorlauf abgesenkt (compressor lock until 15:30)."
        assert explain_device("hot_water", "no charge needed", {}, "en") == "Hot water: no charge needed"

    def test_explain_all_block(self):
        out = explain_all({"mode": "Idle", "mode_reason": "no action",
                           "ev": {"command": {"reason": "not plugged in"}}})
        assert out["mode"]["de"].startswith("Nichts zu tun")
        assert out["ev"] == {"de": "Nicht eingesteckt.", "en": "Not plugged in."}


# ----------------------------------------------------------------------
# C2
# ----------------------------------------------------------------------

class TestOverrides:
    @pytest.mark.asyncio
    async def test_set_expire_and_validate(self):
        ov = OverrideStore()
        o = await ov.set("battery", "grid_charge", T0, 30)
        assert ov.value("battery", T0 + timedelta(minutes=29)) == "grid_charge"
        assert ov.value("battery", T0 + timedelta(minutes=31)) == "auto"
        assert o.until == T0 + timedelta(minutes=30)
        with pytest.raises(OverrideError):
            await ov.set("battery", "now", T0)
        with pytest.raises(OverrideError):
            await ov.set("ev", "now", T0, MAX_MINUTES + 1)
        with pytest.raises(OverrideError):
            await ov.set("boiler", "auto", T0)

    @pytest.mark.asyncio
    async def test_auto_ends_and_default_minutes(self):
        ov = OverrideStore()
        await ov.set_minutes(45)
        o = await ov.set("ev", "pv", T0)
        assert o.until == T0 + timedelta(minutes=45)
        await ov.set("ev", "auto", T0)
        assert ov.value("ev", T0) == "auto"

    @pytest.mark.asyncio
    async def test_survives_a_restart(self, db):
        ov = OverrideStore(db)
        await ov.set("battery", "hold", T0, 60)
        fresh = OverrideStore(db)
        await fresh.load()
        assert fresh.value("battery", T0 + timedelta(minutes=10)) == "hold"
        assert fresh.minutes == 120

    @pytest.mark.asyncio
    async def test_rank_three_in_the_decision(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        await ctrl.overrides.set("battery", "grid_charge", T0, 60)
        d = ctrl._apply_override(ModeDecision(EMSMode.IDLE, "no action"), 50.0, T0)
        assert d.mode is EMSMode.GRID_CHARGING and d.urgent
        assert d.reason == "override: grid_charge until 13:00"
        # Hard limits win.
        protect = ModeDecision(EMSMode.PROTECT_BATTERY, "soc below min", urgent=True)
        assert ctrl._apply_override(protect, 10.0, T0) is protect
        # Full battery: nothing to charge.
        idle = ModeDecision(EMSMode.IDLE, "battery full")
        assert ctrl._apply_override(idle, 99.0, T0) is idle

    @pytest.mark.asyncio
    async def test_no_grid_charge_and_hold(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        await ctrl.overrides.set("battery", "no_grid_charge", T0, 60)
        d = ctrl._apply_override(ModeDecision(EMSMode.GRID_CHARGING, "negative price"), 50.0, T0)
        assert d.mode is EMSMode.IDLE
        pv = ModeDecision(EMSMode.PV_CHARGING, "x")
        assert ctrl._apply_override(pv, 50.0, T0) is pv
        await ctrl.overrides.set("battery", "hold", T0, 60)
        assert ctrl._apply_override(pv, 50.0, T0).mode is EMSMode.HOLD_FOR_PEAK

    @pytest.mark.asyncio
    async def test_ending_an_override_returns_at_once(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        await ctrl.overrides.set("battery", "hold", T0, 60)
        ctrl._apply_override(ModeDecision(EMSMode.IDLE, "no action"), 50.0, T0)
        await ctrl.overrides.clear("battery")
        d = ctrl._apply_override(ModeDecision(EMSMode.IDLE, "no action"), 50.0, T0)
        assert d.mode is EMSMode.IDLE and d.urgent
        later = ModeDecision(EMSMode.IDLE, "no action")
        assert ctrl._apply_override(later, 50.0, T0) is later      # only once

    def test_no_override_changes_nothing(self, make_config, fake_ws):
        ctrl = make_controller(make_config, fake_ws)
        d = ModeDecision(EMSMode.IDLE, "no action")
        assert ctrl._apply_override(d, 50.0, T0) is d


# ----------------------------------------------------------------------
# C3
# ----------------------------------------------------------------------

def _iso(h, m=0):
    return (T0.replace(hour=h, minute=m)).isoformat()


STATUS = {
    "battery_plan": {"plan": {"steps": [
        {"start": _iso(12), "end": _iso(12, 30), "action": "auto", "soc_kwh": 4, "grid_charge_kwh": 0},
        {"start": _iso(12, 30), "end": _iso(13), "action": "auto", "soc_kwh": 4, "grid_charge_kwh": 0},
        {"start": _iso(13), "end": _iso(13, 30), "action": "charge", "soc_kwh": 5, "grid_charge_kwh": 1.0},
        {"start": _iso(13, 30), "end": _iso(14), "action": "charge", "soc_kwh": 6, "grid_charge_kwh": 1.2},
        {"start": _iso(14), "end": _iso(14, 30), "action": "hold", "soc_kwh": 6, "grid_charge_kwh": 0},
    ]}},
    "hot_water": {"plan": {"windows": [{"start": _iso(12, 45), "end": _iso(13, 15), "energy_kwh": 0.8}]}},
    "ev": {"plan": {"slots": [{"start": _iso(15), "end": _iso(15, 30), "power_w": 11000}]}},
}


class TestTimeline:
    def test_rows_merge_and_charge_row(self):
        tl = build_timeline(STATUS)
        assert [s["label"] for s in tl["rows"]["battery"]] == ["auto", "charge", "hold"]
        assert tl["rows"]["grid_charge"] == [{"start": _iso(13), "end": _iso(14), "label": "charge",
                                              "energy_kwh": 2.2}]
        assert tl["rows"]["ev"][0]["energy_kwh"] == 5.5
        assert len(tl["soc"]) == 5

    def test_next_action_is_the_earliest_change(self):
        tl = build_timeline(STATUS)
        na = next_action(tl, T0.replace(minute=5), current_battery="auto")
        assert na["device"] == "hot_water"
        assert na["text"]["de"] == "Warmwasser: 12:45–13:15 (0,8 kWh)"
        na = next_action(tl, T0.replace(hour=12, minute=50), current_battery="auto")
        assert na["device"] == "grid_charge"
        assert na["text"]["en"] == "Grid charging: 13:00–14:00 (2.2 kWh)"

    def test_nothing_planned(self):
        assert next_action(build_timeline({}), T0) is None


# ----------------------------------------------------------------------
# C4
# ----------------------------------------------------------------------

class TestFidelity:
    def test_quota_and_deviation(self):
        j = PlanJournal()
        for minute in range(10):
            actual = 185 if minute < 6 else 0
            j.record(T0 + timedelta(minutes=minute),
                     [ChannelReading("charge_current", "Charge", 185, actual, 1.0)], simulation=False)
        j.record(T0 + timedelta(minutes=10),
                 [ChannelReading("charge_current", "Charge", 185, 185, 1.0)], simulation=False)
        assert j.quota() == pytest.approx(7 / 11)
        assert len(j.deviations) == 1
        dev = j.deviations[0].as_dict()
        assert dev["minutes"] == 4 and "reports 0" in dev["explanation"]

    def test_once_per_minute(self):
        j = PlanJournal()
        r = [ChannelReading("x", "X", 1, 1)]
        j.record(T0, r, False)
        j.record(T0 + timedelta(seconds=30), r, False)
        assert j.counts["x"].minutes == 1

    def test_simulation_records_nothing(self):
        j = PlanJournal()
        j.record(T0, [ChannelReading("x", "X", 1, 0)], simulation=True)
        assert j.quota() is None and j.status(T0)["open"] == []

    def test_unconfirmed_write_is_explained(self):
        r = ChannelReading("x", "X", 5, 3, confirmed=False)
        assert not r.kept and r.explanation == "write sent, not confirmed yet"

    @pytest.mark.asyncio
    async def test_flush(self, db):
        j = PlanJournal()
        j.record(T0, [ChannelReading("x", "X", 1, 1)], False)
        await j.flush(db)
        row = await db.load_day(T0.date())
        assert row["plan_minutes"] == 1 and row["plan_kept_minutes"] == 1


# ----------------------------------------------------------------------
# C5
# ----------------------------------------------------------------------

class TestSavings:
    def test_flat_price_baseline_is_load_times_price(self):
        base, cov = baseline_cost(10.0, {h: 0.30 for h in range(24)})
        assert base == pytest.approx(3.0) and cov == 1.0

    def test_profile_weights_evening(self):
        prices = {h: (0.40 if 18 <= h < 21 else 0.30) for h in range(24)}
        base, _ = baseline_cost(10.0, prices)
        share = sum(PROFILE_WEIGHTS[18:21]) / sum(PROFILE_WEIGHTS)
        assert base == pytest.approx(10 * (0.30 + 0.10 * share))

    def test_day_needs_coverage(self):
        row = {"date": "2026-11-15", "load_total_kwh": 10, "grid_cost_eur": 1.0, "feed_in_revenue_eur": 0.2}
        assert day_saving(row, {h: 0.3 for h in range(8)}) is None
        ds = day_saving(row, {h: 0.3 for h in range(24)})
        assert ds.saving_eur == pytest.approx(3.0 - 0.8)
        assert MIN_COVERAGE <= ds.coverage

    def test_negative_saving_stays_negative(self):
        row = {"date": "2026-11-15", "load_total_kwh": 1, "grid_cost_eur": 1.0}
        ds = day_saving(row, {h: 0.3 for h in range(24)})
        out = summarise([ds], date(2026, 11, 16))
        assert out["week"]["saving_eur"] == pytest.approx(-0.7)
        assert out["month"]["days"] == 1

    @pytest.mark.asyncio
    async def test_end_to_end_with_recorded_prices(self, make_config, fake_ws, db):
        ctrl, *_ = build_controller(make_config, fake_ws)
        ctrl._init_phase_c(None, db)
        yesterday = date.today() - timedelta(days=1)
        await db.upsert_day(yesterday, {"load_total_kwh": 10.0, "grid_cost_eur": 1.5,
                                        "feed_in_revenue_eur": 0.5})
        for h in range(24):
            await db.upsert_hour(f"{yesterday.isoformat()}T{h:02d}:00:00+01:00",
                                 {"date": yesterday.isoformat(), "hour": h, "load_kwh": 0.4,
                                  "price_eur_kwh": 0.30})
        await ctrl._update_savings(datetime.now().astimezone())
        assert ctrl._savings["week"]["saving_eur"] == pytest.approx(2.0)


# ----------------------------------------------------------------------
# C7
# ----------------------------------------------------------------------

STATES = [
    {"entity_id": "sensor.pv", "state": "100", "attributes": {"unit_of_measurement": "W", "friendly_name": "PV"}},
    {"entity_id": "sensor.pv_kwh", "state": "3", "attributes": {"unit_of_measurement": "kWh"}},
    {"entity_id": "number.offset", "state": "0", "attributes": {}},
    {"entity_id": "switch.wb", "state": "off", "attributes": {}},
]


class TestEntities:
    def test_candidates_are_filtered(self):
        ids = [c["entity_id"] for c in candidates("pv_power_entity", STATES)]
        assert ids == ["sensor.pv"]
        assert [c["entity_id"] for c in candidates("heating_offset_entity", STATES)] == ["number.offset"]
        assert len(candidates("unknown_field", STATES)) == 4

    def test_validate(self):
        problems = validate({"pv_power_entity": "sensor.pv_kwh", "heating_offset_entity": "switch.wb",
                             "ev_enable_entity": "switch.missing", "load_power_entity": "",
                             "phase_current_entities": "sensor.pv"}, STATES)
        by_field = {p["field"]: p["problem"] for p in problems}
        assert by_field["pv_power_entity"].startswith("unit kWh")
        assert by_field["heating_offset_entity"].startswith("wrong domain")
        assert by_field["ev_enable_entity"] == "does not exist"
        assert by_field["phase_current_entities"].startswith("unit W")
        assert "load_power_entity" not in by_field
        assert validate({"pv_power_entity": "sensor.pv"}, STATES) == []


# ----------------------------------------------------------------------
# End to end
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_status_carries_the_phase_c_blocks(make_config, fake_ws):
    ctrl, _, inverter, _ = build_controller(make_config, fake_ws, mode_dwell_sec=300)
    fake_ws.values.update({"sensor.pv": 0.0, "sensor.load": 500.0, "sensor.grid": 500.0,
                           "sensor.soc": 50.0, "sensor.batp": 0.0, "sensor.price": 0.30})
    fake_ws.attributes["sensor.price"] = _NIEDRIG_0206_TIMESLOTS
    with freeze_time("2026-08-15 10:00:00"):
        now = datetime.now().astimezone()
        await ctrl.overrides.set("battery", "grid_charge", now, 30)
        result = await ctrl.update()
    assert result["mode"] == EMSMode.GRID_CHARGING.value          # rank 3, no dwell
    assert result["explain"]["mode"]["de"].startswith("Von Hand: Netzladen bis")
    assert result["overrides"]["battery"]["value"] == "grid_charge"
    assert result["timeline"]["background"]
    assert result["plan_inputs"]["prices_until"] is not None
    assert result["peak_price_eur"] == pytest.approx(0.3444)
    assert "plan_fidelity" in result and "alerts" in result


@pytest.mark.asyncio
async def test_ev_override_beats_the_helper(make_config, fake_ws):
    from ev_controller import EVController
    cfg = make_config(ev_charge_power_entity="sensor.wb", ev_mode_entity="input_select.ev")
    ev = EVController(cfg, fake_ws, None)
    fake_ws.state_cache["input_select.ev"] = {"state": "Stopp"}
    fake_ws.values["sensor.wb"] = 3000
    ev.override_mode = "now"
    await ev.update(T0, slots=None, grid_w=0, t=0)
    assert ev.mode.value == "now"
