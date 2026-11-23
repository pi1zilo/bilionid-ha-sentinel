import asyncio
from datetime import datetime, timezone
import sys
from unittest.mock import AsyncMock

from aiohttp.test_utils import TestClient, TestServer
from aiogram.methods import AnswerCallbackQuery, EditMessageText, SendDocument, SendMessage, SetMyCommands
from aiogram.types import CallbackQuery, Chat, Message, Update, User
import pytest

from app.bot.bot import BotManager
from app.core.config import NodeRole, Settings
from app.ha.http_server import HAHttpServer
from app.ha.supervisor import HASupervisor
from app.monitoring.collector import MetricsCollector
from app.monitoring.server_logs import MAX_JOURNAL_BYTES, TRUNCATED, JournalUnavailable, read_server_journal
from app.services.monitoring import MonitoringService
from app.storage.db import Storage


async def test_journal_reader_runs_fixed_command_and_bounds_output(monkeypatch):
    launch = asyncio.create_subprocess_exec

    async def fake_journal(*args, **kwargs):
        assert args == (
            "journalctl", "--directory=/host-journal", "--system", "--quiet", "--no-pager",
            "--utc", "--output=short-iso", "--reverse", "--lines=100",
        )
        return await launch(sys.executable, "-c", "print('entry ' * 50000)", **kwargs)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_journal)
    journal = await read_server_journal(100)
    assert journal.startswith("entry ") and journal.endswith(TRUNCATED)
    assert len(journal.encode()) <= MAX_JOURNAL_BYTES + len(TRUNCATED.encode())
    with pytest.raises(ValueError):
        await read_server_journal(201)


async def test_peer_journal_requires_authentication_and_valid_line_count(monkeypatch, tmp_path):
    settings = Settings(DATA_DIR=tmp_path, NODE_NAME="backup", SERVER_LOGS_ENABLED=True,
                        HA_PEER_ENABLED=True, HA_SECRET_KEY="peer-secret")
    collector = MetricsCollector(settings)
    monitoring = MonitoringService(settings, Storage(settings.db_path), collector)
    server = HAHttpServer(settings, HASupervisor(settings, monitoring), monitoring, collector.docker)
    read = AsyncMock(return_value="systemd[1]: Started service")
    monkeypatch.setattr("app.ha.http_server.read_server_journal", read)

    async with TestClient(TestServer(server.app)) as client:
        assert (await client.get("/ha/logs")).status == 401
        headers = {"X-HA-Secret": "peer-secret"}
        for value in ("0", "201", "--follow", "１２"):
            assert (await client.get("/ha/logs", headers=headers, params={"lines": value})).status == 400
        read.assert_not_awaited()

        main = Settings(HA_PEER_ENABLED=True, PEER_NODE_NAME="backup", PEER_TAILSCALE_IP=client.host,
                        HA_PEER_PORT=client.port, HA_SECRET_KEY="peer-secret")
        remote = MetricsCollector(main)
        assert await remote.get_server_logs(peer=True, lines=50) == read.return_value
        read.assert_awaited_once_with(50)
        read.side_effect = JournalUnavailable
        with pytest.raises(JournalUnavailable):
            await remote.get_server_logs(peer=True, lines=50)


