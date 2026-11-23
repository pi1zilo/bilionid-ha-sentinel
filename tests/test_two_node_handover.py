from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
from aiohttp.test_utils import TestClient, TestServer
from aiogram.methods import GetUpdates
import pytest

from app.bot.bot import BotManager
from app.bot.handlers.ha import cb_handover
from app.core.config import NodeRole, Settings
from app.ha.handover import GRANT_SECONDS, TwoNodeHandover
from app.ha.http_server import HAHttpServer
from app.ha.supervisor import HASupervisor
from app.monitoring.docker.client import DockerMonitor


async def test_two_vps_partition_and_recovery_do_not_overlap_polling(monkeypatch):
    now = 100.0
    monkeypatch.setattr("app.ha.handover.clock", lambda: now)
    active = set()
    changes = []

    def switch(node):
        async def change(enabled):
            if enabled:
                assert not active
                active.add(node)
            else:
                active.remove(node)
            changes.append((node, enabled))
        return change

    secret = "s" * 32
    standby = Settings(
        NODE_ROLE=NodeRole.STANDBY, NODE_NAME="backup", PEER_NODE_NAME="main",
        HA_PEER_ENABLED=True, HA_AUTO_FAILOVER=True, HA_SECRET_KEY=secret,
    )
    backup = TwoNodeHandover(standby, switch("backup"))
    supervisor = HASupervisor(standby, AsyncMock())
    supervisor.handover = backup
    server = HAHttpServer(standby, supervisor, supervisor.monitoring, DockerMonitor(enabled=False))
    async with TestClient(TestServer(server.app)) as client:
        primary = Settings(
            NODE_NAME="main", PEER_NODE_NAME="backup", PEER_TAILSCALE_IP=client.host, HA_PEER_PORT=client.port,
            HA_PEER_ENABLED=True, HA_AUTO_FAILOVER=True, HA_SECRET_KEY=secret,
        )
        main = TwoNodeHandover(primary, switch("main"))
        assert (await client.post("/ha/grant", json={})).status == 401
        headers = {"X-HA-Secret": secret}
        assert (await client.post("/ha/grant", json={"node": "other"}, headers=headers)).status == 400
        await main._step_main()
        await backup._step_backup()
        assert not active

        now += GRANT_SECONDS
        await main._step_main()
        await backup._step_backup()
        assert active == {"main"}
        assert not await backup.grant("different-session")

        connected = main._post
        main._post = AsyncMock(side_effect=aiohttp.ClientConnectionError)
        now += 25
        await main._step_main()
        await backup._step_backup()
        assert not active

        now += 20
        await backup._step_backup()
        assert active == {"backup"}
        assert not main.can_poll()

        main._post = connected
        for _ in range(11):
            now += 4
            await main._step_main()
            await backup._step_backup()
        assert changes == [("main", True), ("main", False), ("backup", True), ("backup", False), ("main", True)]
        assert active == {"main"}

        await main.prefer("backup")
        await main._step_main()
        assert active == {"main"}
        now += GRANT_SECONDS
        await main._step_main()
        await backup._step_backup()
        assert active == {"backup"}


async def test_backup_restart_waits_for_any_previous_grant(monkeypatch):
    now = 100.0
    monkeypatch.setattr("app.ha.handover.clock", lambda: now)
    switch = AsyncMock()
    backup = TwoNodeHandover(Settings(NODE_ROLE=NodeRole.STANDBY), switch)
    now += GRANT_SECONDS - 1
    assert not await backup.grant("new-session")
    await backup._step_backup()
    switch.assert_not_awaited()
    now += 1
    await backup._step_backup()
    switch.assert_awaited_once_with(True)


async def test_get_updates_is_blocked_before_sending_if_grant_is_expired():
    settings = Settings(TELEGRAM_BOT_TOKEN="")
    supervisor = HASupervisor(settings, AsyncMock())
    supervisor.handover = TwoNodeHandover(settings, AsyncMock())
    manager = BotManager(settings, AsyncMock(), AsyncMock(), supervisor)
    transport = AsyncMock()
    with pytest.raises(RuntimeError, match="lease unavailable"):
        await manager._track_poll(transport, None, GetUpdates(timeout=5))
    transport.assert_not_awaited()


async def test_handover_confirmation_checks_admin_id():
    settings = Settings(HA_ADMIN_USER_ID=123)
    supervisor = HASupervisor(settings, AsyncMock())
    supervisor.handover = AsyncMock()
    callback = SimpleNamespace(from_user=SimpleNamespace(id=456), answer=AsyncMock(), data="ha_prefer:backup", message=None)
    await cb_handover(callback, supervisor)
    supervisor.handover.prefer.assert_not_awaited()
    callback.from_user.id = 123
    await cb_handover(callback, supervisor)
    supervisor.handover.prefer.assert_awaited_once_with("backup")
