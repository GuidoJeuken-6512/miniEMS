"""D1 – heat pump as a device class (docs/roadmap/gesamtplan.md, section 7).

The hot-water and heating logic talk to *capabilities*, never to Lambda
registers or entity names:

| Capability            | Config field(s)                                   |
|-----------------------|---------------------------------------------------|
| hot-water setpoint    | ``hw_setpoint_entity`` (number) **or** the service |
|                       | template ``hw_write_service`` + ``hw_write_data``  |
| compressor state      | ``hw_compressor_entity``                           |
| flow-line offset      | ``heating_offset_entity`` (number, K)              |
| PV surplus input      | ``heating_surplus_entity`` (number, W)             |
| room temperature      | ``heating_room_temp_entity``                       |

How a device implements a capability belongs into its profile
(``profiles/heat_pump/*.yaml``) and the config defaults, not into
``hot_water_controller.py`` or ``heating_controller.py``. The Lambda values
are the shipped defaults (see ``profiles/heat_pump/lambda_eu08l.yaml``).

A missing capability returns None; the block that needs it then stays off
and says why (guideline 3).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from config_loader import Config
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)

# Text states of the compressor entity that mean "the compressor runs"
# (central heating, hot water, cooling, defrost).
COMPRESSOR_RUNNING_STATES = frozenset({"CH", "DHW", "CC", "DEFROST"})


@dataclass(frozen=True)
class ServiceCall:
    domain: str
    service: str
    data: dict[str, Any]

    @property
    def name(self) -> str:
        return f"{self.domain}.{self.service}"


class HeatPumpDevice:
    """Capabilities of the configured heat pump."""

    def __init__(self, cfg: "Config", ws: "HAStateClient") -> None:
        self._cfg = cfg
        self._ws = ws
        self._warned: set[str] = set()

    # ------------------------------------------------------------------
    # Hot-water setpoint
    # ------------------------------------------------------------------

    @property
    def hw_setpoint_writable(self) -> bool:
        c = self._cfg
        return bool(c.hw_setpoint_entity) or "." in (c.hw_write_service or "")

    def hw_setpoint_call(self, setpoint_c: float, *, simulation: bool = False) -> ServiceCall | None:
        """The call that writes this boiler setpoint. None = cannot be written.

        In simulation an unset service is reported under a placeholder name,
        so the [SIM] log still shows what would be written.
        """
        c = self._cfg
        if c.hw_setpoint_entity:
            value = round(setpoint_c, 1)
            return ServiceCall("number", "set_value",
                               {"entity_id": c.hw_setpoint_entity, "value": value})
        raw = round(setpoint_c * c.hw_write_scale)
        try:
            data = json.loads(c.hw_write_data.replace("{value}", str(raw)))
        except (json.JSONDecodeError, TypeError) as exc:
            self._warn_once("data", "hw_write_data is not valid JSON after inserting the "
                                    "value: %s", exc)
            return None
        if "." in (c.hw_write_service or ""):
            domain, service = c.hw_write_service.split(".", 1)
            return ServiceCall(domain, service, data)
        if simulation:
            return ServiceCall("<hw_write_service unset>", "write", data)
        self._warn_once("service", "hot-water setpoint has no write path "
                                   "(hw_setpoint_entity / hw_write_service) – nothing written")
        return None

    def hw_setpoint_raw(self, setpoint_c: float) -> float:
        """What lands in the register/entity – for the status page."""
        c = self._cfg
        if c.hw_setpoint_entity:
            return round(setpoint_c, 1)
        return round(setpoint_c * c.hw_write_scale)

    @property
    def hw_write_label(self) -> str:
        c = self._cfg
        return f"number.set_value({c.hw_setpoint_entity})" if c.hw_setpoint_entity else c.hw_write_service

    # ------------------------------------------------------------------
    # Compressor
    # ------------------------------------------------------------------

    def compressor_running(self) -> bool | None:
        """Does the compressor run right now? None = unknown.

        Numeric sensor: > 0 = running (e.g. a compressor rating in %). Text
        sensor: one of COMPRESSOR_RUNNING_STATES.
        """
        entity = self._cfg.hw_compressor_entity
        if not entity:
            return None
        numeric = self._ws.get_state_value(entity)
        if numeric is not None:
            return numeric > 0
        raw = self._ws.get_state_raw(entity)
        if raw is None or raw in ("unavailable", "unknown", ""):
            return None
        return str(raw).strip().upper() in COMPRESSOR_RUNNING_STATES

    # ------------------------------------------------------------------
    # Heating (B6)
    # ------------------------------------------------------------------

    @property
    def has_flow_offset(self) -> bool:
        return bool(self._cfg.heating_offset_entity)

    def flow_offset_call(self, offset_k: float) -> ServiceCall | None:
        entity = self._cfg.heating_offset_entity
        if not entity:
            return None
        return ServiceCall("number", "set_value",
                           {"entity_id": entity, "value": round(offset_k, 1)})

    def flow_offset_now(self) -> float | None:
        entity = self._cfg.heating_offset_entity
        return self._ws.get_state_value(entity) if entity else None

    def surplus_call(self, surplus_w: float) -> ServiceCall | None:
        entity = self._cfg.heating_surplus_entity
        if not entity:
            return None
        return ServiceCall("number", "set_value", {"entity_id": entity, "value": round(surplus_w)})

    def room_temp_c(self) -> float | None:
        entity = self._cfg.heating_room_temp_entity
        return self._ws.get_state_value(entity) if entity else None

    def power_w(self) -> float | None:
        entities = self._cfg.heat_pump_entities
        if not entities:
            return None
        values = [self._ws.get_state_value(e) for e in entities]
        if any(v is None for v in values):
            return None
        return float(sum(values))

    def missing(self, *capabilities: str) -> list[str]:
        """Which of the named capabilities are not configured."""
        have = {
            "hw_setpoint": self.hw_setpoint_writable,
            "compressor": bool(self._cfg.hw_compressor_entity),
            "flow_offset": self.has_flow_offset,
            "surplus": bool(self._cfg.heating_surplus_entity),
            "room_temp": bool(self._cfg.heating_room_temp_entity),
            "power": bool(self._cfg.heat_pump_entities),
        }
        return [c for c in capabilities if not have.get(c, False)]

    def _warn_once(self, key: str, msg: str, *args: Any) -> None:
        if key not in self._warned:
            self._warned.add(key)
            _LOGGER.warning(msg, *args)
