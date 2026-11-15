"""B7 – space heating, stage 2: room model and pre-heat planner.

docs/roadmap/gesamtplan.md, B7.

1. **Record** per hour: mean room temperature, the share of the hour the
   heat pump heated, mean flow-line offset (hourly_stats columns
   room_temp_c, heating_frac, flow_offset_k; outdoor and PV already exist).
2. **Learn** two linear rates (K/h) from consecutive hours, plain least
   squares, no library:
       cooling (heating_frac ≤ 0.1):  −ΔT = a · (T_in − T_out) − b · PV_3h
       heating (heating_frac ≥ 0.8):   ΔT = c + d · T_out
   Valid only with MIN_COOL_HOURS / MIN_HEAT_HOURS and physical signs
   (a > 0, b ≥ 0, heating rate > 0 at the design temperature).
3. **Plan** greedily, like the hot-water planner: simulate the room with the
   heating lowered in every ``high`` slot; at the first violation of the
   comfort minimum, heat in the cheapest slot (effective price ÷ COP) of
   the PREHEAT_WINDOW before it; simulate again. Lowering slots that cannot
   be kept are turned back to 0.
4. **Look back** before going live: the model predicts every recorded hour
   it was not fitted on (alternating hours); mean absolute error in K/h.
   Only a model below MAX_BACKTEST_MAE_K_H may decide.

Pure functions plus a tiny hour accumulator; no Home Assistant access.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta

MIN_COOL_HOURS = 48
MIN_HEAT_HOURS = 24
MAX_BACKTEST_MAE_K_H = 0.3
PREHEAT_WINDOW = timedelta(hours=6)
COOL_FRAC_MAX = 0.1
HEAT_FRAC_MIN = 0.8
MAX_ITERATIONS = 48


# ----------------------------------------------------------------------
# Recording
# ----------------------------------------------------------------------

class HourAccumulator:
    """Means over one local clock hour. Flushed when the hour changes."""

    def __init__(self) -> None:
        self.hour: datetime | None = None
        self._n = 0
        self._room = 0.0
        self._room_n = 0
        self._heat = 0
        self._offset = 0.0

    def add(self, now: datetime, room_c: float | None, heating: bool,
            offset_k: float) -> dict | None:
        """Add a tick. Returns the finished previous hour's row, if any."""
        hour = now.replace(minute=0, second=0, microsecond=0)
        done = None
        if self.hour is not None and hour != self.hour:
            done = self.row()
            self.__init__()
        self.hour = hour
        self._n += 1
        if room_c is not None:
            self._room += room_c
            self._room_n += 1
        self._heat += 1 if heating else 0
        self._offset += offset_k
        return done

    def row(self) -> dict | None:
        if self.hour is None or self._n == 0:
            return None
        return {
            "hour_start": self.hour.isoformat(),
            "date": self.hour.date().isoformat(),
            "hour": self.hour.hour,
            "room_temp_c": round(self._room / self._room_n, 2) if self._room_n else None,
            "heating_frac": round(self._heat / self._n, 3),
            "flow_offset_k": round(self._offset / self._n, 2),
        }


# ----------------------------------------------------------------------
# Model
# ----------------------------------------------------------------------

@dataclass
class RoomModel:
    a: float = 0.0          # cooling per K of indoor-outdoor difference (1/h)
    b: float = 0.0          # sun gain per kWh PV of the last 3 h (K/h/kWh)
    c: float = 0.0          # heating rate at 0 °C outdoors (K/h)
    d: float = 0.0          # change of the heating rate per K outdoors
    cool_hours: int = 0
    heat_hours: int = 0
    backtest_mae_k_h: float | None = None
    reason: str = "not fitted"

    @property
    def ready(self) -> bool:
        return (self.cool_hours >= MIN_COOL_HOURS and self.heat_hours >= MIN_HEAT_HOURS
                and self.a > 0 and self.b >= 0
                and self.backtest_mae_k_h is not None
                and self.backtest_mae_k_h <= MAX_BACKTEST_MAE_K_H)

    def cooling_k_h(self, t_in: float, t_out: float, pv_3h_kwh: float = 0.0) -> float:
        """Temperature loss per hour without heating (≥ 0 while colder outside)."""
        return self.a * (t_in - t_out) - self.b * pv_3h_kwh

    def heating_k_h(self, t_out: float) -> float:
        return max(0.0, self.c + self.d * t_out)

    def as_dict(self) -> dict:
        return {
            "a": round(self.a, 4), "b": round(self.b, 4), "c": round(self.c, 3),
            "d": round(self.d, 4), "cool_hours": self.cool_hours, "heat_hours": self.heat_hours,
            "backtest_mae_k_h": (None if self.backtest_mae_k_h is None
                                 else round(self.backtest_mae_k_h, 3)),
            "ready": self.ready, "reason": self.reason,
        }


