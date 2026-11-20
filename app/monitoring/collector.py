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
    def __init__(self, settings: Settings, docker_monitor: DockerMonitor | None = None):
        self.settings = settings
        self.beszel = BeszelClient(
            main_url=settings.BESZEL_MAIN_URL,
            backup_url=settings.BESZEL_BACKUP_URL,
            username=settings.BESZEL_USERNAME,
            password=settings.BESZEL_PASSWORD,
            auth_token=settings.BESZEL_AUTH_TOKEN,
            backup_username=settings.BESZEL_BACKUP_USERNAME,
            backup_password=settings.BESZEL_BACKUP_PASSWORD,
            backup_auth_token=settings.BESZEL_BACKUP_AUTH_TOKEN,
        )
        self.uptime_kuma = UptimeKumaClient(
            base_url=settings.UPTIME_KUMA_URL,
            api_key=settings.UPTIME_KUMA_API_KEY,
            username=settings.UPTIME_KUMA_USERNAME,
            password=settings.UPTIME_KUMA_PASSWORD,
            enabled=settings.UPTIME_KUMA_ENABLED,
            status_page_slug=settings.UPTIME_KUMA_STATUS_PAGE_SLUG,
        )
        self.docker = docker_monitor or DockerMonitor(
            server_name=settings.NODE_NAME,
            socket_path=settings.DOCKER_SOCKET_PATH,
            enabled=settings.DOCKER_MONITOR_ENABLED,
            proxy_url=settings.DOCKER_PROXY_URL,
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

            servers = await self.beszel.get_all_servers() if self.settings.BESZEL_ENABLED else []
            by_host = {(s.hostname or s.name).casefold(): s for s in servers}
            for previous in self._cached_servers:
                key = (previous.hostname or previous.name).casefold()
                if previous.source == "beszel" and key not in by_host:
                    by_host[key] = previous.model_copy(update={
                        "status": "⚪ Нет свежих данных", "is_online": False,
                        "monitor_state": "UNKNOWN",
                    })
            servers = list(by_host.values())

            self._cached_servers = servers
            self._cached_servers_time = now
            return servers

    async def get_server_detail(self, query: str) -> Optional[ServerMetrics]:
        """Returns details for a specific server (e.g. 'bili', 'susl', 'main', 'backup')."""
        servers = await self.get_servers()
        query_l = query.strip().casefold()
        primary = self.settings.NODE_NAME if self.settings.is_primary else self.settings.PEER_NODE_NAME
        standby = self.settings.PEER_NODE_NAME if self.settings.is_primary else self.settings.NODE_NAME
        names = {"bili": primary, "main": primary, "primary": primary,
                 "susl": standby, "backup": standby, "standby": standby}
        target = names.get(query_l, query_l).casefold()
        matches = [s for s in servers if target in {s.name.casefold(), s.hostname.casefold()}]
        if not matches and target:
            matches = [s for s in servers if target in s.name.casefold() or target in s.hostname.casefold()]
        return matches[0] if len(matches) == 1 else None

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
        if not self.settings.HA_PEER_ENABLED:
            return results
        peer_url = f"{self.settings.peer_ha_url}/ha/containers"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(peer_url, allow_redirects=False) as resp:
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
        main_health, backup_health = await asyncio.gather(
            self.beszel.check_hub_health(self.settings.BESZEL_MAIN_URL),
            self.beszel.check_hub_health(self.settings.BESZEL_BACKUP_URL),
        )
        return {
            "active_hub": self.beszel.active_hub,
            "main": main_health,
            "backup": backup_health,
        }
