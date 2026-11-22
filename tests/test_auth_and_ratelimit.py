"""
Unit tests for group authorization and rate limiting middleware.
"""

from unittest.mock import AsyncMock, MagicMock
from types import SimpleNamespace
import pytest
from aiogram.types import CallbackQuery, Message, User

from app.bot.middlewares.auth import ChatAuthMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware


@pytest.mark.asyncio
@pytest.mark.parametrize("event_type", [Message, CallbackQuery])
@pytest.mark.parametrize(
    ("chat_id", "chat_type", "allowed"),
    [(-100123, "group", True), (-100123, "supergroup", True),
     (-100456, "supergroup", False), (-100123, "channel", False), (123, "private", False)],
)
async def test_chat_auth_for_any_member(event_type, chat_id, chat_type, allowed):
    middleware = ChatAuthMiddleware(chat_id=-100123)
    handler = AsyncMock()
    event = MagicMock(spec=event_type)
    chat = SimpleNamespace(id=chat_id, type=chat_type)
    if event_type is Message:
        event.chat = chat
    else:
        event.message = SimpleNamespace(chat=chat)
    event.answer = AsyncMock()
    data = {"event_from_user": User(id=123, is_bot=False, first_name="Member")}

    await middleware(handler, event, data)

    if allowed:
        handler.assert_awaited_once_with(event, data)
    else:
        handler.assert_not_awaited()
    event.answer.assert_not_awaited()


async def test_inline_callback_without_chat_is_ignored():
    middleware = ChatAuthMiddleware(chat_id=-100123)
    event = MagicMock(spec=CallbackQuery, message=None)
    handler = AsyncMock()
    await middleware(handler, event, {})
    handler.assert_not_awaited()


@pytest.mark.asyncio
async def test_rate_limiting():
    middleware = RateLimitMiddleware(max_per_minute=2)
    handler = AsyncMock(return_value="ok")
    user = User(id=123, is_bot=False, first_name="User")
    event = MagicMock(spec=Message)
    event.answer = AsyncMock()
    data = {"event_from_user": user}

    # Request 1: allowed
    res1 = await middleware(handler, event, data)
    assert res1 == "ok"

    # Request 2: allowed
    res2 = await middleware(handler, event, data)
    assert res2 == "ok"

    # Request 3: blocked
    res3 = await middleware(handler, event, data)
    assert res3 is None
    event.answer.assert_awaited_once()
    assert "Превышен лимит запросов" in event.answer.call_args[0][0]
