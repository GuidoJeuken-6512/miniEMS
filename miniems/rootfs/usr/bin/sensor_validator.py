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
    """Tracks last-accepted value per entity and detects power spikes.

    A single rejected reading does not move the reference on its own – one
    bad sample must not be enough to adopt a bogus baseline. But two
    *consecutive* rejected readings that agree with each other are no longer
    noise: e.g. a heavy load ends and the real power settles at a new, much
    lower value. Without this, the very first legitimate reading after such
    a real shift looks exactly like a spike relative to the old baseline,
    gets rejected, and the reference never updates – every future real
    reading then looks like a spike too, forever, until the add-on restarts.
    Observed live: battery_power stuck rejecting everything for 2.5+ hours
    after one real ~950 W drop.

    The second matching reading promotes the candidate to the new baseline –
    the same two-tick confirmation rule already used for lifetime-counter
    re-anchoring in cost_optimizer.py.
    """

    def __init__(self) -> None:
        self._last: dict[str, float] = {}
        self._pending: dict[str, float] = {}   # first rejected reading, awaiting a second

    def validate(self, entity_id: str, value: float) -> float | None:
        """Return value if plausible, None if it looks like a spike.

        First-ever reading for an entity is always accepted. The internal
        state is updated only for accepted readings (or a spike confirmed by
        a second, agreeing reading), so a one-off spike does not poison
        future comparisons.
        """
        previous = self._last.get(entity_id)

        if previous is not None and self._is_spike(previous, value):
            candidate = self._pending.get(entity_id)
            if candidate is not None and not self._is_spike(candidate, value):
                # Two consecutive readings agree, both far from the old
                # baseline – the shift is real, not noise. Adopt it.
                _LOGGER.info(
                    "Spike on %s confirmed by a second reading (%.0f W) – "
                    "adopting as new baseline (was %.0f W)",
                    entity_id, value, previous,
                )
                self._last[entity_id] = value
                self._pending.pop(entity_id, None)
                return value

            delta = abs(value - previous)
            ratio = delta / max(1.0, abs(previous))
            _LOGGER.warning(
                "Spike detected on %s: %.0f W → %.0f W (Δ%.0f W, %.0f%%) – skipped",
                entity_id, previous, value, delta, ratio * 100,
            )
            self._pending[entity_id] = value
            return None

        self._last[entity_id] = value
        self._pending.pop(entity_id, None)
        return value

    @staticmethod
    def _is_spike(reference: float, value: float) -> bool:
        delta = abs(value - reference)
        ratio = delta / max(1.0, abs(reference))
        return delta > _DELTA_W_THRESHOLD and ratio > _DELTA_RATIO_THRESHOLD
