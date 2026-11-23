"""
Unit tests for Uptime Kuma monitoring client.
"""

from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from app.bot.formatters import format_uptime_kuma_view
from app.monitoring.uptime_kuma.client import UptimeKumaClient


def test_parse_prometheus_metrics():
    client = UptimeKumaClient()
    prometheus_sample = """
# HELP monitor_status The status of the monitor (1 = UP, 0 = DOWN)
# TYPE monitor_status gauge
monitor_status{monitor_name="SysTrack Web",monitor_type="http"} 1
monitor_status{monitor_name="Beszel Hub Main",monitor_type="port"} 1
monitor_status{monitor_name="Weather Bot",monitor_type="http"} 0

# HELP monitor_response_time The response time of the monitor
# TYPE monitor_response_time gauge
monitor_response_time{monitor_name="SysTrack Web"} 32.5
monitor_response_time{monitor_name="Beszel Hub Main"} 12.1
"""
    monitors = client._parse_prometheus_metrics(prometheus_sample)
    assert len(monitors) == 3

    m_map = {m.name: m for m in monitors}
    assert m_map["SysTrack Web"].status == "UP"
    assert m_map["SysTrack Web"].ping_ms == 32.5
    assert m_map["Beszel Hub Main"].status == "UP"
    assert m_map["Beszel Hub Main"].ping_ms == 12.1
    assert m_map["Weather Bot"].status == "DOWN"


@pytest.mark.asyncio
async def test_uptime_kuma_disabled():
    client = UptimeKumaClient(enabled=False)
    overview = await client.get_overview()
    assert overview.available is False
    assert "отключена" in overview.error_message


@pytest.mark.asyncio
async def test_uptime_kuma_unreachable():
    client = UptimeKumaClient(base_url="http://192.0.2.1:29000", timeout_sec=0.1)
    overview = await client.get_overview()
    assert overview.available is False
    assert overview.state == "UNKNOWN"


@pytest.mark.asyncio
@pytest.mark.parametrize("slug, expected", [("", "default"), ("public", "public")])
async def test_metrics_error_falls_back_to_status_page(slug, expected):
    client = UptimeKumaClient(base_url="http://kuma", status_page_slug=slug)
    session = MagicMock()
    metrics = MagicMock(status=401)
    page = MagicMock(status=200, json=AsyncMock(return_value={
        "heartbeatList": {"1": [{"status": 1}]},
    }))
    metadata = MagicMock(status=200, json=AsyncMock(return_value={
        "publicGroupList": [{"monitorList": [
            {"id": 1, "name": "Weather Bot", "tags": [{"name": "Suslik"}]},
        ]}],
    }))
    contexts = [MagicMock(), MagicMock(), MagicMock()]
    for context, response in zip(contexts, [metrics, page, metadata]):
        context.__aenter__.return_value = response
    session.get.side_effect = contexts

    with patch("aiohttp.ClientSession") as client_session:
        client_session.return_value.__aenter__.return_value = session
        overview = await client.get_overview(include_tags=True)

    assert overview.available
    assert overview.up_count == 1
    assert session.get.call_args_list[1].args[0] == f"http://kuma/api/status-page/heartbeat/{expected}"
    assert session.get.call_args_list[2].args[0] == f"http://kuma/api/status-page/{expected}"
    assert overview.monitors[0].name == "Weather Bot"
    assert overview.monitors[0].tags == ["Suslik"]


def test_metrics_keep_distinct_monitors_with_the_same_name():
    monitors = UptimeKumaClient()._parse_prometheus_metrics(
        'monitor_status{monitor_name="same",monitor_url="http://a"} 0\n'
        'monitor_status{monitor_name="same",monitor_url="http://b"} 3\n'
    )
    assert sorted(m.status for m in monitors) == ["DOWN", "MAINTENANCE"]
    assert len({m.id for m in monitors}) == 2


@pytest.mark.asyncio
async def test_overview_uses_exact_tags_by_id_and_keeps_monitors_outside_status_page():
    client = UptimeKumaClient(base_url="http://kuma")
    session = MagicMock()
    metrics_context = MagicMock()
    metrics_context.__aenter__.return_value = MagicMock(status=200, text=AsyncMock(return_value=(
        'monitor_status{monitor_id="1",monitor_name="bot",monitor_type="docker",Server="Suslik"} 1\n'
        'monitor_status{monitor_id="2",monitor_name="bot",monitor_type="docker",Server="Bili"} 0\n'
        'monitor_status{monitor_name="legacy",monitor_type="http"} 1\n'
        'monitor_status{monitor_name="bot",monitor_type="docker"} 1\n'
        'monitor_status{monitor_id="4",monitor_name="metrics-only",Server="Bili,Suslik",Docker=""} 1\n'
        'monitor_response_time{monitor_id="1",monitor_name="bot",monitor_type="docker",Server="Suslik"} -1\n'
    )))
    metadata_context = MagicMock()
    metadata_context.__aenter__.return_value = MagicMock(status=200, json=AsyncMock(return_value={
        "publicGroupList": [{"monitorList": [
            {"id": 2, "name": "bot", "tags": [{"name": "Сервер", "value": "Bili"}]},
            {"id": 1, "name": "bot", "tags": [{"name": "Сервер", "value": "Suslik VPS"}]},
            {"id": 3, "name": "legacy", "tags": [{"name": "Legacy"}]},
        ]}],
    }))
    session.get.side_effect = [metrics_context, metadata_context]

    with patch("aiohttp.ClientSession") as client_session:
        client_session.return_value.__aenter__.return_value = session
        overview = await client.get_overview(include_tags=True)

    assert overview.total_count == 5
    assert format_uptime_kuma_view(overview).splitlines()[3:] == [
        "• bot: UP · 🏷 Сервер: Suslik VPS",
        "• bot: DOWN · 🏷 Сервер: Bili",
        "• legacy: UP · 🏷 Legacy",
        "• bot: UP",
        "• metrics-only: UP · 🏷 Server: Bili, Server: Suslik, Docker",
    ]
