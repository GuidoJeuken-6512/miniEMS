"""B8 – best start time for flexible appliances (docs/roadmap/gesamtplan.md, B8).

For every configured appliance (``flex_devices``: "Name:kWh:hours, …") the
start within the planning horizon whose run costs least – PV surplus valued
at the feed-in tariff, the rest at the grid price. Published as
``sensor.miniems_best_start_<slug>``. It steers nothing.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timedelta

from energy_task import TaskSlot, effective_price


@dataclass(frozen=True)
class FlexDevice:
    name: str
    energy_kwh: float
    duration_h: float

    @property
    def slug(self) -> str:
        return slugify(self.name)


@dataclass(frozen=True)
class StartAdvice:
    device: FlexDevice
    start: datetime
    end: datetime
    cost_eur: float
    cost_now_eur: float | None

    @property
    def saving_eur(self) -> float | None:
        return None if self.cost_now_eur is None else self.cost_now_eur - self.cost_eur

    def as_dict(self) -> dict:
        return {
            "name": self.device.name,
            "slug": self.device.slug,
            "energy_kwh": self.device.energy_kwh,
            "duration_h": self.device.duration_h,
            "start": self.start.isoformat(),
            "end": self.end.isoformat(),
            "cost_eur": round(self.cost_eur, 3),
            "cost_now_eur": None if self.cost_now_eur is None else round(self.cost_now_eur, 3),
            "saving_eur": None if self.saving_eur is None else round(self.saving_eur, 3),
        }


def slugify(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "_", text.lower()).strip("_")
    return text or "device"


def parse_devices(raw: str) -> list[FlexDevice]:
    """"Waschmaschine:1.2:2, Spülmaschine:1:3" → devices. Bad entries are skipped."""
    out: list[FlexDevice] = []
    seen: set[str] = set()
    for part in (raw or "").split(","):
        bits = [b.strip() for b in part.split(":")]
        if len(bits) != 3 or not bits[0]:
            continue
        try:
            kwh, hours = float(bits[1]), float(bits[2])
        except ValueError:
            continue
        if kwh <= 0 or hours <= 0 or hours > 24:
            continue
        dev = FlexDevice(bits[0], kwh, hours)
        if dev.slug in seen:
            continue
        seen.add(dev.slug)
        out.append(dev)
    return out


def _run_cost(device: FlexDevice, slots: list[TaskSlot], start: datetime) -> float | None:
    """Cost of a run starting at ``start``; None if the slots do not cover it."""
    end = start + timedelta(hours=device.duration_h)
    power_w = device.energy_kwh * 1000.0 / device.duration_h
    cost = 0.0
    covered = 0.0
    for s in slots:
        a, b = max(start, s.start), min(end, s.end)
        if b <= a:
            continue
        h = (b - a).total_seconds() / 3600.0
        covered += h
        cost += effective_price(s, power_w, s.pv_surplus_w) * power_w * h / 1000.0
    if covered < device.duration_h - 1e-6:
        return None
    return cost


def best_start(device: FlexDevice, slots: list[TaskSlot], now: datetime) -> StartAdvice | None:
    """Cheapest start on the slot grid (and now) within the horizon."""
    if not slots:
        return None
    candidates = sorted({now, *[s.start for s in slots if s.start >= now]})
    best: tuple[float, datetime] | None = None
    for start in candidates:
        cost = _run_cost(device, slots, start)
        if cost is None:
            continue
        if best is None or cost < best[0] - 1e-9:
            best = (cost, start)
    if best is None:
        return None
    return StartAdvice(device, best[1], best[1] + timedelta(hours=device.duration_h),
                       best[0], _run_cost(device, slots, now))
