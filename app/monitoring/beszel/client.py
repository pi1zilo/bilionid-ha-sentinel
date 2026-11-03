"""
Beszel Hub client with automatic Hub failover (Main Hub -> Backup Hub)
and PocketBase API support.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Dict, List, Optional
import aiohttp
import psutil

from app.monitoring.models import ServerMetrics

logger = logging.getLogger("bilionid_ha.beszel")


def format_uptime(seconds: int) -> str:
    """Formats seconds into Russian human-readable uptime, e.g. '5 дней 2 часа'."""
    if seconds <= 0:
        return "менее 1 мин"
    days, rem = divmod(seconds, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, _ = divmod(rem, 60)

    parts = []
    if days > 0:
        # Russian pluralization for days
        if days % 10 == 1 and days % 100 != 11:
            d_word = "день"
        elif 2 <= days % 10 <= 4 and not (12 <= days % 100 <= 14):
            d_word = "дня"
        else:
            d_word = "дней"
        parts.append(f"{days} {d_word}")

    if hours > 0:
        if hours % 10 == 1 and hours % 100 != 11:
            h_word = "час"
        elif 2 <= hours % 10 <= 4 and not (12 <= hours % 100 <= 14):
            h_word = "часа"
        else:
            h_word = "часов"
        parts.append(f"{hours} {h_word}")

    if not parts or (days == 0 and minutes > 0):
        parts.append(f"{minutes} мин")

    return " ".join(parts[:2])


class BeszelClient:
    def __init__(
        self,
        main_url: str = "http://100.64.0.1:29001",
        backup_url: str = "http://100.64.0.2:29000",
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout_sec: float = 2.0,
    ):
        self.main_url = main_url.rstrip("/")
        self.backup_url = backup_url.rstrip("/")
        self.username = username
        self.password = password
        self.auth_token = auth_token
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec, connect=1.0)
        self.active_hub = self.main_url
        self._tokens: Dict[str, str] = {}
        if auth_token:
            self._tokens[self.main_url] = auth_token
            self._tokens[self.backup_url] = auth_token

    async def get_auth_token(self, session: aiohttp.ClientSession, base_url: str) -> Optional[str]:
        """Authenticates with PocketBase if credentials are provided."""
        if self._tokens.get(base_url):
            return self._tokens[base_url]

        if not self.username or not self.password:
            return None

        # PocketBase authentication endpoints
        auth_endpoints = [
            f"{base_url}/api/collections/users/auth-with-password",
            f"{base_url}/api/admins/auth-with-password",
            f"{base_url}/api/collections/_superusers/auth-with-password",
        ]

        payload = {"identity": self.username, "password": self.password}

        for ep in auth_endpoints:
            try:
                async with session.post(ep, json=payload, timeout=self.timeout) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        token = data.get("token")
                        if token:
                            self._tokens[base_url] = token
                            return token
            except Exception as e:
                logger.debug("Failed auth attempt against %s: %s", ep, e)

        return None

    async def _fetch_from_hub(
        self, session: aiohttp.ClientSession, hub_url: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetches systems collection records from Beszel Hub."""
        headers = {}
        token = await self.get_auth_token(session, hub_url)
        if token:
            headers["Authorization"] = token

        url = f"{hub_url}/api/collections/systems/records?perPage=50"
        try:
            async with session.get(url, headers=headers, timeout=self.timeout) as resp:
                if resp.status == 200:
                    data = await resp.json()
                    items = data.get("items", [])
                    return items
                elif resp.status in (401, 403):
                    # Invalidate cached token
                    self._tokens.pop(hub_url, None)
                    logger.warning("Beszel Hub %s returned HTTP %s (auth issue)", hub_url, resp.status)
                else:
                    logger.warning("Beszel Hub %s returned HTTP %s", hub_url, resp.status)
        except Exception as e:
            logger.debug("Error connecting to Beszel Hub %s: %s", hub_url, e)

        return None

    def _parse_system_record(self, item: Dict[str, Any]) -> ServerMetrics:
        """Parses a PocketBase system record into typed ServerMetrics."""
        name = item.get("name") or item.get("host") or "Unknown"
        info = item.get("info") or {}
        if isinstance(info, str):
            import json
            try:
                info = json.loads(info)
            except Exception:
                info = {}

        status_str = item.get("status", "up")
        is_online = (status_str == "up")

        # Beszel info structure typically contains:
        # cpu: cpu usage percent
        # m: memory used (GB)
        # mt: memory total (GB)
        # mp: memory percent
        # d: disk used (GB)
        # dt: disk total (GB)
        # dp: disk percent
        # l: load average [1m, 5m, 15m]
        # u: uptime seconds
        # c: containers total/running
        cpu = float(info.get("cpu", 0.0))
        ram_used = float(info.get("m", 0.0))
        ram_total = float(info.get("mt", 0.0))
        ram_percent = float(info.get("mp", (ram_used / ram_total * 100) if ram_total > 0 else 0.0))

        disk_used = float(info.get("d", 0.0))
        disk_total = float(info.get("dt", 0.0))
        disk_free = max(0.0, disk_total - disk_used)
        disk_percent = float(info.get("dp", (disk_used / disk_total * 100) if disk_total > 0 else 0.0))

        load = info.get("l", [0.0, 0.0, 0.0])
        if not isinstance(load, list) or len(load) < 3:
            load = [0.0, 0.0, 0.0]

        uptime_sec = int(info.get("u", 0))
        uptime_s = format_uptime(uptime_sec) if uptime_sec > 0 else "Н/Д"

        containers_total = int(info.get("c", 0))
        containers_running = int(info.get("cr", containers_total))
        containers_stopped = max(0, containers_total - containers_running)

        temp = info.get("t")
        temp_val = float(temp) if temp is not None else None

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

        return ServerMetrics(
            name=name,
            hostname=item.get("host", ""),
            status="🟢 Online" if is_online else "🔴 Offline",
            is_online=is_online,
            cpu_percent=round(cpu, 1),
            ram_used_gb=round(ram_used, 1),
            ram_total_gb=round(ram_total, 1),
            ram_percent=round(ram_percent, 1),
            disk_used_gb=round(disk_used, 1),
            disk_total_gb=round(disk_total, 1),
            disk_free_gb=round(disk_free, 1),
            disk_percent=round(disk_percent, 1),
            load_1m=round(float(load[0]), 2),
            load_5m=round(float(load[1]), 2),
            load_15m=round(float(load[2]), 2),
            uptime_str=uptime_s,
            uptime_seconds=uptime_sec,
            docker_containers_total=containers_total,
            docker_containers_running=containers_running,
            docker_containers_stopped=containers_stopped,
            temperature=temp_val,
            last_updated=now_str,
            source="beszel",
        )

    def get_local_fallback_metrics(self, server_name: str) -> ServerMetrics:
        """Collects real local host metrics using psutil as fallback."""
        try:
            cpu = psutil.cpu_percent(interval=None)
            mem = psutil.virtual_memory()
            disk = psutil.disk_usage("/")
            load = [0.0, 0.0, 0.0]
            if hasattr(psutil, "getloadavg"):
                load = list(psutil.getloadavg())

            boot_time = psutil.boot_time()
            uptime_sec = int(datetime.now().timestamp() - boot_time)

            now_str = datetime.now(timezone.utc).strftime("%H:%M:%S")

            return ServerMetrics(
                name=server_name,
                hostname="local",
                status="🟢 Online (Local)",
                is_online=True,
                cpu_percent=round(cpu, 1),
                ram_used_gb=round((mem.total - mem.available) / (1024**3), 1),
                ram_total_gb=round(mem.total / (1024**3), 1),
                ram_percent=round(mem.percent, 1),
                disk_used_gb=round(disk.used / (1024**3), 1),
                disk_total_gb=round(disk.total / (1024**3), 1),
                disk_free_gb=round(disk.free / (1024**3), 1),
                disk_percent=round(disk.percent, 1),
                load_1m=round(load[0], 2),
                load_5m=round(load[1], 2),
                load_15m=round(load[2], 2),
                uptime_str=format_uptime(uptime_sec),
                uptime_seconds=uptime_sec,
                last_updated=now_str,
                source="local_fallback",
            )
        except Exception as e:
            logger.error("Error gathering local fallback metrics: %s", e)
            return ServerMetrics(
                name=server_name,
                status="🔴 Offline",
                is_online=False,
                source="error",
                error_message=str(e),
                last_updated=datetime.now(timezone.utc).strftime("%H:%M:%S"),
            )

    async def get_all_servers(self) -> List[ServerMetrics]:
        """
        Attempts to fetch server metrics from the active Beszel Hub.
        If active Hub fails, seamlessly switches to the other Hub.
        Remembers the working Hub to prevent timeout hangs.
        """
        async with aiohttp.ClientSession() as session:
            # Try current active hub first
            hubs_to_try = [self.active_hub]
            other_hub = self.backup_url if self.active_hub == self.main_url else self.main_url
            hubs_to_try.append(other_hub)

            for hub_url in hubs_to_try:
                items = await self._fetch_from_hub(session, hub_url)
                if items is not None:
                    if self.active_hub != hub_url:
                        logger.info("Beszel Hub switched active hub to %s", hub_url)
                        self.active_hub = hub_url
                    return [self._parse_system_record(item) for item in items]

            logger.warning("Both Beszel Hubs are unreachable.")
            return []

    async def get_server_by_query(self, query: str) -> Optional[ServerMetrics]:
        """Finds server by name or substring query (e.g. 'bili', 'susl')."""
        servers = await self.get_all_servers()
        query_l = query.lower()
        for s in servers:
            if query_l in s.name.lower() or query_l in s.hostname.lower():
                return s
        return None

    async def check_hub_health(self, hub_url: str) -> Dict[str, Any]:
        """Checks responsiveness of a specific Beszel Hub."""
        start_t = datetime.now()
        async with aiohttp.ClientSession() as session:
            try:
                # Beszel / PocketBase health check
                async with session.get(f"{hub_url}/api/health", timeout=self.timeout) as resp:
                    latency_ms = (datetime.now() - start_t).total_seconds() * 1000
                    return {
                        "url": hub_url,
                        "available": resp.status == 200,
                        "status_code": resp.status,
                        "latency_ms": round(latency_ms, 1),
                    }
            except Exception as e:
                return {
                    "url": hub_url,
                    "available": False,
                    "error": str(e),
                }
