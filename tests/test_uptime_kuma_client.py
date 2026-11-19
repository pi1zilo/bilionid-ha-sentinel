"""
Unit tests for Uptime Kuma monitoring client.
"""

from unittest.mock import AsyncMock, patch
import pytest

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
    assert "недоступен" in overview.error_message
