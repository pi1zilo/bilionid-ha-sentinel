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
        self._lock = asyncio.Lock()

    async def get_servers(self, force_refresh: bool = False) -> List[ServerMetrics]:
        """Fetches server metrics for both nodes from Beszel with fallback."""
        now = asyncio.get_running_loop().time()
        async with self._lock:
            if not force_refresh and self._cached_servers and (now - self._cached_servers_time < self._cache_ttl_sec):
                return self._cached_servers

            servers = await self.beszel.get_all_servers()
            if not servers:
                # If Beszel hubs are unreachable, gather local fallback for this node
                local_metric = self.beszel.get_local_fallback_metrics(self.settings.NODE_NAME)
                # And create an offline placeholder for the peer node
                peer_metric = ServerMetrics(
                    name=self.settings.PEER_NODE_NAME,
                    hostname=self.settings.PEER_TAILSCALE_IP,
                    status="🔴 Offline (Hub unreachable)",
                    is_online=False,
                    source="unavailable",
                    last_updated=datetime.now(timezone.utc).strftime("%H:%M:%S"),
                )
                servers = [local_metric, peer_metric]

            self._cached_servers = servers
            self._cached_servers_time = now
            return servers

    async def get_server_detail(self, query: str) -> Optional[ServerMetrics]:
        """Returns details for a specific server (e.g. 'bili', 'susl', 'main', 'backup')."""
        servers = await self.get_servers()
        query_l = query.lower()

        # Keyword mapping
        alias_map = {
            "bili": "bilionid",
            "main": "bilionid",
            "primary": "bilionid",
            "susl": "suslikvpn",
            "backup": "suslikvpn",
            "standby": "suslikvpn",
        }
        target_term = alias_map.get(query_l, query_l)

        for s in servers:
            if target_term in s.name.lower() or target_term in s.hostname.lower():
                return s

        return servers[0] if servers else None

    async def get_all_containers(self) -> Dict[str, List[ContainerInfo]]:
        """
        Gathers containers from local Docker engine and queries peer's HA endpoint
        to provide a complete view across both VPS nodes.
        """
        results: Dict[str, List[ContainerInfo]] = {
            self.settings.NODE_NAME: [],
            self.settings.PEER_NODE_NAME: [],
        }

        # 1. Local containers
        local_containers = await self.docker.get_containers()
        results[self.settings.NODE_NAME] = local_containers

        # 2. Remote containers via peer's HA API
        peer_url = f"{self.settings.peer_ha_url}/ha/containers"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(peer_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        containers = [ContainerInfo(**item) for item in data.get("containers", [])]
                        results[self.settings.PEER_NODE_NAME] = containers
                    else:
                        logger.warning("Peer container endpoint returned HTTP %s", resp.status)
        except Exception as e:
            logger.debug("Could not reach peer container endpoint at %s: %s", peer_url, e)

        return results

    async def get_uptime_kuma(self) -> UptimeKumaOverview:
        """Returns Uptime Kuma monitors."""
        return await self.uptime_kuma.get_overview()

    async def get_beszel_status(self) -> Dict[str, Any]:
        """Checks both Beszel Hubs health."""
        main_health = await self.beszel.check_hub_health(self.settings.BESZEL_MAIN_URL)
        backup_health = await self.beszel.check_hub_health(self.settings.BESZEL_BACKUP_URL)
        return {
            "active_hub": self.beszel.active_hub,
            "main": main_health,
            "backup": backup_health,
        }
