"""integration_installer.py – copies bundled integration files to /config/custom_components/miniems/.

Called once at addon startup. Compares source vs. installed manifest.json version to
decide whether a copy is needed. Falls back to file-hash comparison for individual files.
After a successful update, triggers a reload of the integration via the HA API.
"""
from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path

import aiohttp

import const
from const import INTEGRATION_SOURCE_DIR, INTEGRATION_TARGET_DIR

_LOGGER = logging.getLogger(__name__)


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


def _manifest_version(directory: Path) -> str | None:
    """Read the version field from manifest.json in the given directory."""
    try:
        data = json.loads((directory / "manifest.json").read_text(encoding="utf-8"))
        return data.get("version")
    except (OSError, json.JSONDecodeError):
        return None


async def install_integration() -> None:
    """Ensure /config/custom_components/miniems/ is up-to-date.

    Async for consistency with the main.py async context; file I/O is synchronous
    (files are small and the operation runs only once at startup).
    """
    if not INTEGRATION_SOURCE_DIR.exists():
        _LOGGER.error(
            "Integration source dir not found: %s – skipping install",
            INTEGRATION_SOURCE_DIR,
        )
        return

    src_version = _manifest_version(INTEGRATION_SOURCE_DIR)
    dst_version = _manifest_version(INTEGRATION_TARGET_DIR)

    if src_version and src_version == dst_version:
        _LOGGER.debug("Integration v%s already installed – skipping copy", src_version)
        await _reload_integration()
        return

    _LOGGER.info(
        "Installing miniEMS integration v%s (was: v%s) → %s",
        src_version,
        dst_version or "none",
        INTEGRATION_TARGET_DIR,
    )

    try:
        INTEGRATION_TARGET_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOGGER.error("Cannot create integration dir %s: %s", INTEGRATION_TARGET_DIR, exc)
        return

    files_written = 0
    files_skipped = 0

    for src in INTEGRATION_SOURCE_DIR.rglob("*"):
        if not src.is_file():
            continue
        relative = src.relative_to(INTEGRATION_SOURCE_DIR)
        dst = INTEGRATION_TARGET_DIR / relative
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists() and _file_hash(src) == _file_hash(dst):
            files_skipped += 1
            continue
        try:
            shutil.copy2(str(src), str(dst))
            files_written += 1
        except OSError as exc:
            _LOGGER.error("Failed to copy %s → %s: %s", src, dst, exc)

    _LOGGER.info(
        "Integration install complete: %d written, %d unchanged",
        files_written,
        files_skipped,
    )

    if files_written > 0:
        _write_restart_marker(src_version)
        await _reload_integration()


def _write_restart_marker(version: str | None) -> None:
    """Write a marker file that __init__.py reads to create a HA repair issue."""
    marker = INTEGRATION_TARGET_DIR / ".restart_required"
    try:
        marker.write_text(version or "", encoding="utf-8")
    except OSError as exc:
        _LOGGER.warning("Could not write restart marker: %s", exc)


async def _reload_integration() -> None:
    """Ask HA Core to reload the miniems config entry.

    Home Assistant's REST API has no endpoint to list or reload config
    entries directly – that's WebSocket-only (config_entries/list,
    config_entries/reload). A previous version of this function called
    GET .../config/config_entries, which is not a real REST endpoint and
    always returned 404: the integration was silently never reloaded after
    an update. That in turn is why a renamed sensor `key` (e.g. a typo fix)
    could leave a permanently orphaned entity behind, which later collided
    with the freshly-registered one and forced a "_2" suffix onto it –
    async_setup_entry's registry cleanup (see integration/__init__.py)
    never got a chance to run promptly; it only ran on the next unrelated
    full HA Core restart.

    Fix: call the homeassistant.reload_config_entry *service* instead
    (POST /api/services/..., a real REST endpoint, same pattern
    InverterController already uses). It accepts an entity_id and resolves
    the owning config entry itself, so no entry_id lookup is needed. The
    entity_id is resolved via a Jinja template (POST /api/template,
    integration_entities()) rather than hardcoded – even a "stable-looking"
    key like "miniems_mode" has turned out to have older, pre-repository
    renames of its own on at least one real installation, so any single
    hardcoded name risks pointing at a past orphan instead of a live entity.
    """
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        _LOGGER.warning("No SUPERVISOR_TOKEN – cannot reload integration")
        return
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    try:
        async with aiohttp.ClientSession() as session:
            async with session.post(
                f"{const.HA_API_BASE}/template",
                json={"template": "{{ integration_entities('miniems') | first | default('') }}"},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning(
                        "Could not resolve a miniems entity for reload (HTTP %s)", resp.status
                    )
                    return
                anchor_entity = (await resp.text()).strip()

            if not anchor_entity:
                _LOGGER.warning("No miniems entity found – integration not set up yet, skipping reload")
                return

            async with session.post(
                f"{const.HA_SERVICES_URL}/homeassistant/reload_config_entry",
                json={"entity_id": anchor_entity},
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 201):
                    _LOGGER.info("Integration reload requested successfully (via %s)", anchor_entity)
                else:
                    _LOGGER.warning("Integration reload returned HTTP %s", resp.status)
    except Exception as exc:
        _LOGGER.warning("Integration reload failed: %s", exc)
