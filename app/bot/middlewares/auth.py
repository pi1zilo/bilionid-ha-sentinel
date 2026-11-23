"""Allow commands and buttons only in the configured Telegram group."""

from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import BaseMiddleware
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery, Message, TelegramObject


class ChatAuthMiddleware(BaseMiddleware):
    def __init__(self, chat_id: int):
        self.chat_id = chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: Message | CallbackQuery,
        data: dict[str, Any],
    ) -> Any:
        message = event.message if isinstance(event, CallbackQuery) else event
        if self.chat_id >= 0 or message is None:
            return

        chat = message.chat
        if chat.id == self.chat_id and chat.type in (ChatType.GROUP, ChatType.SUPERGROUP):
            return await handler(event, data)
