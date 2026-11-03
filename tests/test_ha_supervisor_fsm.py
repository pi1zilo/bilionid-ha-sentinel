"""
Unit and integration tests for HA Supervisor FSM, failover, recovery,
single polling invariant, and Telegram 409 split-brain mitigation.
"""

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, patch
import pytest

from app.core.config import NodeRole, NodeState, Settings
from app.ha.supervisor import HASupervisor
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
async def test_primary_startup_starts_polling(test_settings: Settings):
    storage = Storage(test_settings.db_path)
    start_bot = AsyncMock()
    stop_bot = AsyncMock()

    supervisor = HASupervisor(
        settings=test_settings,
        storage=storage,
        start_bot_callback=start_bot,
        stop_bot_callback=stop_bot,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock):
        with patch.object(supervisor.http_server, "stop", new_callable=AsyncMock):
            await supervisor.start()
            assert supervisor.role == NodeRole.PRIMARY
            assert supervisor.state == NodeState.LEADER_ACTIVE
            assert supervisor.is_polling is True
            start_bot.assert_awaited()

            await supervisor.stop()
            assert supervisor.is_polling is False
            stop_bot.assert_awaited()


@pytest.mark.asyncio
async def test_standby_startup_does_not_start_polling(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    start_bot = AsyncMock()
    stop_bot = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        start_bot_callback=start_bot,
        stop_bot_callback=stop_bot,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock):
        with patch.object(supervisor.http_server, "stop", new_callable=AsyncMock):
            await supervisor.start()
            assert supervisor.role == NodeRole.STANDBY
            assert supervisor.state == NodeState.FOLLOWER_PASSIVE
            assert supervisor.is_polling is False
            start_bot.assert_not_awaited()

            await supervisor.stop()


@pytest.mark.asyncio
async def test_standby_failover_takeover_when_primary_down(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    start_bot = AsyncMock()
    stop_bot = AsyncMock()
    send_alert = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        start_bot_callback=start_bot,
        stop_bot_callback=stop_bot,
        send_alert_callback=send_alert,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock), \
         patch.object(supervisor, "_check_external_connectivity", return_value=True), \
         patch.object(supervisor, "_probe_peer") as mock_probe:

        # Simulate probe failure
        def simulate_failure():
            supervisor.peer_health.is_reachable = False
            supervisor.peer_health.consecutive_failures += 1

        mock_probe.side_effect = simulate_failure

        await supervisor.start()

        # Wait enough cycles for failover (0.3s timeout with 0.1s interval)
        await asyncio.sleep(0.45)

        assert supervisor.state == NodeState.FAILOVER_TAKEOVER
        assert supervisor.is_polling is True
        start_bot.assert_awaited()
        send_alert.assert_awaited()

        await supervisor.stop()


@pytest.mark.asyncio
async def test_failover_aborted_when_no_internet(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    start_bot = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        start_bot_callback=start_bot,
    )

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock), \
         patch.object(supervisor, "_check_external_connectivity", return_value=False), \
         patch.object(supervisor, "_probe_peer") as mock_probe:

        def simulate_failure():
            supervisor.peer_health.is_reachable = False
            supervisor.peer_health.consecutive_failures = 10

        mock_probe.side_effect = simulate_failure

        await supervisor.start()
        await asyncio.sleep(0.2)

        # Polling must NOT start because Backup has no outbound internet
        assert supervisor.state == NodeState.DEGRADED
        assert supervisor.is_polling is False
        start_bot.assert_not_awaited()

        await supervisor.stop()


@pytest.mark.asyncio
async def test_recovery_yield_back_to_primary(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    start_bot = AsyncMock()
    stop_bot = AsyncMock()
    send_alert = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        start_bot_callback=start_bot,
        stop_bot_callback=stop_bot,
        send_alert_callback=send_alert,
    )

    # Put standby into promoted state
    supervisor.state = NodeState.FAILOVER_TAKEOVER
    supervisor.is_polling = True

    with patch.object(supervisor.http_server, "start", new_callable=AsyncMock), \
         patch.object(supervisor.http_server, "stop", new_callable=AsyncMock), \
         patch.object(supervisor, "_probe_peer") as mock_probe:

        # Simulate Primary recovered and healthy
        def simulate_healthy():
            supervisor.peer_health.is_reachable = True
            supervisor.peer_health.ha_endpoint_ok = True
            supervisor.peer_health.consecutive_successes += 1
            supervisor.peer_health.consecutive_failures = 0

        mock_probe.side_effect = simulate_healthy

        await supervisor.start()
        await asyncio.sleep(0.45)

        # Standby yields back to Primary!
        assert supervisor.state == NodeState.FOLLOWER_PASSIVE
        assert supervisor.is_polling is False
        stop_bot.assert_awaited()

        await supervisor.stop()


@pytest.mark.asyncio
async def test_telegram_conflict_409_emergency_stepdown(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    await storage.init_db()
    stop_bot = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        stop_bot_callback=stop_bot,
    )
    supervisor.is_polling = True
    supervisor.state = NodeState.FAILOVER_TAKEOVER

    # Trigger 409 Conflict
    supervisor.handle_telegram_conflict_409()
    await asyncio.sleep(0.1)

    assert supervisor.state == NodeState.FOLLOWER_PASSIVE
    assert supervisor.is_polling is False
    stop_bot.assert_awaited()

    events = await storage.get_recent_ha_events(limit=5)
    assert any(e["event_type"] == "SPLIT_BRAIN_AVERTED" for e in events)


@pytest.mark.asyncio
async def test_handle_yield_request_notifies_and_steps_down(test_settings: Settings):
    standby_settings = test_settings.model_copy(
        update={"NODE_ROLE": NodeRole.STANDBY, "NODE_NAME": "backup-node"}
    )
    storage = Storage(standby_settings.db_path)
    await storage.init_db()
    stop_bot = AsyncMock()
    send_alert = AsyncMock()

    supervisor = HASupervisor(
        settings=standby_settings,
        storage=storage,
        stop_bot_callback=stop_bot,
        send_alert_callback=send_alert,
    )
    supervisor.is_polling = True
    supervisor.state = NodeState.FAILOVER_TAKEOVER

    await supervisor.handle_yield_request()

    assert supervisor.state == NodeState.FOLLOWER_PASSIVE
    assert supervisor.is_polling is False
    stop_bot.assert_awaited_once()
    send_alert.assert_awaited_once()
    assert "ПЕРЕДАЧА УПРАВЛЕНИЯ НА PRIMARY" in send_alert.call_args[0][0]

