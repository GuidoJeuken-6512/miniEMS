"""Sensor spike detection for miniEMS.

Validates incoming power readings by comparing them to the previous accepted
value.  Returns None on a spike so the caller can skip accumulation.
"""
import logging

_LOGGER = logging.getLogger(__name__)

# A reading is a spike if BOTH conditions are met:
_DELTA_W_THRESHOLD = 500.0    # absolute change in watts
_DELTA_RATIO_THRESHOLD = 0.5  # relative change (50%)


class SensorValidator:
    """Tracks last-accepted value per entity and detects power spikes."""

    def __init__(self) -> None:
        self._last: dict[str, float] = {}

    def validate(self, entity_id: str, value: float) -> float | None:
        """Return value if plausible, None if it looks like a spike.

        First-ever reading for an entity is always accepted.
        The internal state is updated only for accepted readings, so a spike
        does not poison future comparisons.
        """
        previous = self._last.get(entity_id)

        if previous is not None:
            delta = abs(value - previous)
            ratio = delta / max(1.0, abs(previous))
            if delta > _DELTA_W_THRESHOLD and ratio > _DELTA_RATIO_THRESHOLD:
                _LOGGER.warning(
                    "Spike detected on %s: %.0f W → %.0f W (Δ%.0f W, %.0f%%) – skipped",
                    entity_id, previous, value, delta, ratio * 100,
                )
                return None

        self._last[entity_id] = value
        return value
