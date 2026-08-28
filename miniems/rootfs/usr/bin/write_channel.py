"""Generalised write-confirm/retry primitives, extracted from
inverter_controller.py (v2.3.0) so a future device_control.py can drive
`switch`/`number`/`select` writes the same way, not just the Deye's three
fixed channels.

Semantics are unchanged from the original inline `_WriteChannel`/comparison
logic in `inverter_controller.py` – this is a pure extraction, not a
behaviour change:

- HTTP 200 from HA never marks a write confirmed on its own – only a fresh
  comparison against the *real* entity state does (`matches()` below).
- `number` comparisons are numeric with a tolerance (float rounding on the
  HA side); `switch`/`select` comparisons are exact string equality.
- A channel tracks two independent timestamps: `sent_at` (last attempt, used
  to throttle retries) and `pending_since` (when it first went wrong, used to
  answer "how long has this really been unconfirmed" for
  `longest_pending_sec`/`stuck_channel_labels` – see
  inverter_controller.py's `_inverter_write_status()`).
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class WriteSpec:
    """One write's intent: what to call, what value confirms it, how to
    compare. `tolerance` only matters for `domain == "number"`."""

    domain: str            # "switch" | "number" | "select"
    service: str            # "turn_on" | "turn_off" | "set_value" | "select_option"
    entity_id: str
    payload: dict[str, Any]
    expected: Any            # str (switch/select) or float (number)
    tolerance: float = 0.5


def matches(observed_raw: Any, spec: WriteSpec) -> bool:
    """Does the real HA state (`observed_raw`, already read by the caller)
    confirm this write? `None`/unparsable input is never a match – the
    caller's job is telling "no reading yet" from "a reading that matches",
    not this function's.
    """
    if spec.domain == "number":
        if observed_raw is None:
            return False
        try:
            observed = float(observed_raw)
        except (TypeError, ValueError):
            return False
        try:
            expected = float(spec.expected)
        except (TypeError, ValueError):
            return False
        return abs(observed - expected) < spec.tolerance
    return observed_raw == spec.expected


class WriteChannel:
    """Tracks one write's target and confirmation state."""

    __slots__ = ("target", "sent_at", "confirmed", "pending_since", "label")

    def __init__(self) -> None:
        self.target: Any = None
        self.sent_at: float | None = None    # time.monotonic() of the last send attempt
        self.confirmed: bool = True          # nothing pending yet
        # time.monotonic() of when this channel most recently became
        # unconfirmed - distinct from sent_at, which resets on every retry.
        self.pending_since: float | None = None
        self.label: str = ""                 # human-readable, set on first use


class WriteChannelSet:
    """A named group of WriteChannels (e.g. "charge"/"discharge"/"grid" for
    the Deye today), with the aggregate read-only views a dashboard/warning
    banner needs, computed once over however many channels exist – replaces
    what used to be three separately-named attributes on InverterController.
    """

    def __init__(self, names: list[str]) -> None:
        self._channels: dict[str, WriteChannel] = {name: WriteChannel() for name in names}

    def __getitem__(self, name: str) -> WriteChannel:
        return self._channels[name]

    def __iter__(self):
        return iter(self._channels.values())

    def reset(self) -> None:
        """Force fresh sends on every channel, regardless of whatever
        target/confirmation state they were last left in – used before
        `restore_safe_defaults()` re-applies the safe values."""
        for name in self._channels:
            self._channels[name] = WriteChannel()

    @property
    def unconfirmed_count(self) -> int:
        return sum(0 if ch.confirmed else 1 for ch in self._channels.values())

    @property
    def longest_pending_sec(self) -> float:
        now = time.monotonic()
        pending = [
            now - ch.pending_since for ch in self._channels.values()
            if not ch.confirmed and ch.pending_since is not None
        ]
        return max(pending, default=0.0)

    @property
    def stuck_channel_labels(self) -> list[str]:
        now = time.monotonic()
        pending = [
            (now - ch.pending_since, ch.label) for ch in self._channels.values()
            if not ch.confirmed and ch.pending_since is not None and ch.label
        ]
        pending.sort(reverse=True)
        return [label for _, label in pending]
