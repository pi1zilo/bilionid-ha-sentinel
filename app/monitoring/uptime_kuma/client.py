"""
Uptime Kuma client supporting Prometheus metrics and HTTP status APIs
with optional configuration and graceful degradation.
"""

from __future__ import annotations

import re
import logging
from typing import Any, Dict, List, Optional
import aiohttp

from app.monitoring.models import UptimeKumaMonitor, UptimeKumaOverview

logger = logging.getLogger("bilionid_ha.uptime_kuma")


class UptimeKumaClient:
    def __init__(
        self,
        base_url: str = "http://100.64.0.1:29000",
        api_key: Optional[str] = None,
        username: Optional[str] = None,
        password: Optional[str] = None,
        enabled: bool = True,
        timeout_sec: float = 2.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.username = username
        self.password = password
        self.enabled = enabled
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec, connect=1.0)

    async def get_overview(self) -> UptimeKumaOverview:
        """Fetches status of all monitors from Uptime Kuma."""
        if not self.enabled:
            return UptimeKumaOverview(
                available=False,
                error_message="Интеграция Uptime Kuma отключена в конфигурации.",
            )

        headers: Dict[str, str] = {}
        auth = None
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        elif self.username and self.password:
            auth = aiohttp.BasicAuth(self.username, self.password)

        async with aiohttp.ClientSession(headers=headers, auth=auth) as session:
            # 1. Try Prometheus metrics endpoint (common & robust)
            try:
                metrics_url = f"{self.base_url}/metrics"
                async with session.get(metrics_url, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        text = await resp.text()
                        monitors = self._parse_prometheus_metrics(text)
                        if monitors:
                            up = sum(1 for m in monitors if m.status == "UP")
                            down = sum(1 for m in monitors if m.status == "DOWN")
                            return UptimeKumaOverview(
                                available=True,
                                monitors=monitors,
                                total_count=len(monitors),
                                up_count=up,
                                down_count=down,
                            )
            except Exception as e:
                logger.debug("Uptime Kuma /metrics probe failed: %s", e)

            # 2. Try default status page heartbeats endpoint
            try:
                status_url = f"{self.base_url}/api/status-page/heartbeat/default"
                async with session.get(status_url, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        monitors = self._parse_status_page_heartbeats(data)
                        if monitors:
                            up = sum(1 for m in monitors if m.status == "UP")
                            down = sum(1 for m in monitors if m.status == "DOWN")
                            return UptimeKumaOverview(
                                available=True,
                                monitors=monitors,
                                total_count=len(monitors),
                                up_count=up,
                                down_count=down,
                            )
            except Exception as e:
                logger.debug("Uptime Kuma status-page probe failed: %s", e)

        return UptimeKumaOverview(
            available=False,
            error_message="Uptime Kuma недоступен или API не настроен.",
        )

    def _parse_prometheus_metrics(self, text: str) -> List[UptimeKumaMonitor]:
        """Parses monitor_status and monitor_response_time from Prometheus metrics."""
        monitors_map: Dict[str, UptimeKumaMonitor] = {}

        # Regex for monitor_status{monitor_name="...", ...} 1
        status_pattern = re.compile(
            r'monitor_status\{[^}]*monitor_name="([^"]+)"[^}]*\}\s+([0-9.]+)'
        )
        ping_pattern = re.compile(
            r'monitor_response_time\{[^}]*monitor_name="([^"]+)"[^}]*\}\s+([0-9.]+)'
        )

        for line in text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue

            match = status_pattern.search(line)
            if match:
                name, val = match.groups()
                status_num = int(float(val))
                status_str = "UP" if status_num == 1 else ("DOWN" if status_num == 0 else "PENDING")
                if name not in monitors_map:
                    monitors_map[name] = UptimeKumaMonitor(name=name, status=status_str)
                else:
                    monitors_map[name].status = status_str

            ping_match = ping_pattern.search(line)
            if ping_match:
                name, val = ping_match.groups()
                ping_val = round(float(val), 1)
                if name not in monitors_map:
                    monitors_map[name] = UptimeKumaMonitor(name=name, ping_ms=ping_val)
                else:
                    monitors_map[name].ping_ms = ping_val

        return list(monitors_map.values())

    def _parse_status_page_heartbeats(self, data: Dict[str, Any]) -> List[UptimeKumaMonitor]:
        """Parses heartbeats from status-page JSON format."""
        heartbeat_list = data.get("heartbeatList", {})
        monitors: List[UptimeKumaMonitor] = []

        for monitor_id, beats in heartbeat_list.items():
            if not beats:
                continue
            last_beat = beats[-1]
            status_code = last_beat.get("status", 0)
            status_str = "UP" if status_code == 1 else "DOWN"
            ping = last_beat.get("ping")
            msg = last_beat.get("msg")
            time_str = last_beat.get("time")

            monitors.append(
                UptimeKumaMonitor(
                    id=str(monitor_id),
                    name=f"Monitor #{monitor_id}",
                    status=status_str,
                    ping_ms=round(float(ping), 1) if ping is not None else None,
                    msg=msg,
                    last_check=time_str,
                )
            )

        return monitors
