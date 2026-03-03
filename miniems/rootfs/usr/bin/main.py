"""miniEMS – asyncio entry point."""
import asyncio
import logging
import os
import signal
import sys

import uvicorn

from config_loader import load_config
from cost_optimizer import CostOptimizer
from ems_controller import EMSController
from ha_ws_client import HAWebSocketClient
from web_server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger("miniems.main")


async def ems_loop(controller: EMSController, status_store: dict, interval: int) -> None:
    """Run EMS decision loop on a fixed interval."""
    while True:
        try:
            data = controller.update()
            status_store.clear()
            status_store.update(data)
        except Exception as exc:
            _LOGGER.error("EMS loop error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


async def main() -> None:
    cfg = load_config()

    status_store: dict = {}

    # Build component graph
    cost_optimizer = CostOptimizer(cfg)

    async def on_state_change(entity_id: str, new_state: dict) -> None:
        # Lightweight callback – EMS runs on its own timed loop
        pass

    ws_client = HAWebSocketClient(cfg.monitored_entities, on_state_change, cfg.long_lived_token)
    controller = EMSController(cfg, ws_client, cost_optimizer)
    app = create_app(status_store)

    # Uvicorn config (ingress port 8080)
    uvi_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
        access_log=False,
    )
    uvi_server = uvicorn.Server(uvi_config)

    _LOGGER.info("miniEMS starting up…")

    async def _ems_task() -> None:
        _LOGGER.info("Waiting for HA WebSocket initial states…")
        await ws_client.wait_ready()
        _LOGGER.info("HA WebSocket ready – starting EMS loop")
        await ems_loop(controller, status_store, cfg.update_interval_sec)

    # Run all tasks concurrently
    tasks = [
        asyncio.create_task(ws_client.run(), name="ws_client"),
        asyncio.create_task(_ems_task(), name="ems_loop"),
        asyncio.create_task(uvi_server.serve(), name="web_server"),
    ]

    # Graceful shutdown on SIGTERM / SIGINT
    loop = asyncio.get_running_loop()

    def _shutdown() -> None:
        _LOGGER.info("Shutdown signal received")
        for t in tasks:
            t.cancel()
        uvi_server.should_exit = True

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)

    try:
        await asyncio.gather(*tasks, return_exceptions=True)
    finally:
        _LOGGER.info("miniEMS stopped")


if __name__ == "__main__":
    asyncio.run(main())
