"""
Aiogram 3 Bot manager, router registration, dependency injection,
and resilient polling lifecycle management.
"""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError
from aiogram.methods import GetUpdates

from app.bot.handlers import admin, alerts, containers, ha, monitoring, start, status
from app.bot.middlewares.auth import ChatAuthMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.storage.db import Storage
from app.monitoring.zabbix import ZabbixClient

logger = logging.getLogger("bilionid_ha.bot")


class BotManager:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        collector: MetricsCollector,
        ha_supervisor: HASupervisor,
    ):
        self.settings = settings
        self.storage = storage
        self.collector = collector
        self.ha_supervisor = ha_supervisor

        self.bot: Optional[Bot] = None
        self.dp: Optional[Dispatcher] = None
        self._polling_task: Optional[asyncio.Task] = None
        self._is_running = False
        self.last_poll_ok: datetime | None = None
        self.last_poll_error: str | None = None
        self._conflict_stop: asyncio.Task | None = None

        if settings.TELEGRAM_BOT_TOKEN:
            self._init_bot()

    def _init_bot(self) -> None:
        session = None
        if self.settings.TELEGRAM_API_BASE:
            session = AiohttpSession(api=TelegramAPIServer.from_base(self.settings.TELEGRAM_API_BASE))
        self.bot = Bot(
            token=self.settings.TELEGRAM_BOT_TOKEN,
            session=session,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        )
        self.bot.session.middleware(self._track_poll)
        self.dp = Dispatcher()

        # Middlewares
        chat_auth = ChatAuthMiddleware(self.settings.command_chat_id)
        self.dp.message.outer_middleware(chat_auth)
        self.dp.callback_query.outer_middleware(chat_auth)
        self.dp.message.middleware(RateLimitMiddleware(self.settings.RATE_LIMIT_COMMANDS_PER_MIN))
        self.dp.callback_query.middleware(RateLimitMiddleware(self.settings.RATE_LIMIT_COMMANDS_PER_MIN))

        # Register routers
        self.dp.include_router(start.router)
        self.dp.include_router(status.router)
        self.dp.include_router(ha.router)
        self.dp.include_router(containers.router)
        self.dp.include_router(monitoring.router)
        self.dp.include_router(alerts.router)
        self.dp.include_router(admin.router)

    async def start_polling(self) -> None:
        """Starts Telegram bot polling in a resilient background task."""
        if not self.bot or not self.dp:
            logger.warning("Bot token not configured. Polling not started.")
            return

        if self._is_running:
            return

        self._is_running = True
        self._polling_task = asyncio.create_task(self._run_polling())
        logger.info("Bot polling task initiated on %s", self.settings.NODE_NAME)

    @property
    def is_polling(self) -> bool:
        return bool(self._polling_task and not self._polling_task.done() and self._is_running)

    @property
    def polling_ready(self) -> bool:
        if not self.is_polling or self.last_poll_ok is None or self.last_poll_error:
            return False
        return (datetime.now(timezone.utc) - self.last_poll_ok).total_seconds() < 45

    async def _track_poll(self, make_request, bot, method):
        if not isinstance(method, GetUpdates):
            return await make_request(bot, method)
        try:
            result = await make_request(bot, method)
        except TelegramConflictError:
            logger.error("Telegram polling conflict; operator intervention required")
            self.last_poll_error = "TelegramConflictError"
            if not self._conflict_stop or self._conflict_stop.done():
                self._conflict_stop = asyncio.create_task(self.stop_polling())
            raise
        except Exception as exc:
            self.last_poll_error = type(exc).__name__
            raise
        self.last_poll_ok = datetime.now(timezone.utc)
        self.last_poll_error = None
        return result

    async def stop_polling(self) -> None:
        """Gracefully stops Telegram bot polling."""
        if not self._is_running:
            return

        self._is_running = False
        if self._polling_task:
            if self.dp and not self._polling_task.done():
                try:
                    await self.dp.stop_polling()
                except RuntimeError:
                    self._polling_task.cancel()
            try:
                await self._polling_task
            except asyncio.CancelledError:
                pass
            self._polling_task = None

        logger.info("Bot polling stopped cleanly on %s", self.settings.NODE_NAME)

    async def _run_polling(self) -> None:
        """Background runner with Telegram 409 conflict detection."""
        if not self.bot or not self.dp:
            return

        # Provide context dependencies to handlers
        workflow_data = {
            "settings": self.settings,
            "storage": self.storage,
            "collector": self.collector,
            "ha_supervisor": self.ha_supervisor,
            "zabbix": ZabbixClient(self.settings.ZABBIX_URL, self.settings.ZABBIX_API_TOKEN),
        }

        while self._is_running:
            try:
                logger.info("Connecting to Telegram Bot API (getUpdates)...")
                await self.dp.start_polling(
                    self.bot,
                    **workflow_data,
                    handle_signals=False,
                    close_bot_session=False,
                    polling_timeout=15,
                )
            except asyncio.CancelledError:
                break
            except Exception as e:
                self.last_poll_error = type(e).__name__
                logger.error("Telegram polling error: %s", self.last_poll_error)
                if not self._is_running:
                    break
                await asyncio.sleep(5.0)

    async def send_alert_message(self, message: str) -> None:
        """Sends an operational alert to the designated Telegram chat."""
        if not self.bot or self.settings.TELEGRAM_CHAT_ID >= 0:
            raise RuntimeError("Telegram delivery is not configured")
        await self.bot.send_message(chat_id=self.settings.TELEGRAM_CHAT_ID, text=message, parse_mode=None)

    async def close(self) -> None:
        """Closes bot session."""
        await self.stop_polling()
        if self._conflict_stop and not self._conflict_stop.done():
            await self._conflict_stop
        if self.bot and self.bot.session:
            await self.bot.session.close()
            logger.info("Bot session closed")
