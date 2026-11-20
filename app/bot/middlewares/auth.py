"""
Authorization middleware restricting bot access exclusively to the infrastructure owner.
"""

import logging
from typing import Any, Awaitable, Callable, Dict
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("bilionid_ha.auth")


class OwnerAuthMiddleware(BaseMiddleware):
    def __init__(self, admin_user_id: int, chat_id: int = 0):
        self.admin_user_id = admin_user_id
        self.chat_id = chat_id

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return

        message = event.message if isinstance(event, CallbackQuery) else event
        chat = getattr(message, "chat", None)
        if self.chat_id and getattr(chat, "id", None) != self.chat_id:
            return

        if user.id != self.admin_user_id:
            logger.warning(
                "UNAUTHORIZED ACCESS ATTEMPT: user_id=%s, username=@%s",
                user.id,
                user.username,
            )
            denial_text = (
                "⛔ Доступ запрещен\n\n"
                "Этот бот работает в приватном режиме для мониторинга инфраструктуры владельца. "
                f"Ваш ID ({user.id}) не находится в списке разрешенных."
            )
            if isinstance(event, Message):
                await event.answer(denial_text, parse_mode=None)
            elif isinstance(event, CallbackQuery):
                await event.answer("⛔ Доступ запрещен", show_alert=True)
            return

        return await handler(event, data)
