"""
Unit tests for proactive alerting:
- Standby startup alert
- Primary detecting peer unreachable and peer recovered
- AlertWatcher detecting container crash/recovery and server state transitions
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
import pytest

from app.core.config import NodeRole, NodeState, Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.alert_watcher import AlertWatcher
from app.monitoring.models import ContainerInfo, ServerMetrics
from app.storage.db import Storage


@pytest.fixture
def test_settings(tmp_path: Path):
    return Settings(
        NODE_ROLE=NodeRole.PRIMARY,
        NODE_NAME="primary-node",
        TAILSCALE_IP="100.64.0.1",
        PEER_NODE_NAME="backup-node",
        PEER_TAILSCALE_IP="100.64.0.2",
        HA_PORT=29099,
        HA_SECRET_KEY="test-secret-12345",
        HA_HEARTBEAT_INTERVAL_SEC=0.1,
        HA_FAILOVER_TIMEOUT_SEC=0.3,
        HA_RECOVERY_TIMEOUT_SEC=0.3,
        HA_LEASE_DURATION_SEC=0.5,
        DATA_DIR=tmp_path,
        TELEGRAM_BOT_TOKEN="12345:dummy",
    )


@pytest.mark.asyncio
async def test_standby_startup_sends_alert(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    send_alert = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        send_alert_callback=send_alert,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock):
        await supervisor.start()
        assert supervisor.role == NodeRole.STANDBY
        send_alert.assert_awaited()
        # Verify alert content
        first_call_msg = send_alert.call_args_list[0][0][0]
        assert "РЕЗЕРВНЫЙ СЕРВЕР ЗАПУЩЕН (STANDBY)" in first_call_msg
        assert "backup-node" in first_call_msg

        await supervisor.stop()


@pytest.mark.asyncio
async def test_primary_detects_peer_down_and_recovery(test_settings: Settings):
    storage = Storage(test_settings.db_path)
    send_alert = AsyncMock()

    supervisor = HASupervisor(
        settings=test_settings,
        storage=storage,
        send_alert_callback=send_alert,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock), \
         patch.object(supervisor, "_send_yield_to_peer", new_callable=AsyncMock), \
         patch.object(supervisor, "_send_heartbeat", new_callable=AsyncMock):

        await supervisor.start()
        # Clear the startup alert call
        send_alert.reset_mock()

        # 1. Simulate peer becoming unreachable for 2 cycles
        supervisor.peer_health.is_reachable = False
        supervisor.peer_health.consecutive_failures = 2
        with patch.object(supervisor, "_probe_peer", new_callable=AsyncMock):
            await supervisor._primary_cycle()

            send_alert.assert_awaited_once()
            call_msg = send_alert.call_args[0][0]
            assert "РЕЗЕРВНЫЙ СЕРВЕР НЕДОСТУПЕН" in call_msg
            assert "backup-node" in call_msg
            assert supervisor._peer_down_alert_sent is True

            # 2. Additional failed cycle should NOT spam another alert
            send_alert.reset_mock()
            supervisor.peer_health.consecutive_failures = 3
            await supervisor._primary_cycle()
            send_alert.assert_not_awaited()

            # 3. Simulate peer recovery for 2 cycles
            supervisor.peer_health.is_reachable = True
            supervisor.peer_health.consecutive_successes = 2
            await supervisor._primary_cycle()
            send_alert.assert_awaited_once()
            recovery_msg = send_alert.call_args[0][0]
            assert "РЕЗЕРВНЫЙ СЕРВЕР В СЕТИ" in recovery_msg
            assert supervisor._peer_down_alert_sent is False

        await supervisor.stop()


@pytest.mark.asyncio
async def test_alert_watcher_container_failure_and_recovery(test_settings: Settings):
    notify = AsyncMock()
    mock_collector = MagicMock()

    watcher = AlertWatcher(
        settings=test_settings,
        collector=mock_collector,
        notify_callback=notify,
        interval_sec=0.1,
    )

    # Initial state: container is running
    c_running = ContainerInfo(name="web_app", status="Up 2 hours", state="running", server="primary-node")
    mock_collector.get_all_containers = AsyncMock(return_value={"primary-node": [c_running]})

    # First check: populates baseline, no alert
    await watcher._check_containers()
    notify.assert_not_awaited()

    # Second check: container crashes
    c_exited = ContainerInfo(name="web_app", status="Exited (1) 5 seconds ago", state="exited", server="primary-node")
    mock_collector.get_all_containers = AsyncMock(return_value={"primary-node": [c_exited]})
    await watcher._check_containers()

    notify.assert_awaited_once()
    assert "СБОЙ КОНТЕЙНЕРА НА primary-node" in notify.call_args[0][0]
    assert "web_app" in notify.call_args[0][0]

    # Third check: container recovered
    notify.reset_mock()
    mock_collector.get_all_containers = AsyncMock(return_value={"primary-node": [c_running]})
    await watcher._check_containers()

    notify.assert_awaited_once()
    assert "КОНТЕЙНЕР ВОССТАНОВЛЕН НА primary-node" in notify.call_args[0][0]
