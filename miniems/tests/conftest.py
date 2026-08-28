"""Shared fixtures for the miniEMS test suite.

The app under test (rootfs/usr/bin/*.py) is a flat module layout, not a
package – pyproject.toml's `pythonpath` makes it importable as e.g.
`import ems_controller` directly, exactly like main.py does at runtime.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from config_loader import Config


@pytest.fixture
def make_config():
    """Factory for a Config with sane test defaults, overridable per test."""

    def _make(**overrides: Any) -> Config:
        cfg = Config()
        for key, value in overrides.items():
            assert hasattr(cfg, key), f"Config has no field {key!r}"
            setattr(cfg, key, value)
        return cfg

    return _make


class FakeWS:
    """Minimal stand-in for HAStateClient's read-only interface.

    Exercises the same contract real callers rely on (get_state_value,
    get_state_datetime, get_state_attribute, is_stale, is_stale_daily) without
    any HTTP – the network/parsing side of the real client is covered
    separately in test_ha_state_client.py.
    """

    def __init__(self) -> None:
        self.values: dict[str, float | None] = {}
        self.datetimes: dict[str, datetime | None] = {}
        self.attributes: dict[str, dict[str, Any]] = {}
        self.ages: dict[str, float | None] = {}
        self.stale: dict[str, bool] = {}
        self.stale_daily: dict[str, bool] = {}
        self.state_cache: dict[str, dict[str, Any]] = {}

    def get_state_value(self, entity_id: str) -> float | None:
        return self.values.get(entity_id)

    def get_state_datetime(self, entity_id: str) -> datetime | None:
        return self.datetimes.get(entity_id)

    def get_state_attribute(self, entity_id: str, attribute: str) -> Any:
        return self.attributes.get(entity_id, {}).get(attribute)

    def get_state_age_sec(self, entity_id: str) -> float | None:
        return self.ages.get(entity_id)

    def is_stale(self, entity_id: str, max_age_sec: float) -> bool:
        # Default: healthy/fresh unless a test explicitly marks an entity
        # stale. Decision-logic tests pass bat_soc/pv_w/etc. as plain
        # parameters, not via fake_ws.values, so inferring staleness from
        # "not in values" would flag the wrong things stale by default.
        return self.stale.get(entity_id, False)

    def is_stale_daily(self, entity_id: str, grace_sec: float = 0) -> bool:
        return self.stale_daily.get(entity_id, False)


@pytest.fixture
def fake_ws() -> FakeWS:
    return FakeWS()


@pytest.fixture
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def minutes_ago(n: float, base: datetime | None = None) -> datetime:
    return (base or datetime.now(timezone.utc)) - timedelta(minutes=n)
