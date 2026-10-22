"""Solcast PV forecast accessor for miniEMS.

Reads Solcast sensor states from the HA state cache (via HAStateClient)
and exposes three scalar values used by the EMS grid-charge decision logic.

None is returned when an entity is unconfigured or unavailable – callers must
handle None rather than treating it as 0.
"""
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from config_loader import Config
    from ha_state_client import HAStateClient

_LOGGER = logging.getLogger(__name__)


class SolcastClient:
    """Reads Solcast HA entities from the WebSocket state cache."""

    def __init__(self, config: "Config", ws_client: "HAStateClient") -> None:
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

    # ------------------------------------------------------------------
    # Slot forecast (v3.3.0, Überbrückungsplanung)
    # ------------------------------------------------------------------

    def pv_slots(self, scenario: str = "blend", blend_weight: float = 0.7) -> dict[datetime, tuple[datetime, float]]:
        """PV energy per forecast slot, today and tomorrow.

        Read from the `detailedForecast` attribute (30-min slots) of the
        today/tomorrow entities, falling back to `detailedHourly`. Each entry
        carries `pv_estimate`, `pv_estimate10`, `pv_estimate90` as the slot's
        **mean power in kW** – energy is kW × slot length.

        Returns {slot_start: (slot_end, kWh)} with tz-aware datetimes. Empty
        when the attribute is missing (option "detailed forecast" off in the
        Solcast integration) – callers must treat that as "no slot forecast",
        not as "no sun".

        Raw data is sanitised: negative values clamp to 0 and the scenario
        ordering est10 ≤ est is enforced (observed: est90 = 0 < est at dusk).
        """
        result: dict[datetime, tuple[datetime, float]] = {}
        for entity in (self._cfg.solcast_today_entity, self._cfg.solcast_tomorrow_entity):
            if not entity:
                continue
            entries = self._ws.get_state_attribute(entity, "detailedForecast")
            default_len = timedelta(minutes=30)
            if not isinstance(entries, list) or not entries:
                entries = self._ws.get_state_attribute(entity, "detailedHourly")
                default_len = timedelta(hours=1)
            if not isinstance(entries, list):
                continue
            parsed: list[tuple[datetime, float]] = []
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                start = _parse_dt(entry.get("period_start"))
                if start is None:
                    continue
                kw = _scenario_kw(entry, scenario, blend_weight)
                if kw is None:
                    continue
                parsed.append((start, kw))
            parsed.sort(key=lambda p: p[0])
            for i, (start, kw) in enumerate(parsed):
                if i + 1 < len(parsed) and parsed[i + 1][0] > start:
                    length = min(parsed[i + 1][0] - start, default_len)
                else:
                    length = default_len
                result[start] = (start + length, kw * length.total_seconds() / 3600.0)
        return result


def _parse_dt(raw: object) -> datetime | None:
    if isinstance(raw, datetime):
        dt = raw
    elif isinstance(raw, str):
        try:
            dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _num(value: object) -> float | None:
    try:
        return max(0.0, float(value))   # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _scenario_kw(entry: dict, scenario: str, blend_weight: float) -> float | None:
    est = _num(entry.get("pv_estimate"))
    if est is None:
        return None
    est10 = _num(entry.get("pv_estimate10"))
    est10 = est if est10 is None else min(est10, est)
    if scenario == "estimate":
        return est
    if scenario == "estimate10":
        return est10
    w = max(0.0, min(1.0, blend_weight))
    return w * est + (1.0 - w) * est10
