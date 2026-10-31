"""Heat pump / hot water history (docs/roadmap/v3.4-warmwasser.md, Schritt 1).

Data collection only – nothing here controls the heat pump. Three outputs:

* heat_pump_hourly: per local hour and raw operating-mode code, the average
  of the *filtered* COP plus outdoor temperature, boiler temperature and the
  heat pump's electrical energy. Basis for learning COP(outdoor, boil, mode).
* hot_water_loss_segments: standby periods (boiler not being heated) scored
  as K/h over the whole segment – per-tick deltas are useless at a typical
  0.5 K sensor resolution.
* hot_water_draws: fast drops (tapping), kept separate so they neither
  distort the loss rate nor get lost – they are the demand profile later.
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any

from const import (
    HP_COP_MAX,
    HP_COP_MIN,
    HW_DRAW_WINDOW_SEC,
    HW_GAP_FACTOR,
    HW_LOSS_MIN_SEGMENT_H,
)

if TYPE_CHECKING:
    from config_loader import Config
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

UNKNOWN_MODE = -1


class CopFilter:
    """Drops start-up transients and implausible COP readings.

    A run starts when COP goes from 0/unavailable to > 0, or when the
    operating mode changes while running; the first `settle_sec` of every
    run are discarded.
    """

    def __init__(self, settle_sec: int) -> None:
        self._settle_sec = settle_sec
        self._run_start: datetime | None = None
        self._mode: int | None = None

    def filter(self, now: datetime, cop: float | None, mode: int | None) -> float | None:
        if cop is None or cop <= 0:
            self._run_start = None
            return None
        if self._run_start is None or mode != self._mode:
            self._run_start = now
            self._mode = mode
        if (now - self._run_start).total_seconds() < self._settle_sec:
            return None
        if not HP_COP_MIN <= cop <= HP_COP_MAX:
            return None
        return cop


@dataclass
class _HourAcc:
    ticks: int = 0
    cop_sum: float = 0.0
    cop_ticks: int = 0
    outdoor_sum: float = 0.0
    outdoor_ticks: int = 0
    boil_sum: float = 0.0
    boil_ticks: int = 0
    hp_wh: float = 0.0
    hp_ticks: int = 0
    seeded: bool = False


@dataclass
class _Segment:
    start_ts: datetime
    start_temp: float
    last_ts: datetime | None = None
    last_temp: float | None = None
    boil_sum: float = 0.0
    boil_n: int = 0
    outdoor_sum: float = 0.0
    outdoor_n: int = 0
    # (ts, temp) of recent standby ticks, for draw detection.
    recent: deque = field(default_factory=deque)


@dataclass
class _Draw:
    ts: datetime
    temp_before: float
    temp_after: float
    last_drop_ts: datetime


class HeatPumpRecorder:
    def __init__(self, store: "EnergyStore | None", cfg: "Config", interval_sec: int) -> None:
        self._store = store
        self._cfg = cfg
        self._interval = interval_sec
        self._cop_filter = CopFilter(cfg.hp_cop_settle_sec)
        self._hour: datetime | None = None
        self._acc: dict[int, _HourAcc] = {}
        self._segment: _Segment | None = None
        self._draw: _Draw | None = None
        self._last_valid_ts: datetime | None = None
        self._status: dict[str, Any] = {}
        # Draw start/end events since the last pop_events() – the EMS controller
        # turns them into event-log entries.
        self._events: list[dict[str, Any]] = []

    @property
    def enabled(self) -> bool:
        return bool(self._cfg.heat_pump_cop_entity or self._cfg.boil_temp_entity)

    def pop_events(self) -> list[dict[str, Any]]:
        events, self._events = self._events, []
        return events

    def status(self) -> dict[str, Any]:
        return dict(self._status)

    async def record_tick(
        self,
        now: datetime,
        cop: float | None,
        mode: float | None,
        request_type: float | None,
        boil_temp_c: float | None,
        outdoor_temp_c: float | None,
        heat_pump_w: float | None,
    ) -> None:
        mode_i = int(round(mode)) if mode is not None else None
        req_i = int(round(request_type)) if request_type is not None else None
        cop_f = self._cop_filter.filter(now, cop, mode_i)
        heating = self._is_heating(mode_i, req_i, cop)

        await self._record_hour(now, mode_i, cop_f, outdoor_temp_c, boil_temp_c, heat_pump_w)
        await self._track_loss(now, heating, boil_temp_c, outdoor_temp_c)

        seg = self._segment
        self._status = {
            "cop_raw": cop,
            "cop_filtered": cop_f,
            "mode": mode_i,
            "request_type": req_i,
            "boil_temp_c": boil_temp_c,
            "heating_hot_water": heating,
            "segment_age_h": (
                round((now - seg.start_ts).total_seconds() / 3600.0, 2) if seg else None
            ),
            "segment_k_per_h": self._segment_rate(seg, now, boil_temp_c),
            # A draw in progress – shown at once, stored only when it has ended.
            "draw_active": {
                "since": self._draw.ts.isoformat(),
                "temp_before_c": self._draw.temp_before,
                "temp_now_c": self._draw.temp_after,
                "drop_k": round(self._draw.temp_before - self._draw.temp_after, 1),
            } if self._draw is not None else None,
        }

    # ------------------------------------------------------------------
    # Hourly aggregation
    # ------------------------------------------------------------------

    async def _record_hour(
        self,
        now: datetime,
        mode: int | None,
        cop: float | None,
        outdoor_temp_c: float | None,
        boil_temp_c: float | None,
        heat_pump_w: float | None,
    ) -> None:
        hour = now.replace(minute=0, second=0, microsecond=0)
        if hour != self._hour:
            self._hour = hour
            self._acc = {}
        key = mode if mode is not None else UNKNOWN_MODE
        acc = self._acc.setdefault(key, _HourAcc())
        if not acc.seeded:
            await self._seed(acc, key)
            acc.seeded = True

        acc.ticks += 1
        if cop is not None:
            acc.cop_sum += cop
            acc.cop_ticks += 1
        if outdoor_temp_c is not None:
            acc.outdoor_sum += outdoor_temp_c
            acc.outdoor_ticks += 1
        if boil_temp_c is not None:
            acc.boil_sum += boil_temp_c
            acc.boil_ticks += 1
        if heat_pump_w is not None:
            acc.hp_wh += max(0.0, heat_pump_w) * self._interval / 3600.0
            acc.hp_ticks += 1

        if self._store is None:
            return
        await self._store.upsert_hp_hour(hour.isoformat(), key, {
            "date": str(hour.date()),
            "hour": hour.hour,
            "ticks": acc.ticks,
            "cop_ticks": acc.cop_ticks,
            "cop_avg": round(acc.cop_sum / acc.cop_ticks, 3) if acc.cop_ticks else None,
            "outdoor_temp_c": (
                round(acc.outdoor_sum / acc.outdoor_ticks, 2) if acc.outdoor_ticks else None
            ),
            "boil_temp_c": round(acc.boil_sum / acc.boil_ticks, 2) if acc.boil_ticks else None,
            "heat_pump_kwh": (
                round(acc.hp_wh / 1000.0 * acc.ticks / acc.hp_ticks, 5) if acc.hp_ticks else None
            ),
        })

    async def _seed(self, acc: _HourAcc, mode: int) -> None:
        """Continue an (hour, mode) row recorded before a restart."""
        if self._store is None or self._hour is None:
            return
        key = self._hour.isoformat()
        rows = await self._store.query_hp_hours_since(self._hour.date())
        row = next((r for r in rows if r["hour_start"] == key and r["mode"] == mode), None)
        if not row or not row.get("ticks"):
            return
        ticks = int(row["ticks"])
        acc.ticks = ticks
        if row.get("cop_avg") is not None and row.get("cop_ticks"):
            acc.cop_ticks = int(row["cop_ticks"])
            acc.cop_sum = float(row["cop_avg"]) * acc.cop_ticks
        if row.get("outdoor_temp_c") is not None:
            acc.outdoor_sum = float(row["outdoor_temp_c"]) * ticks
            acc.outdoor_ticks = ticks
        if row.get("boil_temp_c") is not None:
            acc.boil_sum = float(row["boil_temp_c"]) * ticks
            acc.boil_ticks = ticks
        if row.get("heat_pump_kwh") is not None:
            acc.hp_wh = float(row["heat_pump_kwh"]) * 1000.0
            acc.hp_ticks = ticks

    # ------------------------------------------------------------------
    # Standby loss and draws
    # ------------------------------------------------------------------

    def _is_heating(self, mode: int | None, req: int | None, cop: float | None) -> bool | None:
        """True = boiler is being heated, False = standby, None = unknown."""
        cfg = self._cfg
        have_mode = bool(cfg.heat_pump_mode_entity)
        have_req = bool(cfg.heat_pump_request_type_entity)
        if not have_mode and not have_req:
            # No mode information: any compressor run might be hot water.
            return None if cop is None else cop > 0
        if (have_mode and mode is None) or (have_req and req is None):
            return None
        return (have_mode and mode == cfg.hp_mode_hot_water_code) or (
            have_req and req == cfg.hp_request_hot_water_code
        )

    async def _track_loss(
        self,
        now: datetime,
        heating: bool | None,
        boil: float | None,
        outdoor: float | None,
    ) -> None:
        if boil is None or heating is None:
            return   # unknown tick; a long run of these ends the segment as a gap

        if self._last_valid_ts is not None and (
            (now - self._last_valid_ts).total_seconds() > HW_GAP_FACTOR * self._interval
        ):
            self._segment = None
            self._draw = None
        self._last_valid_ts = now

        if heating:
            await self._close_draw()
            await self._close_segment("heating")
            return

        if self._draw is not None:
            await self._continue_draw(now, boil)
            if self._draw is not None:
                return

        if self._segment is None:
            self._segment = _Segment(start_ts=now, start_temp=boil)
        seg = self._segment

        seg.recent.append((now, boil))
        while seg.recent and (now - seg.recent[0][0]).total_seconds() > HW_DRAW_WINDOW_SEC:
            seg.recent.popleft()
        peak_ts, peak_temp = max(seg.recent, key=lambda p: p[1])
        if peak_temp - boil >= self._cfg.hw_draw_drop_k:
            self._draw = _Draw(ts=peak_ts, temp_before=peak_temp, temp_after=boil,
                               last_drop_ts=now)
            self._events.append({"type": "draw_start", "ts": peak_ts, "temp_before_c": peak_temp,
                                 "temp_now_c": boil})
            seg.last_ts, seg.last_temp = peak_ts, peak_temp
            await self._close_segment("draw")
            return

        seg.last_ts, seg.last_temp = now, boil
        seg.boil_sum += boil
        seg.boil_n += 1
        if outdoor is not None:
            seg.outdoor_sum += outdoor
            seg.outdoor_n += 1

    async def _continue_draw(self, now: datetime, boil: float) -> None:
        draw = self._draw
        if boil < draw.temp_after:
            draw.temp_after = boil
            draw.last_drop_ts = now
        elif (now - draw.last_drop_ts).total_seconds() > HW_DRAW_WINDOW_SEC:
            await self._close_draw()

    async def _close_draw(self) -> None:
        draw, self._draw = self._draw, None
        if draw is None:
            return
        self._events.append({"type": "draw_end", "ts": draw.ts, "temp_before_c": draw.temp_before,
                             "temp_after_c": draw.temp_after, "last_drop_ts": draw.last_drop_ts})
        if self._store is None:
            return
        await self._store.insert_draw(draw.ts.isoformat(), draw.temp_before, draw.temp_after)

    async def _close_segment(self, reason: str) -> None:
        seg, self._segment = self._segment, None
        if seg is None or seg.last_ts is None:
            return
        end_ts, end_temp = seg.last_ts, seg.last_temp
        duration_h = (end_ts - seg.start_ts).total_seconds() / 3600.0
        if duration_h < HW_LOSS_MIN_SEGMENT_H or end_temp > seg.start_temp:
            return   # too short to say anything, or warmed by something else
        if self._store is None:
            return
        await self._store.insert_loss_segment({
            "start_ts": seg.start_ts.isoformat(),
            "end_ts": end_ts.isoformat(),
            "duration_h": round(duration_h, 3),
            "start_temp_c": seg.start_temp,
            "end_temp_c": end_temp,
            "k_per_h": round((seg.start_temp - end_temp) / duration_h, 4),
            "avg_boil_temp_c": round(seg.boil_sum / seg.boil_n, 2) if seg.boil_n else None,
            "avg_outdoor_temp_c": (
                round(seg.outdoor_sum / seg.outdoor_n, 2) if seg.outdoor_n else None
            ),
            "end_reason": reason,
        })

    @staticmethod
    def _segment_rate(seg: _Segment | None, now: datetime, boil: float | None) -> float | None:
        if seg is None or boil is None:
            return None
        hours = (now - seg.start_ts).total_seconds() / 3600.0
        if hours < HW_LOSS_MIN_SEGMENT_H:
            return None
        return round((seg.start_temp - boil) / hours, 3)
