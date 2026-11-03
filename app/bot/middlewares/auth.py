"""
Authorization middleware restricting bot access exclusively to authorized infrastructure admins.
"""

import logging
from typing import Any, Awaitable, Callable, Dict, List, Optional, Set, Union
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("bilionid_ha.auth")


class OwnerAuthMiddleware(BaseMiddleware):
    def __init__(
        self,
        allowed_user_ids: Any = None,
        admin_user_id: Any = None,
        allowed_chat_id: Optional[int] = None,
        disable_auth: bool = False,
    ):
        self.allowed_chat_id = allowed_chat_id if allowed_chat_id != 0 else None
        self.disable_auth = disable_auth
        self.allowed_ids: Set[int] = set()

        target = allowed_user_ids if allowed_user_ids is not None else admin_user_id
        if str(target).strip() in ("*", "all"):
            self.disable_auth = True
        elif isinstance(target, (list, set)):
            self.allowed_ids = {int(x) for x in target if str(x).strip().isdigit() and int(str(x).strip()) != 0}
        elif isinstance(target, int):
            if target != 0:
                self.allowed_ids = {target}
        elif target:
            import re
            self.allowed_ids = {
                int(x)
                for x in re.split(r"[,;|\s]+", str(target).strip())
                if x.isdigit() and int(x) != 0
            }

    def _matches_allowed_chat(self, chat_id: Optional[int]) -> bool:
        if not chat_id or not self.allowed_chat_id:
            return False
        if chat_id == self.allowed_chat_id:
            return True

        def _clean(s: str) -> str:
            s = s.strip()
            if s.startswith("-100"):
                return s[4:]
            if s.startswith("-"):
                return s[1:]
            return s

        return _clean(str(chat_id)) == _clean(str(self.allowed_chat_id))

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        # 1. If auth check is disabled, allow all requests
        if self.disable_auth:
            return await handler(event, data)

        chat = data.get("event_chat")
        user = data.get("event_from_user")

        chat_id: Optional[int] = None
        event_chat = getattr(event, "chat", None)
        event_msg = getattr(event, "message", None)

        if event_chat and hasattr(event_chat, "id"):
            chat_id = event_chat.id
        elif event_msg and getattr(event_msg, "chat", None) and hasattr(event_msg.chat, "id"):
            chat_id = event_msg.chat.id
        elif chat and hasattr(chat, "id"):
            chat_id = chat.id

        # 2. CHAT-BASED ACCESS (Primary):
        # If message/query comes from the authorized group or chat, ALLOW EVERYONE in it!
        if self._matches_allowed_chat(chat_id):
            return await handler(event, data)

        # 3. USER-BASED ACCESS (Optional, for private DMs if admin IDs configured):
        if self.allowed_ids and user and user.id in self.allowed_ids:
            return await handler(event, data)

        # 4. If neither chat nor admin IDs are set, allow all
        if not self.allowed_chat_id and not self.allowed_ids:
            return await handler(event, data)

        user_id = user.id if user else "Unknown"
        logger.warning(
            "UNAUTHORIZED ACCESS ATTEMPT: user_id=%s, chat_id=%s",
            user_id,
            chat_id,
        )

        if self.allowed_chat_id:
            denial_text = (
                "🔒 **Бот работает только в рабочей группе**\n\n"
                f"Этот бот настроен на работу исключительно в чате ID `{self.allowed_chat_id}`.\n"
                "Все команды и кнопки мониторинга доступны всем участникам этого чата."
            )
        else:
            denial_text = (
                "⛔ **Доступ запрещен**\n\n"
                f"Ваш Telegram ID (`{user_id}`) не находится в списке разрешенных."
            )

        if isinstance(event, Message):
            await event.answer(denial_text, parse_mode="Markdown")
        elif isinstance(event, CallbackQuery):
            await event.answer("🔒 Бот работает только в рабочей группе", show_alert=True)
        return
