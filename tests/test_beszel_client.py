"""
Unit tests for Beszel Hub client, failover, and parsing.
"""

from unittest.mock import AsyncMock, patch
import pytest

from app.monitoring.beszel.client import BeszelClient, format_uptime


def test_uptime_formatting():
    # 5 days 2 hours
    sec = 5 * 86400 + 2 * 3600
    res = format_uptime(sec)
    assert res == "5 дней 2 часа"

    # 1 day 1 hour
    sec = 1 * 86400 + 1 * 3600
    res = format_uptime(sec)
    assert res == "1 день 1 час"

    # 3 days 4 hours
    sec = 3 * 86400 + 4 * 3600
    res = format_uptime(sec)
    assert res == "3 дня 4 часа"

    # 25 minutes
    sec = 25 * 60
    res = format_uptime(sec)
    assert res == "25 мин"


def test_beszel_record_parsing():
    client = BeszelClient()
    mock_record = {
        "id": "sys123",
        "name": "primary-node",
        "host": "100.64.0.1",
        "status": "up",
        "info": {
            "cpu": 12.0,
            "m": 4.2,
            "mt": 24.0,
            "mp": 17.5,
            "d": 12.0,
            "dt": 99.0,
            "dp": 12.1,
            "l": [0.31, 0.56, 0.50],
            "u": 439200,
            "c": 10,
            "cr": 10,
        },
    }

    metrics = client._parse_system_record(mock_record)
    assert metrics.name == "primary-node"
    assert metrics.is_online is True
    assert metrics.cpu_percent == 12.0
    assert metrics.ram_used_gb == 4.2
    assert metrics.ram_total_gb == 24.0
    assert metrics.disk_used_gb == 12.0
    assert metrics.disk_total_gb == 99.0
    assert metrics.disk_free_gb == 87.0
    assert metrics.load_1m == 0.31
    assert metrics.load_5m == 0.56
    assert metrics.load_15m == 0.50
    assert metrics.docker_containers_total == 10
    assert metrics.docker_containers_running == 10
    assert metrics.docker_containers_stopped == 0
    assert metrics.uptime_str == "5 дней 2 часа"


@pytest.mark.asyncio
async def test_beszel_hub_failover():
    client = BeszelClient(
        main_url="http://100.64.0.1:29001",
        backup_url="http://100.64.0.2:29000",
    )

    mock_backup_items = [
        {
            "id": "rec1",
            "name": "backup-node",
            "status": "up",
            "info": {"cpu": 5.0, "m": 1.0, "mt": 4.0, "d": 5.0, "dt": 40.0, "l": [0.1, 0.1, 0.1], "u": 3600},
        }
    ]

    async def mock_fetch(session, hub_url):
        if "29001" in hub_url:
            return None  # Main Hub fails
        return mock_backup_items  # Backup Hub succeeds

    with patch.object(client, "_fetch_from_hub", side_effect=mock_fetch):
        servers = await client.get_all_servers()
        assert len(servers) == 1
        assert servers[0].name == "backup-node"
        assert client.active_hub == "http://100.64.0.2:29000"


def test_local_fallback_metrics():
    client = BeszelClient()
    metrics = client.get_local_fallback_metrics("Local-Test")
    assert metrics.name == "Local-Test"
    assert metrics.is_online is True
    assert metrics.cpu_percent >= 0.0
    assert metrics.ram_total_gb > 0.0
    assert metrics.source == "local_fallback"


@pytest.mark.asyncio
async def test_collector_peer_metrics_fallback():
    from app.core.config import Settings
    from app.monitoring.collector import MetricsCollector
    from app.monitoring.models import ServerMetrics

    settings = Settings(
        NODE_NAME="primary-node",
        PEER_NODE_NAME="backup-node",
        TELEGRAM_BOT_TOKEN="12345:dummy",
    )
    collector = MetricsCollector(settings)

    # Simulate Beszel returning []
    collector.beszel.get_all_servers = AsyncMock(return_value=[])

    # Simulate peer responding over HA endpoint
    peer_metric = ServerMetrics(
        name="backup-node",
        hostname="100.64.0.2",
        cpu_percent=4.5,
        ram_percent=25.0,
        ram_used_gb=1.0,
        ram_total_gb=4.0,
        uptime_str="2 дня 5 часов",
        is_online=True,
    )
    collector.get_peer_metrics = AsyncMock(return_value=peer_metric)

    servers = await collector.get_servers(force_refresh=True)
    assert len(servers) == 2
    assert servers[0].name == "primary-node"
    assert servers[0].is_online is True
    assert servers[1].name == "backup-node"
    assert servers[1].is_online is True
    assert servers[1].cpu_percent == 4.5
    assert servers[1].uptime_str == "2 дня 5 часов"

