"""heat_pump_recorder.py: COP filter, hourly aggregation, standby loss and draws."""
from datetime import datetime, timedelta, timezone

import pytest

from heat_pump_recorder import UNKNOWN_MODE, CopFilter, HeatPumpRecorder

T0 = datetime(2026, 10, 29, 10, 0, tzinfo=timezone(timedelta(hours=1)))
INTERVAL = 30


class FakeStore:
    def __init__(self) -> None:
        self.hours: dict[tuple[str, int], dict] = {}
        self.segments: list[dict] = []
        self.draws: list[tuple] = []

    async def upsert_hp_hour(self, hour_start, mode, fields):
        row = self.hours.setdefault((hour_start, mode), {"hour_start": hour_start, "mode": mode})
        row.update(fields)

    async def query_hp_hours_since(self, since_date):
        return [dict(r) for r in self.hours.values()]

    async def insert_loss_segment(self, fields):
        self.segments.append(fields)

    async def insert_draw(self, ts, before, after):
        self.draws.append((ts, before, after))


@pytest.fixture
def cfg(make_config):
    return make_config(
        heat_pump_cop_entity="sensor.cop",
        heat_pump_mode_entity="sensor.mode",
        heat_pump_request_type_entity="sensor.req",
        boil_temp_entity="sensor.boil",
        hp_cop_settle_sec=300,
        hw_draw_drop_k=1.5,
    )


def at(sec: float) -> datetime:
    return T0 + timedelta(seconds=sec)


async def tick(rec, sec, cop=0.0, mode=0, req=0, boil=50.0, outdoor=5.0, hp_w=None):
    await rec.record_tick(at(sec), cop, mode, req, boil, outdoor, hp_w)


class TestCopFilter:
    def test_settle_phase_discarded_then_passed(self):
        f = CopFilter(300)
        assert f.filter(at(0), 1.2, 2) is None       # compressor start transient
        assert f.filter(at(299), 3.0, 2) is None
        assert f.filter(at(300), 3.0, 2) == 3.0

    def test_mode_change_restarts_settle(self):
        f = CopFilter(300)
        f.filter(at(0), 3.0, 1)
        assert f.filter(at(400), 3.0, 1) == 3.0
        assert f.filter(at(410), 2.5, 2) is None

    def test_zero_ends_run(self):
        f = CopFilter(300)
        f.filter(at(0), 3.0, 1)
        f.filter(at(400), 0.0, 1)
        assert f.filter(at(410), 3.0, 1) is None

    def test_out_of_band_dropped(self):
        f = CopFilter(0)
        assert f.filter(at(0), 15.0, 1) is None
        assert f.filter(at(1), 0.2, 1) is None


