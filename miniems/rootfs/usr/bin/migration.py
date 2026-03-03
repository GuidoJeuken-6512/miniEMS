"""Config version migrations for miniEMS.

Each _vX_to_vY function migrates the raw config dict between schema versions.
Add a new function and increment CURRENT_VERSION whenever the config schema changes.
"""
import logging

_LOGGER = logging.getLogger(__name__)

CURRENT_VERSION = 1


def migrate(data: dict) -> dict:
    """Migrate config dict to CURRENT_VERSION. Returns updated dict."""
    version = data.get("_version", 0)
    if version >= CURRENT_VERSION:
        return data

    _LOGGER.info("Migrating config v%d → v%d", version, CURRENT_VERSION)

    if version < 1:
        data = _v0_to_v1(data)

    data["_version"] = CURRENT_VERSION
    return data


# ---------------------------------------------------------------------------
# Migration steps
# ---------------------------------------------------------------------------

def _v0_to_v1(data: dict) -> dict:
    """v0 → v1: rename GBP fields to EUR (currency switch)."""
    if "cheap_rate_threshold_gbp" in data and "cheap_rate_threshold_eur" not in data:
        data["cheap_rate_threshold_eur"] = data.pop("cheap_rate_threshold_gbp")
        _LOGGER.info("Migrated cheap_rate_threshold_gbp → cheap_rate_threshold_eur")
    return data
