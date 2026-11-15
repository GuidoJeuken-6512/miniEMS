"""C2 – manual override with an expiry (docs/roadmap/gesamtplan.md, C2).

Rank 3 of the rule order: an override beats the plan and every economic
rule, but never a hard limit (stale data, SoC below minimum). Every
override expires – 15 min to 6 h – and the device then falls back to Auto.
There is no override without an expiry.

Devices and values:

    battery: auto | grid_charge | hold | no_grid_charge
    ev:      auto | now | pv | stop

Conflict rule between the add-on page and Home Assistant: the value written
last wins; both read the same store.

The store is persisted (kv_state) so an override survives a restart until
its expiry.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from store import EnergyStore

VALUES: dict[str, tuple[str, ...]] = {
    "battery": ("auto", "grid_charge", "hold", "no_grid_charge"),
    "ev": ("auto", "now", "pv", "stop"),
}
MIN_MINUTES = 15
MAX_MINUTES = 360
DEFAULT_MINUTES = 120
_KV_KEY = "overrides"


class OverrideError(ValueError):
    """Invalid device, value or duration – the message says which."""


@dataclass(frozen=True)
class Override:
    device: str
    value: str
    until: datetime
    source: str = "api"

    def as_dict(self) -> dict[str, Any]:
        return {"value": self.value, "until": self.until.isoformat(), "source": self.source}


class OverrideStore:
    def __init__(self, store: "EnergyStore | None" = None) -> None:
        self._store = store
        self._active: dict[str, Override] = {}
        self.minutes = DEFAULT_MINUTES
        self._loaded = False

    async def load(self) -> None:
        if self._loaded or self._store is None:
            self._loaded = True
            return
        self._loaded = True
        data = await self._store.get_state(_KV_KEY)
        if not isinstance(data, dict):
            return
        try:
            self.minutes = clamp_minutes(int(data.get("minutes", DEFAULT_MINUTES)))
        except (TypeError, ValueError):
            pass
        for device, item in (data.get("active") or {}).items():
            try:
                if device in VALUES and item["value"] in VALUES[device]:
                    self._active[device] = Override(device, item["value"],
                                                    datetime.fromisoformat(item["until"]),
                                                    item.get("source", "api"))
            except (KeyError, TypeError, ValueError):
                continue

    async def _save(self) -> None:
        if self._store is None:
            return
        await self._store.set_state(_KV_KEY, {
            "minutes": self.minutes,
            "active": {d: o.as_dict() for d, o in self._active.items()},
        })

    def active(self, device: str, now: datetime) -> Override | None:
        o = self._active.get(device)
        if o is None:
            return None
        if now >= o.until:
            del self._active[device]
            return None
        return o

    def value(self, device: str, now: datetime) -> str:
        o = self.active(device, now)
        return o.value if o is not None else "auto"

    async def set(self, device: str, value: str, now: datetime, minutes: int | None = None,
                  source: str = "api") -> Override | None:
        """Start an override; "auto" ends it. Raises OverrideError."""
        if device not in VALUES:
            raise OverrideError(f"unknown device {device!r} (allowed: {', '.join(VALUES)})")
        if value not in VALUES[device]:
            raise OverrideError(f"unknown value {value!r} for {device} "
                                f"(allowed: {', '.join(VALUES[device])})")
        if value == "auto":
            await self.clear(device)
            return None
        mins = self.minutes if minutes is None else minutes
        if not (MIN_MINUTES <= mins <= MAX_MINUTES):
            raise OverrideError(f"duration {mins} min outside {MIN_MINUTES}–{MAX_MINUTES} min")
        o = Override(device, value, now + timedelta(minutes=mins), source)
        self._active[device] = o
        await self._save()
        return o

    async def clear(self, device: str | None = None) -> None:
        if device is None:
            self._active.clear()
        else:
            if device not in VALUES:
                raise OverrideError(f"unknown device {device!r}")
            self._active.pop(device, None)
        await self._save()

    async def set_minutes(self, minutes: int) -> None:
        if not (MIN_MINUTES <= minutes <= MAX_MINUTES):
            raise OverrideError(f"duration {minutes} min outside {MIN_MINUTES}–{MAX_MINUTES} min")
        self.minutes = minutes
        await self._save()

    def status(self, now: datetime) -> dict[str, Any]:
        out: dict[str, Any] = {"minutes": self.minutes,
                               "min_minutes": MIN_MINUTES, "max_minutes": MAX_MINUTES,
                               "options": {d: list(v) for d, v in VALUES.items()}}
        for device in VALUES:
            o = self.active(device, now)
            out[device] = o.as_dict() if o is not None else {"value": "auto", "until": None}
        return out


def clamp_minutes(minutes: int) -> int:
    return max(MIN_MINUTES, min(MAX_MINUTES, minutes))
