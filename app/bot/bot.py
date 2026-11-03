"""
Aiogram 3 Bot manager, router registration, dependency injection,
and resilient polling lifecycle management.
"""

import asyncio
import logging
from typing import Optional
from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramConflictError

from app.bot.handlers import admin, alerts, containers, ha, monitoring, start, status
from app.bot.middlewares.auth import OwnerAuthMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.storage.db import Storage

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

        if settings.TELEGRAM_BOT_TOKEN:
            self._init_bot()

    def _init_bot(self) -> None:
        self.bot = Bot(
            token=self.settings.TELEGRAM_BOT_TOKEN,
            default=DefaultBotProperties(parse_mode=ParseMode.MARKDOWN),
        )
        self.dp = Dispatcher()

        # Middlewares
        auth_middleware = OwnerAuthMiddleware(
            allowed_user_ids=self.settings.allowed_admin_ids,
            allowed_chat_id=self.settings.TELEGRAM_CHAT_ID,
            disable_auth=self.settings.DISABLE_AUTH,
        )
        self.dp.message.middleware(auth_middleware)
        self.dp.callback_query.middleware(auth_middleware)
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

    async def stop_polling(self) -> None:
        """Gracefully stops Telegram bot polling."""
        if not self._is_running:
            return

        self._is_running = False
        if self.dp:
            await self.dp.stop_polling()

        if self._polling_task:
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
        }

        while self._is_running:
            try:
                logger.info("Connecting to Telegram Bot API (getUpdates)...")
                await self.dp.start_polling(
                    self.bot,
                    **workflow_data,
                    handle_signals=False,
                    close_bot_session=False,
                )
            except TelegramConflictError as conflict_err:
                logger.critical(
                    "Telegram 409 Conflict: Another instance is polling getUpdates! Error: %s",
                    conflict_err,
                )
                self.ha_supervisor.handle_telegram_conflict_409()
                self._is_running = False
                break
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Unexpected error in bot polling loop: %s", e, exc_info=True)
                if not self._is_running:
                    break
                await asyncio.sleep(5.0)

    async def send_alert_message(self, message: str) -> None:
        """Sends an operational alert to the designated Telegram chat or admin user."""
        target_chat = self.settings.TELEGRAM_CHAT_ID or self.settings.primary_admin_id
        if not self.bot or not target_chat:
            logger.warning("Cannot send alert: bot or destination chat_id not configured.")
            return

        try:
            await self.bot.send_message(
                chat_id=target_chat,
                text=message,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception as e:
            logger.error("Failed to send Telegram alert to %s: %s", target_chat, e)

    async def close(self) -> None:
        """Closes bot session."""
        await self.stop_polling()
        if self.bot and self.bot.session:
            await self.bot.session.close()
            logger.info("Bot session closed")
