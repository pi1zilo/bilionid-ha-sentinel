"""
Tests for enhanced reliability, container formatting, safe message editing,
and session lifecycle management.
"""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
import pytest
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import CallbackQuery, Message

from app.bot.bot import BotManager, DEFAULT_BOT_COMMANDS
from app.bot.formatters import format_containers_view
from app.bot.handlers import safe_edit_message
from app.core.config import NodeRole, Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.monitoring.models import ContainerInfo
from app.storage.db import Storage


@pytest.fixture
def dummy_settings(tmp_path: Path) -> Settings:
    return Settings(
        NODE_ROLE=NodeRole.PRIMARY,
        NODE_NAME="test-node",
        DATA_DIR=tmp_path,
        TELEGRAM_BOT_TOKEN="12345:dummy",
    )


@pytest.mark.asyncio
async def test_safe_edit_message_normal():
    cb = MagicMock(spec=CallbackQuery)
    cb.data = "nav_test"
    cb.message = MagicMock(spec=Message)
    cb.message.edit_text = AsyncMock()

    await safe_edit_message(cb, "New text", reply_markup=None)
    cb.message.edit_text.assert_awaited_once_with(
        "New text", reply_markup=None, parse_mode="Markdown"
    )


@pytest.mark.asyncio
async def test_safe_edit_message_suppresses_not_modified():
    cb = MagicMock(spec=CallbackQuery)
    cb.data = "nav_test"
    cb.message = MagicMock(spec=Message)
    # Simulate Telegram BadRequest: message is not modified
    cb.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: message is not modified: specified new message content and reply markup are exactly the same",
        )
    )

    # Must NOT raise exception!
    await safe_edit_message(cb, "Identical text", reply_markup=None)
    cb.message.edit_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_safe_edit_message_reraises_other_errors():
    cb = MagicMock(spec=CallbackQuery)
    cb.data = "nav_test"
    cb.message = MagicMock(spec=Message)
    cb.message.edit_text = AsyncMock(
        side_effect=TelegramBadRequest(
            method=MagicMock(),
            message="Bad Request: chat not found",
        )
    )

    with pytest.raises(TelegramBadRequest, match="chat not found"):
        await safe_edit_message(cb, "Any text", reply_markup=None)


def test_format_containers_view_markdown_safety_and_truncation():
    # Containers with underscores in names and images
    containers = [
        ContainerInfo(
            server="VPS-1",
            id=f"id_{i}",
            name=f"web_app_worker_{i}",
            image=f"custom_repo/my_app_service:tag_v_{i}",
            state="running",
            status="Up 2 hours",
            health="healthy",
        )
        for i in range(50)
    ]

    # Format with small max_length to trigger truncation
    result = format_containers_view({"VPS-1": containers}, max_length=500)

    # 1. Image and name must be in backticks to prevent unescaped underscores from breaking Markdown v1
    assert "`web_app_worker_0` (`custom_repo/my_app_service:tag_v_0`)" in result
    # 2. Must contain truncation indicator
    assert "и ещё" in result
    # 3. Output length must stay bounded
    assert len(result) < 1500


@pytest.mark.asyncio
async def test_supervisor_session_lifecycle(dummy_settings: Settings):
    storage = Storage(dummy_settings.db_path)
    supervisor = HASupervisor(settings=dummy_settings, storage=storage)

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock):

        await supervisor.start()
        assert supervisor._session is not None
        assert not supervisor._session.closed

        await supervisor.stop()
        assert supervisor._session is None


@pytest.mark.asyncio
async def test_setup_commands_success(dummy_settings: Settings):
    storage = Storage(dummy_settings.db_path)
    collector = MagicMock(spec=MetricsCollector)
    ha_supervisor = MagicMock(spec=HASupervisor)

    bot_manager = BotManager(
        settings=dummy_settings,
        storage=storage,
        collector=collector,
        ha_supervisor=ha_supervisor,
    )

    assert bot_manager.bot is not None
    bot_manager.bot.set_my_commands = AsyncMock(return_value=True)
    bot_manager.bot.set_chat_menu_button = AsyncMock(return_value=True)

    result = await bot_manager.setup_commands()
    assert result is True

    bot_manager.bot.set_my_commands.assert_awaited_once()
    call_args = bot_manager.bot.set_my_commands.await_args
    assert call_args.kwargs["commands"] == DEFAULT_BOT_COMMANDS

    bot_manager.bot.set_chat_menu_button.assert_awaited_once()


@pytest.mark.asyncio
async def test_setup_commands_no_bot(tmp_path: Path):
    no_token_settings = Settings(
        NODE_ROLE=NodeRole.PRIMARY,
        NODE_NAME="test-node",
        DATA_DIR=tmp_path,
        TELEGRAM_BOT_TOKEN="",
    )
    bot_manager = BotManager(
        settings=no_token_settings,
        storage=MagicMock(),
        collector=MagicMock(),
        ha_supervisor=MagicMock(),
    )
    assert bot_manager.bot is None
    result = await bot_manager.setup_commands()
    assert result is False


@pytest.mark.asyncio
async def test_setup_commands_handles_exception(dummy_settings: Settings):
    bot_manager = BotManager(
        settings=dummy_settings,
        storage=MagicMock(),
        collector=MagicMock(),
        ha_supervisor=MagicMock(),
    )
    assert bot_manager.bot is not None
    bot_manager.bot.set_my_commands = AsyncMock(side_effect=Exception("Telegram connection failed"))

    result = await bot_manager.setup_commands()
    assert result is False
