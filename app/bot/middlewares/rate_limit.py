"""
Sliding-window rate limiting middleware preventing command flooding.
"""

import time
import logging
from typing import Any, Awaitable, Callable, Dict, List
from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject

logger = logging.getLogger("bilionid_ha.ratelimit")


class RateLimitMiddleware(BaseMiddleware):
    def __init__(self, max_per_minute: int = 30):
        self.max_per_minute = max_per_minute
        self.user_requests: Dict[int, List[float]] = {}

    async def __call__(
        self,
        handler: Callable[[TelegramObject, Dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: Dict[str, Any],
    ) -> Any:
        user = data.get("event_from_user")
        if not user:
            return await handler(event, data)

        now = time.time()
        window_start = now - 60.0

        # Clean timestamps older than 60s
        timestamps = self.user_requests.setdefault(user.id, [])
        self.user_requests[user.id] = [t for t in timestamps if t > window_start]

        if len(self.user_requests[user.id]) >= self.max_per_minute:
            logger.warning("Rate limit exceeded for user %s", user.id)
            warn_text = "⚠️ **Превышен лимит запросов**\nПожалуйста, подождите несколько секунд перед следующим действием."
            if isinstance(event, Message):
                await event.answer(warn_text, parse_mode="Markdown")
            elif isinstance(event, CallbackQuery):
                await event.answer("⚠️ Слишком много запросов. Подождите.", show_alert=True)
            return

        self.user_requests[user.id].append(now)
        return await handler(event, data)
