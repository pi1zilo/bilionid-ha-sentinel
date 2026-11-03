"""
Main entry point for BilioniD HA Bot.
Initializes configuration, storage, monitoring collector, HA supervisor, and bot.
Handles clean shutdown on SIGINT/SIGTERM.
"""

import asyncio
import logging
import signal
import sys
from typing import Optional

from app.bot.bot import BotManager
from app.core.config import Settings, get_settings
from app.core.logger import setup_logger
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.monitoring.docker.client import DockerMonitor
from app.storage.db import Storage


async def run_app() -> None:
    settings = get_settings()
    logger = setup_logger(
        name="bilionid_ha",
        level=settings.LOG_LEVEL,
        json_format=settings.LOG_FORMAT_JSON,
    )

    logger.info(
        "Starting BilioniD HA Bot on node '%s' (Role: %s)",
        settings.NODE_NAME,
        settings.NODE_ROLE.value,
        extra={"node": settings.NODE_NAME, "role": settings.NODE_ROLE.value},
    )

    # 1. Initialize persistent storage
    storage = Storage(settings.db_path)
    await storage.init_db()

    # 2. Local Docker monitor
    docker_monitor = DockerMonitor(
        server_name=settings.NODE_NAME,
        socket_path=settings.DOCKER_SOCKET_PATH,
        enabled=settings.DOCKER_MONITOR_ENABLED,
    )

    # 3. Monitoring aggregator
    collector = MetricsCollector(settings)

    # Placeholders for cross-reference
    bot_manager_ref: list[Optional[BotManager]] = [None]

    # HA supervisor callbacks
    async def start_polling_callback() -> None:
        if bot_manager_ref[0]:
            await bot_manager_ref[0].start_polling()

    async def stop_polling_callback() -> None:
        if bot_manager_ref[0]:
            await bot_manager_ref[0].stop_polling()

    async def send_alert_callback(msg: str) -> None:
        if bot_manager_ref[0]:
            await bot_manager_ref[0].send_alert_message(msg)

    # 4. Initialize HA Supervisor
    supervisor = HASupervisor(
        settings=settings,
        storage=storage,
        start_bot_callback=start_polling_callback,
        stop_bot_callback=stop_polling_callback,
        send_alert_callback=send_alert_callback,
        docker_monitor=docker_monitor,
        get_local_metrics=lambda: collector.beszel.get_local_fallback_metrics(settings.NODE_NAME),
    )

    # 5. Initialize Telegram Bot Manager
    bot_manager = BotManager(
        settings=settings,
        storage=storage,
        collector=collector,
        ha_supervisor=supervisor,
    )
    bot_manager_ref[0] = bot_manager

    # 6. Start HA Supervisor (which starts polling if role is PRIMARY)
    await supervisor.start()

    # 7. Graceful shutdown handler
    stop_event = asyncio.Event()

    def request_shutdown(sig_name: str) -> None:
        logger.info("Received termination signal %s. Initiating graceful shutdown...", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, lambda s=s: request_shutdown(s.name))
    else:
        # Windows signal handling support
        pass

    try:
        await stop_event.wait()
    except (asyncio.CancelledError, KeyboardInterrupt):
        pass
    finally:
        logger.info("Shutting down BilioniD HA Bot components...")
        await supervisor.stop()
        await bot_manager.close()
        logger.info("Shutdown complete.")


def main() -> None:
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
