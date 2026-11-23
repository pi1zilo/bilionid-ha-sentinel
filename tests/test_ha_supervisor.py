from unittest.mock import AsyncMock

from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from app.core.config import NodeRole, Settings
from app.ha.http_server import HAHttpServer
from app.ha.supervisor import HASupervisor
from app.monitoring.docker.client import DockerMonitor


async def test_backup_observes_polling_independently_from_http():
    flag = {"ready": False}

    async def status(request):
        assert request.headers["X-HA-Secret"] == "valid-test-secret"
        return web.json_response({"node_name": "main", "polling_ready": flag["ready"]})

    server = web.Application()
    server.router.add_get("/ha/status", status)
    async with TestServer(server) as peer:
        settings = Settings(
            NODE_ROLE=NodeRole.STANDBY, HA_PEER_ENABLED=True, HA_WATCH_PRIMARY=True,
            PEER_NODE_NAME="main", PEER_TAILSCALE_IP=peer.host, HA_PEER_PORT=peer.port,
            HA_SECRET_KEY="valid-test-secret",
        )
        monitoring = AsyncMock()
        supervisor = HASupervisor(settings, monitoring)
        await supervisor.probe_peer()
        assert supervisor.peer_health.is_reachable
        assert not supervisor.get_ha_info()["polling_enabled"]
        assert monitoring.source_health.await_args.args[3] is False

        flag["ready"] = True
        await supervisor.probe_peer()
        assert monitoring.source_health.await_args.args[3] is True
        assert not supervisor.get_ha_info()["polling_enabled"]


async def test_authenticated_webhooks_and_readiness():
    settings = Settings(
        HA_PEER_ENABLED=True, HA_SECRET_KEY="peer-secret", UPTIME_KUMA_WEBHOOK_SECRET="kuma-secret",
        ZABBIX_WEBHOOK_SECRET="zabbix-secret",
    )
    monitoring = AsyncMock()
    supervisor = HASupervisor(settings, monitoring)
    server = HAHttpServer(settings, supervisor, monitoring, DockerMonitor(enabled=False))
    async with TestClient(TestServer(server.app)) as client:
        assert (await client.get("/health")).status == 200
        assert (await client.get("/ready")).status == 503
        assert (await client.get("/ha/status")).status == 401
        assert (await client.get("/ha/status", headers={"X-HA-Secret": "peer-secret"})).status == 200
        assert (await client.post("/ha/force_role")).status == 404
        assert (await client.post("/events/kuma", json={})).status == 401
        headers = {"X-Monitor-Secret": "kuma-secret"}
        assert (await client.post("/events/kuma", headers=headers, json=[])).status == 400
        assert (await client.post("/events/kuma", headers=headers, json={})).status == 204
        monitoring.accept_kuma.assert_awaited_once_with({})
        assert (await client.post("/events/zabbix", headers=headers, json={})).status == 401
        supervisor.bot_status = lambda: {"polling_ready": True}
        assert (await client.get("/ready")).status == 200


def test_manual_polling_flag_survives_restart_and_overrides_physical_role():
    assert not Settings(NODE_ROLE=NodeRole.PRIMARY, TELEGRAM_POLLING_ENABLED=False).polling_enabled
    assert Settings(NODE_ROLE=NodeRole.STANDBY, TELEGRAM_POLLING_ENABLED=True).polling_enabled