def _lstsq2(xs1: list[float], xs2: list[float], ys: list[float],
            intercept: bool) -> tuple[float, float] | None:
    """y ≈ p·x1 + q·x2 (no intercept) or y ≈ p + q·x2 (intercept, x1 ignored)."""
    n = len(ys)
    if n < 2:
        return None
    if intercept:
        mx = sum(xs2) / n
        my = sum(ys) / n
        sxx = sum((x - mx) ** 2 for x in xs2)
        if sxx <= 1e-9:
            return my, 0.0
        sxy = sum((x - mx) * (y - my) for x, y in zip(xs2, ys))
        q = sxy / sxx
        return my - q * mx, q
    s11 = sum(x * x for x in xs1)
    s22 = sum(x * x for x in xs2)
    s12 = sum(x1 * x2 for x1, x2 in zip(xs1, xs2))
    s1y = sum(x * y for x, y in zip(xs1, ys))
    s2y = sum(x * y for x, y in zip(xs2, ys))
    det = s11 * s22 - s12 * s12
    if abs(det) <= 1e-9:
        if s11 <= 1e-9:
            return None
        return s1y / s11, 0.0
    return (s1y * s22 - s2y * s12) / det, (s2y * s11 - s1y * s12) / det


def _samples(rows: list[dict]) -> tuple[list[tuple], list[tuple]]:
    """(cooling samples, heating samples) from consecutive hourly rows."""
    cool, heat = [], []
    by_start = {r["hour_start"]: r for r in rows}
    ordered = sorted(rows, key=lambda r: r["hour_start"])
    for i, r in enumerate(ordered[:-1]):
        nxt = ordered[i + 1]
        try:
            t0 = datetime.fromisoformat(r["hour_start"])
            t1 = datetime.fromisoformat(nxt["hour_start"])
        except (KeyError, ValueError, TypeError):
            continue
        if t1 - t0 != timedelta(hours=1):
            continue
        tin0, tin1 = r.get("room_temp_c"), nxt.get("room_temp_c")
        tout = r.get("outdoor_temp_c")
        frac = r.get("heating_frac")
        if None in (tin0, tin1, tout, frac):
            continue
        pv3 = 0.0
        for k in range(3):
            prev = by_start.get((t0 - timedelta(hours=k)).isoformat())
            if prev is not None and prev.get("pv_kwh") is not None:
                pv3 += prev["pv_kwh"]
        d_t = tin1 - tin0
        if frac <= COOL_FRAC_MAX:
            cool.append((tin0 - tout, pv3, -d_t))
        elif frac >= HEAT_FRAC_MIN:
            heat.append((tout, d_t, tin0, pv3))
    return cool, heat


def fit_room_model(rows: list[dict]) -> RoomModel:
    """Fit cooling and heating rates; backtest on every other sample."""
    cool, heat = _samples(rows)
    model = RoomModel(cool_hours=len(cool), heat_hours=len(heat))
    if len(cool) < MIN_COOL_HOURS or len(heat) < MIN_HEAT_HOURS:
        model.reason = (f"learning: {len(cool)}/{MIN_COOL_HOURS} cooling and "
                        f"{len(heat)}/{MIN_HEAT_HOURS} heating hours")
        return model

    def fit(cs, hs) -> RoomModel | None:
        ab = _lstsq2([s[0] for s in cs], [-s[1] for s in cs], [s[2] for s in cs], False)
        # Heating samples: ΔT = heating − cooling → heating = ΔT + cooling.
        if ab is None:
            return None
        a, b = ab
        b = max(0.0, b)
        ys = [s[1] + (a * (s[2] - s[0]) - b * s[3]) for s in hs]
        cd = _lstsq2([], [s[0] for s in hs], ys, True)
        if cd is None:
            return None
        return RoomModel(a=a, b=b, c=cd[0], d=cd[1])

    full = fit(cool, heat)
    if full is None:
        model.reason = "fit failed (degenerate data)"
        return model
    model.a, model.b, model.c, model.d = full.a, full.b, full.c, full.d

    # Look back: fit on even samples, predict the odd ones.
    train = fit(cool[::2], heat[::2])
    errors = []
    if train is not None:
        for x, pv3, loss in cool[1::2]:
            errors.append(abs(train.a * x - train.b * pv3 - loss))
        for tout, d_t, tin, pv3 in heat[1::2]:
            pred = train.heating_k_h(tout) - train.cooling_k_h(tin, tout, pv3)
            errors.append(abs(pred - d_t))
    model.backtest_mae_k_h = sum(errors) / len(errors) if errors else None
    if model.a <= 0:
        model.reason = "implausible: house does not cool down (a ≤ 0)"
    elif model.backtest_mae_k_h is None:
        model.reason = "no backtest samples"
    elif model.backtest_mae_k_h > MAX_BACKTEST_MAE_K_H:
        model.reason = (f"backtest error {model.backtest_mae_k_h:.2f} K/h above "
                        f"{MAX_BACKTEST_MAE_K_H} K/h – not used")
    else:
        model.reason = f"ready, backtest error {model.backtest_mae_k_h:.2f} K/h"
    return model