class TestHourly:
    async def test_cop_average_excludes_settle_and_splits_by_mode(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(20):   # 10 min hot water: first 5 min settle
            await tick(rec, i * INTERVAL, cop=2.0 if i < 10 else 3.0, mode=2, req=4)
        row = store.hours[(T0.isoformat(), 2)]
        assert row["ticks"] == 20
        assert row["cop_ticks"] == 10
        assert row["cop_avg"] == 3.0
        await tick(rec, 20 * INTERVAL, cop=0.0, mode=0)
        assert store.hours[(T0.isoformat(), 0)]["cop_avg"] is None

    async def test_unknown_mode_bucket(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        await rec.record_tick(at(0), 0.0, None, None, 50.0, 5.0, None)
        assert (T0.isoformat(), UNKNOWN_MODE) in store.hours

    async def test_seed_continues_hour_after_restart(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(4):
            await tick(rec, i * INTERVAL, boil=50.0)
        rec2 = HeatPumpRecorder(store, cfg, INTERVAL)   # restart within the hour
        await tick(rec2, 10 * INTERVAL, boil=48.0)
        row = store.hours[(T0.isoformat(), 0)]
        assert row["ticks"] == 5
        assert row["boil_temp_c"] == pytest.approx((4 * 50.0 + 48.0) / 5)

    async def test_heat_pump_kwh(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(120):   # one hour at 2 kW
            await tick(rec, i * INTERVAL, hp_w=2000.0)
        assert store.hours[(T0.isoformat(), 0)]["heat_pump_kwh"] == pytest.approx(2.0)


class TestLossSegments:
    async def test_segment_scored_when_hot_water_starts(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        n = 240   # 2 h standby, 50 -> 49
        for i in range(n + 1):
            await tick(rec, i * INTERVAL, boil=50.0 - i / n)
        await tick(rec, (n + 1) * INTERVAL, cop=2.0, mode=2, boil=49.0)
        assert len(store.segments) == 1
        seg = store.segments[0]
        assert seg["end_reason"] == "heating"
        assert seg["duration_h"] == pytest.approx(2.0)
        assert seg["k_per_h"] == pytest.approx(0.5)

    async def test_request_type_also_counts_as_heating(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(241):
            await tick(rec, i * INTERVAL, boil=50.0 - i / 240)
        await tick(rec, 241 * INTERVAL, mode=0, req=4, boil=49.0)
        assert [s["end_reason"] for s in store.segments] == ["heating"]

    async def test_short_segment_discarded(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(60):   # 30 min only
            await tick(rec, i * INTERVAL, boil=50.0)
        await tick(rec, 60 * INTERVAL, mode=2, boil=50.0)
        assert store.segments == []

    async def test_gap_discards_segment(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(200):
            await tick(rec, i * INTERVAL, boil=50.0)
        # 10 min without a valid reading, then standby resumes and heating starts
        for i in range(220, 350):
            await tick(rec, i * INTERVAL, boil=49.5)
        await tick(rec, 350 * INTERVAL, mode=2, boil=49.5)
        assert len(store.segments) == 1
        assert store.segments[0]["start_ts"] == at(220 * INTERVAL).isoformat()

    async def test_draw_ends_segment_and_is_logged(self, cfg):
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(241):   # 2 h slow loss 50 -> 49
            await tick(rec, i * INTERVAL, boil=50.0 - i / 240)
        t = 241 * INTERVAL
        for temp in (48.0, 47.0, 46.0, 46.0):   # shower
            await tick(rec, t, boil=temp)
            t += INTERVAL
        assert [s["end_reason"] for s in store.segments] == ["draw"]
        assert store.segments[0]["k_per_h"] == pytest.approx(0.5)
        # draw stays open until no new low for the draw window
        for _ in range(25):
            await tick(rec, t, boil=46.0)
            t += INTERVAL
        assert len(store.draws) == 1
        _, before, after = store.draws[0]
        # highest reading within the draw window before the drop
        assert before == pytest.approx(49.08, abs=0.01)
        assert after == 46.0

    async def test_draw_is_shown_and_reported_before_it_is_stored(self, cfg):
        rec = HeatPumpRecorder(FakeStore(), cfg, INTERVAL)
        for i in range(10):
            await tick(rec, i * INTERVAL, boil=50.0)
        t = 10 * INTERVAL
        for temp in (49.0, 48.0):                       # drop of 2 K → detected
            await tick(rec, t, boil=temp)
            t += INTERVAL
        active = rec.status()["draw_active"]
        assert active is not None and active["drop_k"] == pytest.approx(2.0)
        events = rec.pop_events()
        assert [e["type"] for e in events] == ["draw_start"]
        await tick(rec, t, boil=47.0)                   # still falling: live value follows
        assert rec.status()["draw_active"]["temp_now_c"] == 47.0
        for _ in range(25):                             # no new low for the window → stored
            t += INTERVAL
            await tick(rec, t, boil=47.0)
        assert rec.status()["draw_active"] is None
        end = rec.pop_events()
        assert [e["type"] for e in end] == ["draw_end"]
        assert end[0]["temp_before_c"] == 50.0 and end[0]["temp_after_c"] == 47.0

    async def test_no_mode_sensors_uses_cop_as_heating(self, make_config):
        cfg = make_config(heat_pump_cop_entity="sensor.cop", boil_temp_entity="sensor.boil")
        store = FakeStore()
        rec = HeatPumpRecorder(store, cfg, INTERVAL)
        for i in range(241):
            await rec.record_tick(at(i * INTERVAL), 0.0, None, None, 50.0 - i / 240, None, None)
        await rec.record_tick(at(241 * INTERVAL), 3.0, None, None, 49.0, None, None)
        assert [s["end_reason"] for s in store.segments] == ["heating"]


async def test_status_and_enabled(cfg, make_config):
    rec = HeatPumpRecorder(None, cfg, INTERVAL)
    assert rec.enabled
    await tick(rec, 0, cop=2.5, mode=2, req=4, boil=48.0)
    st = rec.status()
    assert st["cop_raw"] == 2.5
    assert st["cop_filtered"] is None
    assert st["heating_hot_water"] is True
    assert not HeatPumpRecorder(None, make_config(), INTERVAL).enabled
