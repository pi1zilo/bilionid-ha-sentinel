"""
Unit tests for owner whitelist authorization and rate limiting middleware.
"""

from unittest.mock import AsyncMock, MagicMock
import pytest
from aiogram.types import Message, User

from app.bot.middlewares.auth import OwnerAuthMiddleware
from app.bot.middlewares.rate_limit import RateLimitMiddleware


@pytest.mark.asyncio
async def test_owner_auth_allowed():
    admin_id = 123456789
    middleware = OwnerAuthMiddleware(admin_user_id=admin_id)

    handler = AsyncMock(return_value="success")
    user = User(id=admin_id, is_bot=False, first_name="Owner", username="owner")
    event = MagicMock(spec=Message)
    data = {"event_from_user": user}

    result = await middleware(handler, event, data)
    assert result == "success"
    handler.assert_awaited_once_with(event, data)


@pytest.mark.asyncio
async def test_multiple_admins_allowed():
    middleware = OwnerAuthMiddleware(allowed_user_ids="123456789,987654321")

    handler = AsyncMock(return_value="success")
    friend = User(id=987654321, is_bot=False, first_name="Friend", username="friend")
    event = MagicMock(spec=Message)
    data = {"event_from_user": friend}

    result = await middleware(handler, event, data)
    assert result == "success"
    handler.assert_awaited_once_with(event, data)



@pytest.mark.asyncio
async def test_chat_auth_allowed():
    # Anyone in the authorized chat is allowed
    target_chat_id = -100123456789
    middleware = OwnerAuthMiddleware(
        allowed_user_ids=[111],
        allowed_chat_id=target_chat_id,
    )

    handler = AsyncMock(return_value="chat_success")
    any_user = User(id=9999, is_bot=False, first_name="Stranger")
    from aiogram.types import Chat
    chat = Chat(id=target_chat_id, type="supergroup")
    event = MagicMock(spec=Message)
    data = {"event_from_user": any_user, "event_chat": chat}

    result = await middleware(handler, event, data)
    assert result == "chat_success"
    handler.assert_awaited_once_with(event, data)


@pytest.mark.asyncio
async def test_disable_auth_allows_all():
    middleware = OwnerAuthMiddleware(disable_auth=True)

    handler = AsyncMock(return_value="all_allowed")
    user = User(id=8888, is_bot=False, first_name="Random")
    event = MagicMock(spec=Message)
    data = {"event_from_user": user}

    result = await middleware(handler, event, data)
    assert result == "all_allowed"
    handler.assert_awaited_once_with(event, data)


@pytest.mark.asyncio
async def test_owner_auth_denied():
    admin_id = 123456789
    intruder_id = 999999999
    middleware = OwnerAuthMiddleware(admin_user_id=admin_id)

    handler = AsyncMock()
    user = User(id=intruder_id, is_bot=False, first_name="Stranger", username="stranger")
    event = MagicMock(spec=Message)
    event.answer = AsyncMock()
    data = {"event_from_user": user}

    result = await middleware(handler, event, data)
    assert result is None
    handler.assert_not_awaited()
    event.answer.assert_awaited_once()
    assert "Доступ запрещен" in event.answer.call_args[0][0]


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