async def test_logs_commands_and_buttons_work_for_everyone_in_the_command_group(tmp_path):
    settings = Settings(
        DATA_DIR=tmp_path, TELEGRAM_BOT_TOKEN="12345:test", TELEGRAM_CHAT_ID=-100123,
        TELEGRAM_COMMAND_CHAT_ID=-100456, NODE_NAME="main", PEER_NODE_NAME="backup",
    )
    group = settings.command_chat_id
    collector = MetricsCollector(settings)
    storage = Storage(settings.db_path)
    monitoring = MonitoringService(settings, storage, collector)
    manager = BotManager(settings, storage, collector, HASupervisor(settings, monitoring))
    collector.get_server_logs = AsyncMock(return_value="systemd[1]: Started service")
    requests = AsyncMock()
    manager.bot.session.make_request = requests

    async def send(text, user_id, chat_id, chat_type):
        message = Message(
            message_id=1, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type=chat_type),
            from_user=User(id=user_id, is_bot=False, first_name="Operator"), text=text,
        )
        await manager.dp.feed_update(
            manager.bot, Update(update_id=1, message=message), settings=settings, collector=collector,
        )

    async def click(data, user_id, chat_id, chat_type, node_settings=settings):
        callback = CallbackQuery(
            id="selection", chat_instance="logs", data=data,
            from_user=User(id=user_id, is_bot=False, first_name="Operator"),
            message=Message(message_id=2, date=datetime.now(timezone.utc), chat=Chat(id=chat_id, type=chat_type)),
        )
        await manager.dp.feed_update(
            manager.bot, Update(update_id=2, callback_query=callback), settings=node_settings, collector=collector,
        )

    try:
        await manager.dp.emit_startup(bot=manager.bot)
        registered = requests.await_args.args[1]
        assert isinstance(registered, SetMyCommands)
        assert registered.scope.chat_id == group
        assert {"logs", "alerts", "note", "handover", "problems"} <= {command.command for command in registered.commands}
        requests.reset_mock()

        await send("/logs backup 100", 456, settings.TELEGRAM_CHAT_ID, "supergroup")
        await send("/logs backup 100", 456, 456, "private")
        await send("/start", 123, 123, "private")
        collector.get_server_logs.assert_not_awaited()
        requests.assert_not_awaited()
        await send("/help", 456, group, "supergroup")
        assert "Доступные команды" in requests.await_args.args[1].text

        await send("/start", 456, group, "supergroup")
        menu = requests.await_args.args[1]
        logs_button, = [button for row in menu.reply_markup.inline_keyboard for button in row if button.callback_data == "nav_logs"]
        await click(logs_button.callback_data, 456, group, "supergroup")
        selector = requests.await_args.args[1]
        assert isinstance(selector, EditMessageText)
        assert [button.text for button in selector.reply_markup.inline_keyboard[0]] == ["bili", "suslik"]
        collector.get_server_logs.assert_not_awaited()

        await send("/logs main 201", 123, group, "supergroup")
        collector.get_server_logs.assert_not_awaited()
        assert "200" in requests.await_args.args[1].text

        await send("/logs", 123, group, "supergroup")
        collector.get_server_logs.assert_not_awaited()
        menu = requests.await_args.args[1]
        assert menu.chat_id == group
        main, backup = menu.reply_markup.inline_keyboard[0]
        assert [main.text, backup.text] == ["bili", "suslik"]
        assert "50" in menu.text

        requests.reset_mock()
        await click(main.callback_data, 456, 456, "private")
        await click(backup.callback_data, 123, settings.TELEGRAM_CHAT_ID, "supergroup")
        collector.get_server_logs.assert_not_awaited()
        requests.assert_not_awaited()

        await click(main.callback_data, 456, group, "supergroup")
        collector.get_server_logs.assert_awaited_once_with(False, 50)
        assert isinstance(requests.await_args_list[0].args[1], AnswerCallbackQuery)
        assert "Журнал bili".lower() in requests.await_args.args[1].text.lower()

        await click(backup.callback_data, 123, group, "supergroup")
        collector.get_server_logs.assert_awaited_with(True, 50)
        assert "журнал suslik" in requests.await_args.args[1].text

        standby = settings.model_copy(update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup", "PEER_NODE_NAME": "main"})
        await click(main.callback_data, 123, group, "supergroup", standby)
        collector.get_server_logs.assert_awaited_with(True, 50)
        assert "журнал bili" in requests.await_args.args[1].text

        collector.get_server_logs.reset_mock()
        await send("/logs suslik 100", 456, group, "supergroup")
        collector.get_server_logs.assert_awaited_once_with(True, 100)
        result = requests.await_args.args[1]
        assert isinstance(result, SendMessage) and result.protect_content
        assert result.chat_id == group
        assert result.link_preview_options.is_disabled
        assert "suslik" in result.text and "Started service" in result.text

        collector.get_server_logs.return_value = "long journal " * 500
        await click(main.callback_data, 123, group, "supergroup")
        collector.get_server_logs.assert_awaited_with(False, 50)
        result = requests.await_args.args[1]
        assert isinstance(result, SendDocument) and result.protect_content
        assert result.chat_id == group
    finally:
        await manager.close()
