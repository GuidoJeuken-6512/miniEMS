"""miniEMS – asyncio entry point."""
import asyncio
import logging
import os
import signal
import sys

import uvicorn

from config_loader import load_config
from consumption_model import ConsumptionModel
from cost_optimizer import CostOptimizer
from ems_controller import EMSController
from event_log import EventLog
from ha_sensor_publisher import HASensorPublisher
from ha_ws_client import HAWebSocketClient
from inverter_controller import InverterController
from mqtt_publisher import MQTTPublisher
from solcast_client import SolcastClient
from store import EnergyStore
from weather_client import WeatherClient
from web_server import create_app

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s – %(message)s",
    stream=sys.stdout,
)
_LOGGER = logging.getLogger("miniems.main")


async def ems_loop(
    controller: EMSController,
    status_store: dict,
    interval: int,
    mqtt: MQTTPublisher,
    rest: HASensorPublisher,
) -> None:
    """Run EMS decision loop on a fixed interval."""
    while True:
        try:
            data = await controller.update()
            status_store.clear()
            status_store.update(data)
            # Prefer MQTT (has unique_id); fall back to REST
            if mqtt.available:
                await mqtt.publish(data)
            else:
                await rest.publish(data)
        except Exception as exc:
            _LOGGER.error("EMS loop error: %s", exc, exc_info=True)
        await asyncio.sleep(interval)


async def main() -> None:
    cfg = load_config()

    # Open SQLite store and restore today's accumulators
    store = EnergyStore()
    await store.open()

    status_store: dict = {}

    supervisor_token = os.environ.get("SUPERVISOR_TOKEN", "")

    cost_optimizer = CostOptimizer(cfg, store)
    await cost_optimizer.restore_today()

    async def on_state_change(entity_id: str, new_state: dict) -> None:
        pass

    ws_client = HAWebSocketClient(cfg.monitored_entities, on_state_change, cfg.long_lived_token)

    inverter = InverterController(cfg, supervisor_token, cfg.long_lived_token)
    if cfg.battery_control_enabled:
        sim_txt = " (SIMULATION)" if cfg.battery_control_simulation else ""
        _LOGGER.info("Battery control enabled%s", sim_txt)

    weather_client = WeatherClient(cfg.weather_entity, supervisor_token)
    if weather_client.enabled:
        _LOGGER.info("Weather forecast enabled (%s)", cfg.weather_entity)
    consumption_model = ConsumptionModel(cfg, store, weather_client if weather_client.enabled else None)

    solcast_client = SolcastClient(cfg, ws_client)
    event_log = EventLog(max_entries=100, store=store)
    await event_log.restore_from_db()

    controller = EMSController(
        cfg, ws_client, cost_optimizer, inverter, consumption_model,
        solcast=solcast_client,
        event_log=event_log,
    )
    app = create_app(status_store, cfg, supervisor_token, store)

    # Publisher setup: try MQTT, fall back to REST
    mqtt_publisher = MQTTPublisher(supervisor_token)
    rest_publisher = HASensorPublisher(supervisor_token, cfg.long_lived_token)
    await mqtt_publisher.setup()

    # Uvicorn config (ingress port 8080)
    uvi_config = uvicorn.Config(
        app,
        host="0.0.0.0",
        port=8080,
        log_level="warning",
        access_log=False,
    )
    uvi_server = uvicorn.Server(uvi_config)

    _LOGGER.info("miniEMS starting up… MQTT=%s", mqtt_publisher.available)

    async def _ems_task() -> None:
        _LOGGER.info("Waiting for HA WebSocket initial states…")
        await ws_client.wait_ready()
        _LOGGER.info("HA WebSocket ready – starting EMS loop")
        await ems_loop(controller, status_store, cfg.update_interval_sec, mqtt_publisher, rest_publisher)

    tasks = [
        asyncio.create_task(ws_client.run(), name="ws_client"),
        asyncio.create_task(_ems_task(), name="ems_loop"),
        asyncio.create_task(uvi_server.serve(), name="web_server"),
    ]

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
        await store.close()
        _LOGGER.info("miniEMS stopped")


if __name__ == "__main__":
    asyncio.run(main())