# ----------------------------------------------------------------------
# Planner
# ----------------------------------------------------------------------

@dataclass(frozen=True)
class HeatSlot:
    start: datetime
    end: datetime
    price_eur_kwh: float
    tier: str | None
    outdoor_c: float
    pv_kwh: float = 0.0
    cop: float = 3.5

    @property
    def hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0


@dataclass
class HeatingPlan:
    slots: list[HeatSlot]
    actions: list[int]                 # +1 pre-heat, −1 lowered, 0 normal
    temps: list[float]                 # predicted room temperature at slot end
    up_k: float
    down_k: float
    ready: bool
    reason: str
    lowered_h: float = 0.0
    preheat_h: float = 0.0
    shifted_cost_eur_kwh_th: float | None = None

    def offset_at(self, moment: datetime) -> float | None:
        for s, a in zip(self.slots, self.actions):
            if s.start <= moment < s.end:
                return self.up_k if a > 0 else (-self.down_k if a < 0 else 0.0)
        return None

    def as_dict(self) -> dict:
        return {
            "ready": self.ready, "reason": self.reason,
            "lowered_h": round(self.lowered_h, 1), "preheat_h": round(self.preheat_h, 1),
            "slots": [
                {"start": s.start.isoformat(), "action": a, "temp_c": round(t, 2)}
                for s, a, t in zip(self.slots, self.actions, self.temps) if a != 0
            ],
        }


def _simulate(model: RoomModel, slots: list[HeatSlot], actions: list[int],
              t_now: float, setpoint_c: float, boost_factor: float) -> list[float]:
    """Room temperature at each slot end.

    Normal (0): the heat pump holds the room near the setpoint (the heating
    curve does that), so the temperature drifts to the setpoint at the
    heating rate. Lowered (−1): no heating, the room cools. Pre-heat (+1):
    heating rate × boost_factor above the normal drift.
    """
    temps = []
    t = t_now
    for s, a in zip(slots, actions):
        h = s.hours
        pv = s.pv_kwh
        cool = model.cooling_k_h(t, s.outdoor_c, pv) * h
        heat = model.heating_k_h(s.outdoor_c) * h
        if a < 0:
            t = t - cool
        elif a > 0:
            t = t - cool + heat * boost_factor
        else:
            # Normal: below the setpoint the heat pump heats up to it; above
            # it the room drifts down and the heating catches it there.
            if t < setpoint_c:
                t = min(setpoint_c, t - cool + heat)
            else:
                t = max(setpoint_c, t - cool)
        temps.append(t)
    return temps


def plan_heating(
    model: RoomModel,
    slots: list[HeatSlot],
    *,
    t_now: float,
    comfort_min_c: float,
    setpoint_c: float,
    up_k: float,
    down_k: float,
    boost_per_k: float = 0.15,
) -> HeatingPlan:
    """Lower in every high slot, pre-heat in the cheapest slot before a violation."""
    if not model.ready:
        return HeatingPlan(slots, [0] * len(slots), [t_now] * len(slots), up_k, down_k,
                           False, model.reason)
    boost = 1.0 + boost_per_k * up_k
    actions = [-1 if s.tier == "high" else 0 for s in slots]
    for _ in range(MAX_ITERATIONS):
        temps = _simulate(model, slots, actions, t_now, setpoint_c, boost)
        bad = next((i for i, t in enumerate(temps) if t < comfort_min_c), None)
        if bad is None:
            break
        window_start = slots[bad].end - PREHEAT_WINDOW
        candidates = [i for i in range(bad + 1)
                      if actions[i] == 0 and slots[i].start >= window_start]
        if not candidates:
            # Nothing left to pre-heat: give up the lowering that hurts.
            lowered = [i for i in range(bad + 1) if actions[i] < 0]
            if not lowered:
                break
            actions[lowered[-1]] = 0
            continue
        best = min(candidates, key=lambda i: (slots[i].price_eur_kwh / max(0.5, slots[i].cop),
                                              slots[i].start))
        actions[best] = 1
    temps = _simulate(model, slots, actions, t_now, setpoint_c, boost)
    lowered_h = sum(s.hours for s, a in zip(slots, actions) if a < 0)
    preheat_h = sum(s.hours for s, a in zip(slots, actions) if a > 0)
    reason = (f"{lowered_h:.1f} h lowered, {preheat_h:.1f} h pre-heat, "
              f"min {min(temps, default=t_now):.1f} °C")
    return HeatingPlan(slots, actions, temps, up_k, down_k, True, reason, lowered_h, preheat_h)
