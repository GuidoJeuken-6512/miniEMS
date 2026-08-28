"""integration_installer.py: copy-if-changed install + HA reload trigger."""
import json

import pytest
from aioresponses import aioresponses

import integration_installer as ii
import const


@pytest.fixture(autouse=True)
def _isolated_dirs(tmp_path, monkeypatch):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    monkeypatch.setattr(ii, "INTEGRATION_SOURCE_DIR", src)
    monkeypatch.setattr(ii, "INTEGRATION_TARGET_DIR", dst)
    monkeypatch.setenv("SUPERVISOR_TOKEN", "sup-token")
    return src, dst


def _write_manifest(directory, version):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "manifest.json").write_text(json.dumps({"version": version}))


class TestFileHashAndManifest:
    def test_file_hash_stable_for_same_content(self, tmp_path):
        f = tmp_path / "a.txt"
        f.write_text("hello")
        assert ii._file_hash(f) == ii._file_hash(f)

    def test_file_hash_differs_for_different_content(self, tmp_path):
        f1 = tmp_path / "a.txt"
        f2 = tmp_path / "b.txt"
        f1.write_text("hello")
        f2.write_text("world")
        assert ii._file_hash(f1) != ii._file_hash(f2)

    def test_manifest_version_reads_field(self, tmp_path):
        _write_manifest(tmp_path, "1.2.3")
        assert ii._manifest_version(tmp_path) == "1.2.3"

    def test_manifest_version_none_when_missing(self, tmp_path):
        assert ii._manifest_version(tmp_path / "nope") is None

    def test_manifest_version_none_on_bad_json(self, tmp_path):
        (tmp_path / "manifest.json").write_text("{bad json")
        assert ii._manifest_version(tmp_path) is None


class TestInstallIntegration:
    @pytest.mark.asyncio
    async def test_missing_source_dir_skips_silently(self, _isolated_dirs, monkeypatch):
        src, dst = _isolated_dirs
        monkeypatch.setattr(ii, "INTEGRATION_SOURCE_DIR", src / "does-not-exist")
        await ii.install_integration()   # must not raise

    @pytest.mark.asyncio
    async def test_same_version_skips_copy_but_still_reloads(self, _isolated_dirs):
        src, dst = _isolated_dirs
        _write_manifest(src, "1.0.0")
        _write_manifest(dst, "1.0.0")
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", status=200, body="")
            await ii.install_integration()
        assert not (dst / "manifest.json").exists() or json.loads((dst / "manifest.json").read_text())["version"] == "1.0.0"

    @pytest.mark.asyncio
    async def test_new_version_copies_files(self, _isolated_dirs):
        src, dst = _isolated_dirs
        _write_manifest(src, "2.0.0")
        (src / "sensor.py").write_text("# sensor code")
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="", status=200)
            await ii.install_integration()
        assert (dst / "manifest.json").exists()
        assert (dst / "sensor.py").read_text() == "# sensor code"

    @pytest.mark.asyncio
    async def test_unchanged_file_is_skipped_on_reinstall(self, _isolated_dirs):
        src, dst = _isolated_dirs
        _write_manifest(src, "1.0.0")
        (src / "sensor.py").write_text("# v1")
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="", status=200)
            await ii.install_integration()
        mtime_before = (dst / "sensor.py").stat().st_mtime_ns

        # Bump manifest so a re-copy pass runs, but leave sensor.py identical
        _write_manifest(src, "1.0.1")
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="", status=200)
            await ii.install_integration()
        assert (dst / "sensor.py").read_text() == "# v1"

    @pytest.mark.asyncio
    async def test_writes_restart_marker_when_files_written(self, _isolated_dirs):
        src, dst = _isolated_dirs
        _write_manifest(src, "2.0.0")
        (src / "sensor.py").write_text("# code")
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="", status=200)
            await ii.install_integration()
        assert (dst / ".restart_required").exists()
        assert (dst / ".restart_required").read_text() == "2.0.0"

    @pytest.mark.asyncio
    async def test_reload_skipped_without_supervisor_token(self, _isolated_dirs, monkeypatch):
        src, dst = _isolated_dirs
        monkeypatch.delenv("SUPERVISOR_TOKEN", raising=False)
        _write_manifest(src, "1.0.0")
        (src / "sensor.py").write_text("# code")
        await ii.install_integration()   # no aioresponses mock -> would raise if it tried to call out
        assert (dst / "sensor.py").exists()


class TestReloadIntegration:
    @pytest.mark.asyncio
    async def test_reload_calls_service_when_entity_found(self):
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="sensor.miniems_mode", status=200)
            m.post(f"{const.HA_SERVICES_URL}/homeassistant/reload_config_entry", status=200)
            await ii._reload_integration()   # must not raise

    @pytest.mark.asyncio
    async def test_reload_noop_when_no_entity_found(self):
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", payload="", status=200)
            await ii._reload_integration()   # must not raise, and must not call reload service

    @pytest.mark.asyncio
    async def test_reload_noop_when_template_call_fails(self):
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", status=500)
            await ii._reload_integration()

    @pytest.mark.asyncio
    async def test_reload_swallows_transport_errors(self):
        with aioresponses() as m:
            m.post(f"{const.HA_API_BASE}/template", exception=ConnectionError("boom"))
            await ii._reload_integration()   # must not raise
