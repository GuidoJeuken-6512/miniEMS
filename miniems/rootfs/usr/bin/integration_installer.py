"""integration_installer.py – copies bundled integration files to /config/custom_components/miniems/.

Called once at addon startup. Skips write if the installed version already matches.
After a successful update, triggers a reload of the integration via the HA API.
"""
from __future__ import annotations

import hashlib
import logging
import os
import shutil
from pathlib import Path

import aiohttp

import const
from const import INTEGRATION_SOURCE_DIR, INTEGRATION_TARGET_DIR

_LOGGER = logging.getLogger(__name__)

_VERSION_FILE = INTEGRATION_TARGET_DIR / ".miniems_version"


def _file_hash(path: Path) -> str:
    return hashlib.md5(path.read_bytes()).hexdigest()


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

    # Fast path: version already installed
    if _VERSION_FILE.exists():
        try:
            if _VERSION_FILE.read_text(encoding="utf-8").strip() == const.VERSION:
                _LOGGER.debug("Integration v%s already installed – skipping", const.VERSION)
                return
        except OSError:
            pass

    _LOGGER.info(
        "Installing miniEMS integration v%s → %s", const.VERSION, INTEGRATION_TARGET_DIR
    )

    try:
        INTEGRATION_TARGET_DIR.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _LOGGER.error("Cannot create integration dir %s: %s", INTEGRATION_TARGET_DIR, exc)
        return

    files_written = 0
    files_skipped = 0

    for src in INTEGRATION_SOURCE_DIR.iterdir():
        if not src.is_file():
            continue
        dst = INTEGRATION_TARGET_DIR / src.name
        if dst.exists() and _file_hash(src) == _file_hash(dst):
            files_skipped += 1
            continue
        try:
            shutil.copy2(str(src), str(dst))
            files_written += 1
        except OSError as exc:
            _LOGGER.error("Failed to copy %s → %s: %s", src, dst, exc)

    try:
        _VERSION_FILE.write_text(const.VERSION, encoding="utf-8")
    except OSError as exc:
        _LOGGER.warning("Failed to write version marker: %s", exc)

    _LOGGER.info(
        "Integration install complete: %d written, %d unchanged",
        files_written,
        files_skipped,
    )

    if files_written > 0:
        await _reload_integration()


async def _reload_integration() -> None:
    """Ask HA Core to reload the miniems integration via the Supervisor proxy."""
    token = os.environ.get("SUPERVISOR_TOKEN", "")
    if not token:
        _LOGGER.warning("No SUPERVISOR_TOKEN – cannot reload integration")
        return
    url = f"{const.HA_API_BASE}/config/config_entries/entry/reload"
    headers = {"Authorization": f"Bearer {token}"}
    try:
        async with aiohttp.ClientSession() as session:
            # Fetch the config entry id for the miniems domain
            async with session.get(
                f"{const.HA_API_BASE}/config/config_entries",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status != 200:
                    _LOGGER.warning("Could not fetch config entries (HTTP %s)", resp.status)
                    return
                entries = await resp.json()

            entry_id = next(
                (e["entry_id"] for e in entries if e.get("domain") == "miniems"), None
            )
            if not entry_id:
                _LOGGER.warning("miniems config entry not found – skipping reload")
                return

            async with session.post(
                f"{const.HA_API_BASE}/config/config_entries/{entry_id}/reload",
                headers=headers,
                timeout=aiohttp.ClientTimeout(total=10),
            ) as resp:
                if resp.status in (200, 204):
                    _LOGGER.info("Integration reloaded successfully (entry %s)", entry_id)
                else:
                    _LOGGER.warning("Integration reload returned HTTP %s", resp.status)
    except Exception as exc:
        _LOGGER.warning("Integration reload failed: %s", exc)
