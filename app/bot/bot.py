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
from aiogram.types import BotCommand, BotCommandScopeDefault, MenuButtonCommands

from app.bot.handlers import admin, alerts, containers, ha, monitoring, start, status
from app.bot.middlewares.auth import OwnerAuthMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware
from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.alert_watcher import AlertWatcher
from app.monitoring.collector import MetricsCollector
from app.storage.db import Storage

logger = logging.getLogger("bilionid_ha.bot")

DEFAULT_BOT_COMMANDS: list[BotCommand] = [
    BotCommand(command="start", description="Главное меню с кнопками"),
    BotCommand(command="status", description="Общий статус всей инфраструктуры"),
    BotCommand(command="servers", description="Список и состояние серверов"),
    BotCommand(command="containers", description="Docker-контейнеры на обоих VPS"),
    BotCommand(command="ha", description="Состояние отказоустойчивости (HA)"),
    BotCommand(command="beszel", description="Статус Beszel Hubs"),
    BotCommand(command="uptime", description="Мониторы Uptime Kuma"),
    BotCommand(command="alerts", description="Журнал инцидентов и аварий"),
    BotCommand(command="refresh", description="Принудительное обновление данных"),
    BotCommand(command="help", description="Справка по всем командам"),
]


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
        session = None
        if self.settings.TELEGRAM_PROXY_URL:
            from aiogram.client.session.aiohttp import AiohttpSession
            session = AiohttpSession(proxy=self.settings.TELEGRAM_PROXY_URL)

        self.bot = Bot(
            token=self.settings.TELEGRAM_BOT_TOKEN,
            session=session,
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
        for r in (
            start.router,
            status.router,
            ha.router,
            containers.router,
            monitoring.router,
            alerts.router,
            admin.router,
        ):
            r._parent_router = None
            self.dp.include_router(r)

        # Proactive background alert watcher (runs only on active leader)
        self.alert_watcher = AlertWatcher(
            settings=self.settings,
            collector=self.collector,
            notify_callback=self.send_alert_message,
            interval_sec=15.0,
        )

    async def start_polling(self) -> None:
        """Starts Telegram bot polling in a resilient background task."""
        if not self.bot or not self.dp:
            logger.warning("Bot token not configured. Polling not started.")
            return

        if self._is_running:
            return

        self._is_running = True
        self._polling_task = asyncio.create_task(self._run_polling())
        self.alert_watcher.start()
        logger.info("Bot polling task and AlertWatcher initiated on %s", self.settings.NODE_NAME)

    async def stop_polling(self) -> None:
        """Gracefully stops Telegram bot polling."""
        if not self._is_running:
            return

        self._is_running = False
        await self.alert_watcher.stop()
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

    async def setup_commands(self) -> bool:
        """
        Registers bot commands with Telegram API and ensures the Menu button
        is displayed next to the message input field.
        """
        if not self.bot:
            return False

        try:
            await self.bot.set_my_commands(
                commands=DEFAULT_BOT_COMMANDS,
                scope=BotCommandScopeDefault(),
            )
            await self.bot.set_chat_menu_button(
                menu_button=MenuButtonCommands(),
            )
            logger.info("Telegram bot commands and Menu button registered successfully.")
            return True
        except Exception as e:
            logger.warning("Failed to configure Telegram bot commands: %s", e)
            return False

    async def _run_polling(self) -> None:
        """Background runner with Telegram 409 conflict detection."""
        if not self.bot or not self.dp:
            return

        # Configure commands in Telegram menu button (left of the input field)
        await self.setup_commands()

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
            logger.warning("Markdown send failed (%s), retrying as plain text...", e)
            try:
                await self.bot.send_message(
                    chat_id=target_chat,
                    text=message,
                    parse_mode=None,
                )
            except Exception as e2:
                logger.error("Failed to send Telegram alert to %s: %s", target_chat, e2)

    async def close(self) -> None:
        """Closes bot session."""
        await self.stop_polling()
        await self.alert_watcher.stop()
        if self.bot and self.bot.session:
            await self.bot.session.close()
            logger.info("Bot session closed")
