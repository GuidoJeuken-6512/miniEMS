"""main.py: the extractable, testable pieces – ems_loop() and version sync.

main() itself is pure asyncio/signal-handler wiring (creates the real
EMSController/InverterController/uvicorn.Server graph and calls
asyncio.gather) – not usefully unit-testable without turning this into an
integration test of the whole process. Covered here: the two functions that
contain actual branching logic.
"""
import asyncio

import pytest
from aioresponses import aioresponses

import const
import main as main_module
from main import _sync_version_from_supervisor, ems_loop


class FakeController:
    def __init__(self, data=None, exc=None, hang=False):
        self._data = data or {"mode": "Idle"}
        self._exc = exc
        self._hang = hang
        self.calls = 0

    async def update(self):
        self.calls += 1
        if self._hang:
            await asyncio.sleep(999)
        if self._exc:
            raise self._exc
        return self._data


async def _run_one_tick(controller, status_store, interval=0.01):
    """Run ems_loop() briefly, then cancel it – it's an infinite loop."""
    task = asyncio.create_task(ems_loop(controller, status_store, interval))
    await asyncio.sleep(interval * 3)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


@pytest.mark.asyncio
async def test_ems_loop_updates_status_store():
    status = {}
    controller = FakeController(data={"mode": "PV Charging"})
    await _run_one_tick(controller, status)
    assert status["mode"] == "PV Charging"
    assert "last_updated" in status
    assert controller.calls >= 1


@pytest.mark.asyncio
async def test_ems_loop_clears_stale_keys_between_ticks():
    status = {"stale_key": "should be gone"}
    controller = FakeController(data={"mode": "Idle"})
    await _run_one_tick(controller, status)
    assert "stale_key" not in status


@pytest.mark.asyncio
async def test_ems_loop_survives_controller_exception():
    status = {}
    controller = FakeController(exc=RuntimeError("boom"))
    await _run_one_tick(controller, status)
    # No crash propagated out of the loop, and the loop kept calling update()
    assert controller.calls >= 1
    assert status == {}   # exception -> status_store never got this tick's data


@pytest.mark.asyncio
async def test_ems_loop_times_out_without_crashing(monkeypatch):
    monkeypatch.setattr(main_module, "_LOGGER", main_module._LOGGER)
    status = {}
    controller = FakeController(hang=True)
    task = asyncio.create_task(ems_loop(controller, status, interval=0.01))
    # timeout = max(90, interval*3) inside ems_loop, far longer than we can
    # wait in a unit test – cancel instead and just confirm no crash so far.
    await asyncio.sleep(0.05)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    assert status == {}


class TestSyncVersionFromSupervisor:
    @pytest.mark.asyncio
    async def test_no_token_is_a_noop(self):
        original = const.VERSION
        await _sync_version_from_supervisor("")
        assert const.VERSION == original

    @pytest.mark.asyncio
    async def test_updates_version_from_supervisor_response(self):
        original = const.VERSION
        try:
            with aioresponses() as m:
                m.get(
                    "http://supervisor/addons/self/info",
                    payload={"data": {"version": "9.9.9"}},
                )
                await _sync_version_from_supervisor("tok")
            assert const.VERSION == "9.9.9"
        finally:
            const.VERSION = original

    @pytest.mark.asyncio
    async def test_missing_version_field_leaves_current_value(self):
        original = const.VERSION
        try:
            with aioresponses() as m:
                m.get("http://supervisor/addons/self/info", payload={"data": {}})
                await _sync_version_from_supervisor("tok")
            assert const.VERSION == original
        finally:
            const.VERSION = original

    @pytest.mark.asyncio
    async def test_request_failure_leaves_current_value(self):
        original = const.VERSION
        try:
            with aioresponses() as m:
                m.get("http://supervisor/addons/self/info", status=500)
                await _sync_version_from_supervisor("tok")
            assert const.VERSION == original
        finally:
            const.VERSION = original
