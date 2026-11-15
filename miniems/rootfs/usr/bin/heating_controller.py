"""B6 – space heating, stage 1: steering through the flow-line offset.

docs/roadmap/gesamtplan.md, B6 (and the stage-2 hooks of B7).

The only actuator is the heat pump's *flow-line offset* (capability of
heat_pump_device.HeatPumpDevice, Lambda: HC register 50, −10 … +10 K).

Decision per tick, in this order:

1. Off, no offset capability, or outdoors above the heating limit → 0 K.
2. Price class (A1, with the hysteresis meant for slow actuators):
   ``low`` → +up, ``high`` → −down, ``medium`` → 0.
3. Pre-load: not ``high`` now and a ``high`` slot within PRELOAD_HORIZON →
   at least +PRELOAD_K. Less when PV is forecast for the hours ahead.
4. COP check for every raise: a higher flow temperature lowers the COP, so
   heating earlier only pays when price_now / COP_raised < price_peak / COP.
5. Protection without an indoor sensor: at most MAX_LOWER_H lowered in a
   row, then RECOVER_H normal; below −5 °C outdoors at most 1 K lowering,
   below −15 °C none.
6. Heating-rod protection: heat-pump power well above the compressor's own
   power means the rod runs – warning, lowering halved for the rest of the day.
7. Stage 2 (B7, room sensor): near the comfort minimum no lowering; below it
   for COLD_ALARM_MIN in a row → raise.
8. Compressor protection: after every change a lock of LOCK_UP_MIN (raise)
   or LOCK_DOWN_MIN (lower). The lock survives a restart (store kv_state).

The offset is written 0 K at start, at shutdown and at handover, so a crash
never leaves the heat pump detuned for long (A10 failsafe as well).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from heat_pump_device import HeatPumpDevice
from ha_service import ConfirmedWriter
from write_channel import WriteChannel, WriteSpec, matches

if TYPE_CHECKING:
    from config_loader import Config
    from ha_service import HAServiceCaller
    from ha_state_client import HAStateClient
    from heating_model import HeatingPlan
    from store import EnergyStore

_LOGGER = logging.getLogger(__name__)

PRELOAD_HORIZON = timedelta(hours=6)
PRELOAD_K = 1.0
MAX_LOWER_H = 3.0
RECOVER_H = 1.0
COLD_LIMIT_C = -5.0
COLD_MAX_LOWER_K = 1.0
FROST_LIMIT_C = -15.0
LOCK_UP_MIN = 60
LOCK_DOWN_MIN = 45
COLD_ALARM_MIN = 30
ROD_FACTOR = 1.3
ROD_MIN_SEC = 600
NEAR_COMFORT_K = 0.3
SURPLUS_WRITE_SEC = 300
_KV_KEY = "heating_offset"


@dataclass(frozen=True)
class PriceSlot:
    start: datetime
    end: datetime
    price_eur_kwh: float
    tier: str | None           # "low" | "medium" | "high" | None


@dataclass
class HeatingInputs:
    now: datetime
    price_eur_kwh: float | None
    tier: str | None
    slots: list[PriceSlot] = field(default_factory=list)   # upcoming
    outdoor_c: float | None = None
    pv_ahead_kwh: float | None = None                       # PV forecast for the next hours
    grid_w: float | None = None
    plan: "HeatingPlan | None" = None                       # B7, when in control


class HeatingController:
    def __init__(self, cfg: "Config", ws: "HAStateClient", store: "EnergyStore | None",
                 caller: "HAServiceCaller | None") -> None:
        self._cfg = cfg
        self._ws = ws
        self._store = store
        self._device = HeatPumpDevice(cfg, ws)
        self._writer = ConfirmedWriter(caller) if caller is not None else None
        self._channel = WriteChannel()
        self._surplus_channel = WriteChannel()
        self._surplus_at: datetime | None = None
        self._offset_k = 0.0                # last decided (and written) offset
        self._wanted_k = 0.0                # before the lock
        self._reason = "startup"
        self._changed_at: datetime | None = None
        self._last_dir = 0                  # +1 raise, −1 lower
        self._lowered_since: datetime | None = None
        self._recover_until: datetime | None = None
        self._cold_since: datetime | None = None
        self._rod_since: datetime | None = None
        self._rod_day: date | None = None
        self._restored = False
        self._started = False

    # ------------------------------------------------------------------

    @property
    def configured(self) -> bool:
        return self._device.has_flow_offset

    @property
    def controlling(self) -> bool:
        return self.configured and self._cfg.heating_control_enabled

    @property
    def simulation(self) -> bool:
        return self._cfg.heating_control_simulation

    @property
    def offset_k(self) -> float:
        return self._offset_k

    async def restore_lock(self) -> None:
        """Load the compressor lock from the store (survives a restart)."""
        if self._store is None or self._restored:
            return
        self._restored = True
        data = await self._store.get_state(_KV_KEY)
        if not isinstance(data, dict):
            return
        try:
            self._changed_at = datetime.fromisoformat(data["changed_at"])
            self._last_dir = int(data.get("dir", 0))
        except (KeyError, ValueError, TypeError):
            self._changed_at = None

    # ------------------------------------------------------------------
    # Tick
    # ------------------------------------------------------------------

    async def update(self, inp: HeatingInputs) -> None:
        if not self.configured:
            self._reason = "no flow-offset entity configured"
            return
        await self.restore_lock()
        now = inp.now
        if not self._started:
            # Start: 0 K first, whatever the last run left behind.
            self._started = True
            self._offset_k = 0.0
            self._reason = "startup: offset 0 K"
            if self.controlling:
                await self._write(0.0)
            return

        wanted, reason = self._decide(inp)
        self._wanted_k = wanted
        if wanted != self._offset_k:
            locked, until = self._locked(now)
            if locked:
                reason = f"{reason} – compressor lock until {until:%H:%M}"
            else:
                direction = 1 if wanted > self._offset_k else -1
                self._offset_k = wanted
                self._changed_at = now
                self._last_dir = direction
                if self._store is not None:
                    await self._store.set_state(_KV_KEY, {
                        "changed_at": now.isoformat(), "dir": direction, "offset_k": wanted})
        self._track_lowering(now)
        self._reason = reason
        if self.controlling:
            await self._write(self._offset_k)
            await self._write_surplus(now, inp.grid_w)

    def _decide(self, inp: HeatingInputs) -> tuple[float, str]:
        cfg = self._cfg
        now = inp.now
        if inp.outdoor_c is not None and inp.outdoor_c >= cfg.heating_limit_temp_c:
            return 0.0, f"outdoor {inp.outdoor_c:.1f} °C at or above the heating limit"
        if inp.tier is None or inp.price_eur_kwh is None:
            return 0.0, "no price class"

        up, down = cfg.heating_offset_up_k, cfg.heating_offset_down_k
        offset, reason = 0.0, f"price {inp.tier}"
        if inp.plan is not None and inp.plan.ready:
            planned = inp.plan.offset_at(now)
            if planned is not None:
                offset, reason = planned, f"heating plan: {inp.plan.reason}"
        elif inp.tier == "low":
            offset = up
        elif inp.tier == "high":
            offset = -down

        upcoming_high = [s for s in inp.slots
                         if s.tier == "high" and now < s.start <= now + PRELOAD_HORIZON]
        if inp.tier != "high" and upcoming_high and offset < PRELOAD_K:
            offset = PRELOAD_K
            reason = f"pre-load before high price at {upcoming_high[0].start:%H:%M}"
        if (offset > 0 and inp.pv_ahead_kwh is not None
                and inp.pv_ahead_kwh >= cfg.heating_sun_kwh):
            offset *= 0.5
            reason += f" (halved: {inp.pv_ahead_kwh:.0f} kWh PV ahead)"

        if offset > 0:
            peak = max((s.price_eur_kwh for s in inp.slots
                        if now < s.start <= now + PRELOAD_HORIZON), default=None)
            if peak is None or not self._raise_pays(inp.price_eur_kwh, peak, offset):
                return 0.0, (f"{reason} – not worth it: higher flow temperature costs COP "
                             f"(now {inp.price_eur_kwh * 100:.1f} ct, peak "
                             f"{(peak or 0) * 100:.1f} ct)")

        if offset < 0:
            offset, why = self._limit_lowering(now, offset, inp.outdoor_c)
            if why:
                reason += f" – {why}"

        room = self._device.room_temp_c()
        if room is not None and cfg.heating_comfort_min_c > 0:
            if room < cfg.heating_comfort_min_c:
                self._cold_since = self._cold_since or now
                if now - self._cold_since >= timedelta(minutes=COLD_ALARM_MIN):
                    return up, f"room {room:.1f} °C below {cfg.heating_comfort_min_c:.1f} °C for 30 min"
            else:
                self._cold_since = None
            if offset < 0 and room < cfg.heating_comfort_min_c + NEAR_COMFORT_K:
                return 0.0, f"room {room:.1f} °C near the comfort minimum – no lowering"
        return round(offset, 1), reason

    def _raise_pays(self, price_now: float, price_peak: float, offset_k: float) -> bool:
        """price_now / COP_raised < price_peak / COP_normal."""
        drop = self._cfg.heating_cop_drop_pct_per_k / 100.0 * offset_k
        cop_ratio = max(0.1, 1.0 - drop)          # COP_raised / COP_normal
        return price_now / cop_ratio < price_peak

    def _limit_lowering(self, now: datetime, offset: float,
                        outdoor: float | None) -> tuple[float, str]:
        why = []
        if outdoor is not None and outdoor < FROST_LIMIT_C:
            return 0.0, f"outdoor {outdoor:.0f} °C below {FROST_LIMIT_C:.0f} °C – no lowering"
        if outdoor is not None and outdoor < COLD_LIMIT_C and offset < -COLD_MAX_LOWER_K:
            offset = -COLD_MAX_LOWER_K
            why.append(f"outdoor below {COLD_LIMIT_C:.0f} °C – at most 1 K")
        if self._recover_until is not None and now < self._recover_until:
            return 0.0, f"recovering after {MAX_LOWER_H:.0f} h lowered"
        if (self._lowered_since is not None
                and now - self._lowered_since >= timedelta(hours=MAX_LOWER_H)):
            self._recover_until = now + timedelta(hours=RECOVER_H)
            self._lowered_since = None
            return 0.0, f"{MAX_LOWER_H:.0f} h lowered – {RECOVER_H:.0f} h normal"
        if self._rod_day == now.date():
            offset *= 0.5
            why.append("heating rod seen today – lowering halved")
        return offset, "; ".join(why)

    def _track_lowering(self, now: datetime) -> None:
        if self._offset_k < 0:
            self._lowered_since = self._lowered_since or now
        else:
            self._lowered_since = None
        power = self._device.power_w()
        rod_w = self._cfg.heating_compressor_max_w
        if power is not None and rod_w > 0 and power > ROD_FACTOR * rod_w:
            self._rod_since = self._rod_since or now
            if (now - self._rod_since).total_seconds() >= ROD_MIN_SEC and self._rod_day != now.date():
                self._rod_day = now.date()
                _LOGGER.warning("Heating: heat pump draws %.0f W, well above the compressor's "
                                "%.0f W – heating rod? Lowering halved for today", power, rod_w)
        else:
            self._rod_since = None

    def _locked(self, now: datetime) -> tuple[bool, datetime | None]:
        if self._changed_at is None:
            return False, None
        minutes = LOCK_UP_MIN if self._last_dir > 0 else LOCK_DOWN_MIN
        until = self._changed_at + timedelta(minutes=minutes)
        return now < until, until

    # ------------------------------------------------------------------
    # Writes
    # ------------------------------------------------------------------

    async def _write(self, offset_k: float) -> None:
        call = self._device.flow_offset_call(offset_k)
        if call is None or self._writer is None:
            return
        entity = call.data["entity_id"]
        spec = WriteSpec("number", "set_value", entity, call.data, round(offset_k, 1), tolerance=0.05)
        await self._writer.write(self._channel, spec,
                                 matches(self._device.flow_offset_now(), spec),
                                 self.simulation, f"Flow offset ({entity})")

    async def _write_surplus(self, now: datetime, grid_w: float | None) -> None:
        """Report the PV surplus to the heat pump ourselves (Lambda register 102)."""
        if not self._cfg.heating_surplus_entity or self._writer is None or grid_w is None:
            return
        if self._surplus_at is not None and (now - self._surplus_at).total_seconds() < SURPLUS_WRITE_SEC:
            return
        self._surplus_at = now
        value = round(max(0.0, -grid_w) / 100.0) * 100
        call = self._device.surplus_call(value)
        if call is None:
            return
        entity = call.data["entity_id"]
        spec = WriteSpec("number", "set_value", entity, call.data, value, tolerance=50)
        await self._writer.write(self._surplus_channel, spec,
                                 matches(self._ws.get_state_value(entity), spec),
                                 self.simulation, f"PV surplus to heat pump ({entity})")

    def failsafe_actions(self) -> list[dict[str, Any]]:
        if not self.controlling or self.simulation:
            return []
        actions = []
        call = self._device.flow_offset_call(0.0)
        if call is not None:
            actions.append({"service": call.name, "data": call.data})
        surplus = self._device.surplus_call(0)
        if surplus is not None:
            actions.append({"service": surplus.name, "data": surplus.data})
        return actions

    async def restore_safe_defaults(self) -> None:
        if not self.controlling:
            return
        _LOGGER.info("Heating: flow offset back to 0 K")
        self._channel = WriteChannel()
        self._offset_k = 0.0
        await self._write(0.0)
        if self._cfg.heating_surplus_entity:
            self._surplus_channel = WriteChannel()
            self._surplus_at = None
            await self._write_surplus(datetime.now(timezone.utc), 0.0)

    def reset_write_state(self) -> None:
        self._channel = WriteChannel()
        self._surplus_channel = WriteChannel()

    def pop_write_events(self) -> list[dict[str, Any]]:
        return self._writer.pop_events() if self._writer is not None else []

    def status(self, now: datetime) -> dict[str, Any]:
        c = self._cfg
        locked, until = self._locked(now)
        return {
            "control": ("off" if not c.heating_control_enabled
                        else "simulation" if c.heating_control_simulation else "live"),
            "offset_k": self._offset_k,
            "wanted_k": self._wanted_k,
            "reason": self._reason,
            "locked_until": until.isoformat() if locked and until else None,
            "room_temp_c": self._device.room_temp_c(),
            "lowered_since": self._lowered_since.isoformat() if self._lowered_since else None,
            "heating_rod_today": self._rod_day == now.date(),
            "confirmed": self._channel.confirmed,
            "missing": self._device.missing("flow_offset", "room_temp", "power"),
        }
