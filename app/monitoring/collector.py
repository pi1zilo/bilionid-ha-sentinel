"""
Unified metrics collector coordinating Beszel, Uptime Kuma, and Docker monitors.
Includes short-lived caching to minimize network overhead.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import aiohttp

from app.core.config import Settings
from app.monitoring.beszel.client import BeszelClient
from app.monitoring.docker.client import DockerMonitor
from app.monitoring.models import ContainerInfo, ServerMetrics, UptimeKumaOverview
from app.monitoring.uptime_kuma.client import UptimeKumaClient

logger = logging.getLogger("bilionid_ha.collector")


class MetricsCollector:
    def __init__(self, settings: Settings):
        self.settings = settings
        self.beszel = BeszelClient(
            main_url=settings.BESZEL_MAIN_URL,
            backup_url=settings.BESZEL_BACKUP_URL,
            username=settings.BESZEL_USERNAME,
            password=settings.BESZEL_PASSWORD,
            auth_token=settings.BESZEL_AUTH_TOKEN,
        )
        self.uptime_kuma = UptimeKumaClient(
            base_url=settings.UPTIME_KUMA_URL,
            api_key=settings.UPTIME_KUMA_API_KEY,
            username=settings.UPTIME_KUMA_USERNAME,
            password=settings.UPTIME_KUMA_PASSWORD,
            enabled=settings.UPTIME_KUMA_ENABLED,
        )
        self.docker = DockerMonitor(
            server_name=settings.NODE_NAME,
            socket_path=settings.DOCKER_SOCKET_PATH,
            enabled=settings.DOCKER_MONITOR_ENABLED,
        )

        self._cached_servers: List[ServerMetrics] = []
        self._cached_servers_time: float = 0.0
        self._cache_ttl_sec: float = 5.0

        self._cached_kuma: Optional[UptimeKumaOverview] = None
        self._cached_kuma_time: float = 0.0
        self._kuma_cache_ttl_sec: float = 10.0

        self.containers_errors: Dict[str, str] = {}
        self._lock = asyncio.Lock()

    async def get_peer_metrics(self) -> Optional[ServerMetrics]:
        """Fetches local host metrics from peer node via private HA API."""
        peer_url = f"{self.settings.peer_ha_url}/ha/metrics"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=2.0, connect=1.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(peer_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        raw_metrics = data.get("metrics")
                        if raw_metrics:
                            metric = ServerMetrics(**raw_metrics)
                            metric.source = "ha_peer"
                            return metric
                    else:
                        logger.debug("Peer metrics endpoint returned HTTP %s", resp.status)
        except Exception as e:
            logger.debug("Could not reach peer metrics endpoint at %s: %s", peer_url, e)
        return None

    async def get_servers(self, force_refresh: bool = False) -> List[ServerMetrics]:
        """Fetches server metrics for both nodes from Beszel with fallback to HA peer."""
        now = asyncio.get_running_loop().time()
        async with self._lock:
            if not force_refresh and self._cached_servers and (now - self._cached_servers_time < self._cache_ttl_sec):
                return self._cached_servers

            servers = await self.beszel.get_all_servers()
            if not servers:
                # Beszel hubs are unreachable or unauthorized: gather local metrics
                local_metric = self.beszel.get_local_fallback_metrics(self.settings.NODE_NAME)
                # Try to get real metrics from peer via HA inter-node endpoint
                peer_metric = await self.get_peer_metrics()
                if not peer_metric:
                    peer_metric = ServerMetrics(
                        name=self.settings.PEER_NODE_NAME,
                        hostname=self.settings.PEER_TAILSCALE_IP,
                        status="🔴 Offline (Hub unreachable)",
                        is_online=False,
                        source="unavailable",
                        last_updated=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    )
                servers = [local_metric, peer_metric]
            else:
                # If Beszel returned some servers, but peer node is missing from Beszel,
                # supplement it via HA peer endpoint
                peer_name_lower = self.settings.PEER_NODE_NAME.lower()
                has_peer = any(
                    peer_name_lower in s.name.lower() or peer_name_lower in s.hostname.lower()
                    for s in servers
                )
                if not has_peer:
                    peer_metric = await self.get_peer_metrics()
                    if peer_metric:
                        servers.append(peer_metric)

            self._cached_servers = servers
            self._cached_servers_time = now
            return servers

    async def get_server_detail(self, query: str) -> Optional[ServerMetrics]:
        """Returns details for a specific server (e.g. 'bili', 'susl', 'main', 'backup')."""
        servers = await self.get_servers()
        query_l = query.lower()

        is_primary_req = query_l in ("main", "primary", "bili", "node1")
        is_backup_req = query_l in ("backup", "standby", "susl", "node2")

        if is_primary_req:
            primary_name = (
                self.settings.NODE_NAME
                if self.settings.is_primary
                else self.settings.PEER_NODE_NAME
            ).lower()
            for s in servers:
                if primary_name in s.name.lower() or "main" in s.name.lower() or "primary" in s.name.lower():
                    return s
            return servers[0] if servers else None

        if is_backup_req:
            backup_name = (
                self.settings.PEER_NODE_NAME
                if self.settings.is_primary
                else self.settings.NODE_NAME
            ).lower()
            for s in servers:
                if backup_name in s.name.lower() or "backup" in s.name.lower() or "standby" in s.name.lower():
                    return s
            return servers[1] if len(servers) > 1 else (servers[0] if servers else None)

        for s in servers:
            if query_l in s.name.lower() or query_l in s.hostname.lower():
                return s

        return servers[0] if servers else None

    async def get_all_containers(self) -> Dict[str, List[ContainerInfo]]:
        """
        Gathers containers from local Docker engine and queries peer's HA endpoint
        to provide a complete view across both VPS nodes.
        Records errors into self.containers_errors.
        """
        self.containers_errors.clear()
        results: Dict[str, List[ContainerInfo]] = {
            self.settings.NODE_NAME: [],
            self.settings.PEER_NODE_NAME: [],
        }

        # 1. Local containers
        local_containers = await self.docker.get_containers()
        results[self.settings.NODE_NAME] = local_containers
        if not local_containers and self.docker.last_error:
            self.containers_errors[self.settings.NODE_NAME] = self.docker.last_error

        # 2. Remote containers via peer's HA API
        peer_url = f"{self.settings.peer_ha_url}/ha/containers"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=2.0, connect=1.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(peer_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        containers = [ContainerInfo(**item) for item in data.get("containers", [])]
                        results[self.settings.PEER_NODE_NAME] = containers
                    else:
                        self.containers_errors[self.settings.PEER_NODE_NAME] = f"HTTP {resp.status} от узла"
                        logger.warning("Peer container endpoint returned HTTP %s", resp.status)
        except Exception as e:
            self.containers_errors[self.settings.PEER_NODE_NAME] = "Узел выключен или недоступен по сети"
            logger.debug("Could not reach peer container endpoint at %s: %s", peer_url, e)

        return results

    async def get_uptime_kuma(self, force_refresh: bool = False) -> UptimeKumaOverview:
        """Returns Uptime Kuma monitors with short-lived caching."""
        now = asyncio.get_running_loop().time()
        if not force_refresh and self._cached_kuma and (now - self._cached_kuma_time < self._kuma_cache_ttl_sec):
            return self._cached_kuma

        overview = await self.uptime_kuma.get_overview()
        self._cached_kuma = overview
        self._cached_kuma_time = now
        return overview

    async def get_beszel_status(self) -> Dict[str, Any]:
        """Checks both Beszel Hubs health."""
        main_health = await self.beszel.check_hub_health(self.settings.BESZEL_MAIN_URL)
        backup_health = await self.beszel.check_hub_health(self.settings.BESZEL_BACKUP_URL)
        return {
            "active_hub": self.beszel.active_hub,
            "main": main_health,
            "backup": backup_health,
        }
