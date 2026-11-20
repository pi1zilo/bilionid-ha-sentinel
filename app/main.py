"""
Main entry point for BilioniD HA Bot.
Initializes configuration, storage, monitoring collector, HA supervisor, and bot.
Handles clean shutdown on SIGINT/SIGTERM.
"""

import asyncio
import signal
import sys

from app.bot.bot import BotManager
from app.core.config import get_settings
from app.core.logger import setup_logger
from app.ha.supervisor import HASupervisor
from app.ha.http_server import HAHttpServer
from app.monitoring.collector import MetricsCollector
from app.monitoring.docker.client import DockerMonitor
from app.storage.db import Storage
from app.services.monitoring import MonitoringService


async def run_app() -> None:
    settings = get_settings()
    settings.validate_runtime()
    logger = setup_logger(
        name="bilionid_ha",
        level=settings.LOG_LEVEL,
        json_format=settings.LOG_FORMAT_JSON,
        secrets=(
            settings.TELEGRAM_BOT_TOKEN, settings.HA_SECRET_KEY,
            settings.BESZEL_PASSWORD, settings.BESZEL_AUTH_TOKEN,
            settings.BESZEL_BACKUP_PASSWORD, settings.BESZEL_BACKUP_AUTH_TOKEN,
            settings.UPTIME_KUMA_PASSWORD, settings.UPTIME_KUMA_API_KEY,
            settings.UPTIME_KUMA_WEBHOOK_SECRET, settings.ZABBIX_API_TOKEN,
            settings.ZABBIX_WEBHOOK_SECRET,
        ),
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
        proxy_url=settings.DOCKER_PROXY_URL,
    )

    # 3. Monitoring aggregator
    collector = MetricsCollector(settings, docker_monitor)
    monitoring = MonitoringService(settings, storage, collector)
    supervisor = HASupervisor(settings, monitoring)
    http_server = HAHttpServer(settings, supervisor, monitoring, docker_monitor)
    bot_manager = BotManager(
        settings=settings,
        storage=storage,
        collector=collector,
        ha_supervisor=supervisor,
    )
    def bot_status() -> dict:
        stamp = bot_manager.last_poll_ok
        return {
            "is_polling": bot_manager.is_polling,
            "polling_ready": bot_manager.polling_ready,
            "last_poll_ok": stamp.isoformat() if stamp else None,
            "last_poll_error": bot_manager.last_poll_error,
        }

    async def send_queued_alert(message: str) -> None:
        if settings.polling_enabled and bot_manager.last_poll_error == "TelegramConflictError":
            raise RuntimeError("Operator must resolve the Telegram polling conflict")
        await bot_manager.send_alert_message(message)

    supervisor.bot_status = bot_status
    tasks = []
    stop_event = asyncio.Event()

    def request_shutdown(sig_name: str) -> None:
        logger.info("Received termination signal %s. Initiating graceful shutdown...", sig_name)
        stop_event.set()

    loop = asyncio.get_running_loop()
    if sys.platform != "win32":
        for s in (signal.SIGTERM, signal.SIGINT):
            loop.add_signal_handler(s, lambda s=s: request_shutdown(s.name))
    try:
        await http_server.start()
        await storage.record_ha_event(
            "POLLING_ENABLED" if settings.polling_enabled else "STANDBY_STARTUP",
            settings.NODE_NAME,
        )
        if settings.polling_enabled:
            await bot_manager.start_polling()
            tasks.extend(monitoring.start_checks())
        if settings.HA_PEER_ENABLED:
            tasks.append(asyncio.create_task(supervisor.run()))
        if settings.polling_enabled or settings.HA_WATCH_PRIMARY:
            tasks.append(asyncio.create_task(monitoring.deliver(send_queued_alert)))
        await stop_event.wait()
    finally:
        logger.info("Shutting down BilioniD HA Bot components...")
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        await http_server.stop()
        await bot_manager.close()
        logger.info("Shutdown complete.")


def main() -> None:
    try:
        asyncio.run(run_app())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
