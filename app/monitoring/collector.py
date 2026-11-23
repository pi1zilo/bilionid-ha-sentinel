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
from app.core.nodes import BILI, SUSLIK, find_node
from app.monitoring.beszel.client import BeszelClient
from app.monitoring.docker.client import DockerMonitor
from app.monitoring.models import ContainerInfo, ServerMetrics, UptimeKumaOverview
from app.monitoring.server_logs import MAX_LOG_RESPONSE_BYTES, JournalUnavailable, read_server_journal
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
            if self.settings.BESZEL_ENABLED and not servers and not any(self.beszel.hub_available.values()):
                servers = [self.local_metrics()]
                if self.settings.HA_PEER_ENABLED:
                    remote = await self.get_peer_metrics()
                    if remote is not None:
                        servers.append(remote)
            by_host = {(s.hostname or s.name).casefold(): s for s in servers}
            fresh_names = {s.name.casefold() for s in servers}
            for previous in self._cached_servers:
                key = (previous.hostname or previous.name).casefold()
                if previous.source == "beszel" and key not in by_host and previous.name.casefold() not in fresh_names:
                    by_host[key] = previous.model_copy(update={
                        "status": "⚪ Нет свежих данных", "is_online": False,
                        "monitor_state": "UNKNOWN",
                    })
            servers = list(by_host.values())

            self._cached_servers = servers
            self._cached_servers_time = now
            return servers

    def local_metrics(self) -> ServerMetrics:
        metrics = self.beszel.get_local_fallback_metrics(self.settings.NODE_NAME)
        address = self.settings.TAILSCALE_IP
        metrics.hostname = "" if address in {"127.0.0.1", "::1"} else address
        if metrics.is_online:
            metrics.status = "🟡 Локальные метрики · Beszel недоступен"
            metrics.monitor_state = "FALLBACK"
        return metrics

    async def get_peer_metrics(self) -> ServerMetrics | None:
        url = f"{self.settings.peer_ha_url}/ha/metrics"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            async with aiohttp.ClientSession(headers=headers, timeout=aiohttp.ClientTimeout(total=3)) as session:
                async with session.get(url, allow_redirects=False) as response:
                    response.raise_for_status()
                    metrics = ServerMetrics.model_validate(await response.json())
            if metrics.name == self.settings.PEER_NODE_NAME:
                return metrics
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.debug("Peer metrics: %s", type(exc).__name__)
        return None

    async def get_server_detail(self, query: str) -> Optional[ServerMetrics]:
        """Returns details for a specific server (e.g. 'bili', 'susl', 'main', 'backup')."""
        servers = await self.get_servers()
        query = query.strip().casefold()
        local = (self.settings.NODE_NAME, self.settings.TAILSCALE_IP)
        peer = (self.settings.PEER_NODE_NAME, self.settings.PEER_TAILSCALE_IP)
        primary, standby = (local, peer) if self.settings.is_primary else (peer, local)
        node = find_node(self.settings, query)
        if node is BILI:
            name, address = primary
            aliases = ("bilionid",)
        elif node is SUSLIK:
            name, address = standby
            aliases = ("suslikvpn", "suslik")
        else:
            name, address, aliases = query, "", ()

        if address in {"127.0.0.1", "::1"}:
            address = ""
        for target in (name, address, *aliases):
            target = target.strip().casefold()
            matches = [s for s in servers if target in {s.name.casefold(), s.hostname.casefold()}]
            if target and len(matches) == 1:
                return matches[0]
        for target in (name, *aliases):
            target = target.strip().casefold()
            if not target:
                continue
            matches = [s for s in servers if target in s.name.casefold() or target in s.hostname.casefold()]
            if len(matches) == 1:
                return matches[0]
        return None

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

    async def get_peer_incidents(self) -> list[dict] | None:
        peer_url = f"{self.settings.peer_ha_url}/ha/incidents"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(peer_url, allow_redirects=False) as response:
                    response.raise_for_status()
                    document = await response.json()
            node = self.settings.PEER_NODE_NAME
            if document.get("node") == node:
                return [{**incident, "node": node} for incident in document["incidents"][:10]]
        except (aiohttp.ClientError, TimeoutError, ValueError, TypeError, KeyError, AttributeError) as exc:
            logger.debug("Peer incident journal: %s", type(exc).__name__)
        return None

    async def get_server_logs(self, peer: bool, lines: int) -> str:
        if not peer:
            if self.settings.SERVER_LOGS_ENABLED:
                return await read_server_journal(lines)
            raise JournalUnavailable
        if not self.settings.HA_PEER_ENABLED:
            raise JournalUnavailable

        url = f"{self.settings.peer_ha_url}/ha/logs"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=7)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.get(url, params={"lines": lines}, allow_redirects=False) as response:
                    if response.status != 200 or response.content_type != "text/plain":
                        raise JournalUnavailable
                    try:
                        await response.content.readexactly(MAX_LOG_RESPONSE_BYTES + 1)
                    except asyncio.IncompleteReadError as exc:
                        return exc.partial.decode("utf-8")
        except (aiohttp.ClientError, TimeoutError, UnicodeError) as exc:
            raise JournalUnavailable from exc
        raise JournalUnavailable

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
