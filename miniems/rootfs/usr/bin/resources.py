"""B1/B2 at run time: reads the phase currents and the §14a signal, keeps the
fuse guard, and hands each controllable load its power cap.

docs/roadmap/gesamtplan.md, B1 (Sicherung, §14a) and B2 (Verteiler).

Loads compete in a fixed order – the task with the tightest deadline first.
Spare headroom goes to the first load that wants more; a deficit is taken
from the last load first. While the §14a dimming signal is on, the sum of all
controllable grid draws is held at ``grid_dimming_limit_w``.

Off (``fuse_guard_enabled`` false and no dimming entity) every cap is None,
and the callers behave exactly as before.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING

from fuse_guard import FuseGuard, dimming_share_w

if TYPE_CHECKING:
    from config_loader import Config
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class LoadDemand:
    """One controllable load as the coordinator sees it. Power in W."""

    name: str
    own_w: float          # what it draws from the grid now
    want_w: float         # what it would like to draw
    phases: int = 3


def split_entities(raw: str) -> list[str]:
    return [e.strip() for e in (raw or "").split(",") if e.strip()]


class ResourceCoordinator:
    def __init__(self, cfg: "Config", ws: "HAStateClient") -> None:
        self._cfg = cfg
        self._ws = ws
        self.guard: FuseGuard | None = None
        self._caps: dict[str, float | None] = {}
        self._warned = False
        if cfg.fuse_guard_enabled:
            if cfg.fuse_rated_a <= 0 or len(split_entities(cfg.phase_current_entities)) != 3:
                _LOGGER.warning("fuse_guard_enabled, but fuse_rated_a or the three "
                                "phase_current_entities are missing – guard off")
            else:
                self.guard = FuseGuard(cfg.fuse_rated_a, cfg.fuse_work_limit_pct,
                                       voltage_v=cfg.phase_voltage_v)

    @property
    def active(self) -> bool:
        return self.guard is not None or bool(self._cfg.grid_dimming_entity)

    def dimming(self) -> bool:
        entity = self._cfg.grid_dimming_entity
        if not entity:
            return False
        raw = self._ws.state_cache.get(entity, {}).get("state")
        return str(raw).lower() in ("on", "true", "1", "active")

    def tick(self, t: float, controlled_w: float = 0.0) -> None:
        """Feed the guard one set of phase readings."""
        if self.guard is None:
            return
        cfg = self._cfg
        currents = []
        for entity in split_entities(cfg.phase_current_entities):
            stale = self._ws.is_stale(entity, cfg.fuse_stale_sec)
            currents.append(None if stale else self._ws.get_state_value(entity))
        powers = [self._ws.get_state_value(e) for e in split_entities(cfg.phase_power_entities)]
        per_phase_a = max(0.0, controlled_w) / (3 * cfg.phase_voltage_v)
        self.guard.update(t, currents, powers or None, controlled_a=per_phase_a)

    def caps(self, t: float, loads: list[LoadDemand]) -> dict[str, float | None]:
        """Cap per load (W). ``loads`` is in priority order (first = tightest deadline)."""
        if not self.active:
            self._caps = {ld.name: None for ld in loads}
            return self._caps
        caps: dict[str, float | None] = {ld.name: None for ld in loads}
        guard = self.guard
        if guard is not None:
            v = guard.voltage_v
            status = guard.status(t)
            if status.tripped:
                caps = {ld.name: 0.0 for ld in loads}
            else:
                room_a = guard.headroom_a()
                if room_a >= 0:
                    for ld in loads:
                        give = min(max(0.0, ld.want_w - ld.own_w), room_a * v * ld.phases)
                        caps[ld.name] = ld.own_w + give
                        room_a -= give / (v * ld.phases)
                else:
                    deficit_a = -room_a
                    for ld in loads:
                        caps[ld.name] = ld.own_w
                    for ld in reversed(loads):
                        cut = min(ld.own_w, deficit_a * v * ld.phases)
                        caps[ld.name] = ld.own_w - cut
                        deficit_a -= cut / (v * ld.phases)
                        if deficit_a <= 0:
                            break
                if guard.in_hold(t):
                    caps = {ld.name: min(caps[ld.name], ld.own_w) for ld in loads}
        if self.dimming():
            wanted = {ld.name: (caps[ld.name] if caps[ld.name] is not None else ld.want_w)
                      for ld in loads}
            share = dimming_share_w(self._cfg.grid_dimming_limit_w, wanted,
                                    [ld.name for ld in loads])
            for name, w in share.items():
                caps[name] = w if caps[name] is None else min(caps[name], w)
        self._caps = caps
        return caps

    def status(self, t: float) -> dict | None:
        if not self.active:
            return None
        dimming = self.dimming()
        if self.guard is None:
            return {"active": False, "dimming": dimming,
                    "dimming_limit_w": self._cfg.grid_dimming_limit_w if dimming else None,
                    "limits_w": {k: round(v) for k, v in self._caps.items() if v is not None}}
        return self.guard.status(
            t, dimming=dimming, dimming_limit_w=self._cfg.grid_dimming_limit_w,
            limits_w={k: v for k, v in self._caps.items() if v is not None},
        ).as_dict()
