from unittest.mock import AsyncMock

import pytest

from app.core.config import NodeRole, Settings
from app.monitoring.collector import MetricsCollector
from app.monitoring.models import ServerMetrics


@pytest.mark.asyncio
@pytest.mark.parametrize("role", [NodeRole.PRIMARY, NodeRole.STANDBY])
async def test_server_buttons_resolve_beszel_names_with_wizard_defaults(role):
    local, peer = ("main", "backup") if role == NodeRole.PRIMARY else ("backup", "main")
    collector = MetricsCollector(Settings(
        NODE_ROLE=role, NODE_NAME=local, PEER_NODE_NAME=peer,
        TAILSCALE_IP="127.0.0.1", PEER_TAILSCALE_IP="127.0.0.1",
    ))
    main = ServerMetrics(name="BilioniD @bilionid")
    backup = ServerMetrics(name="suslik @suslik24na7")
    collector.get_servers = AsyncMock(return_value=[backup, main])

    assert await collector.get_server_detail("bili") == main
    assert await collector.get_server_detail("susl") == backup
    assert await collector.get_server_detail("missing") is None


@pytest.mark.asyncio
async def test_server_button_prefers_configured_address_over_legacy_name():
    collector = MetricsCollector(Settings(NODE_NAME="main", TAILSCALE_IP="100.64.1.2"))
    main = ServerMetrics(name="Production", hostname="100.64.1.2")
    other = ServerMetrics(name="BilioniD", hostname="100.64.1.20")
    collector.get_servers = AsyncMock(return_value=[other, main])

    assert await collector.get_server_detail("main") == main


async def test_local_and_peer_metrics_are_available_when_both_hubs_fail():
    collector = MetricsCollector(Settings(NODE_NAME="main", PEER_NODE_NAME="backup", HA_PEER_ENABLED=True))
    collector.beszel.get_all_servers = AsyncMock(return_value=[])
    remote = ServerMetrics(name="backup", source="local_fallback", monitor_state="FALLBACK")
    collector.get_peer_metrics = AsyncMock(return_value=remote)
    local, peer = await collector.get_servers()
    assert local.source == "local_fallback" and local.monitor_state == "FALLBACK"
    assert peer == remote
