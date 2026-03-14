"""Solcast PV forecast accessor for miniEMS.

Reads Solcast sensor states from the HA state cache (via HAWebSocketClient)
and exposes three scalar values used by the EMS grid-charge decision logic.

None is returned when an entity is unconfigured or unavailable – callers must
handle None rather than treating it as 0.
"""
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config
    from ha_ws_client import HAWebSocketClient

_LOGGER = logging.getLogger(__name__)


class SolcastClient:
    """Reads Solcast HA entities from the WebSocket state cache."""

    def __init__(self, config: "Config", ws_client: "HAWebSocketClient") -> None:
        self._cfg = config
        self._ws = ws_client

    @property
    def remaining_today_kwh(self) -> float | None:
        entity = self._cfg.solcast_remaining_today_entity
        if not entity:
            return None
        return self._ws.get_state_value(entity)

    @property
    def today_kwh(self) -> float | None:
        entity = self._cfg.solcast_today_entity
        if not entity:
            return None
        return self._ws.get_state_value(entity)

    @property
    def tomorrow_kwh(self) -> float | None:
        entity = self._cfg.solcast_tomorrow_entity
        if not entity:
            return None
        return self._ws.get_state_value(entity)
