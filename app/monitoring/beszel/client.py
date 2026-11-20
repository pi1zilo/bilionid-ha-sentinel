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
        main_url: str = "http://100.68.106.100:29001",
        backup_url: str = "http://100.77.242.10:29000",
        username: Optional[str] = None,
        password: Optional[str] = None,
        auth_token: Optional[str] = None,
        timeout_sec: float = 4.0,
        backup_username: str | None = None,
        backup_password: str | None = None,
        backup_auth_token: str | None = None,
    ):
        self.main_url = main_url.rstrip("/")
        self.backup_url = backup_url.rstrip("/")
        self.credentials = {
            self.main_url: (username, password),
            self.backup_url: (backup_username, backup_password),
        }
        self.static_tokens = {
            self.main_url: auth_token,
            self.backup_url: backup_auth_token,
        }
        self.timeout = aiohttp.ClientTimeout(total=timeout_sec)
        self.active_hub = self.main_url
        self._tokens: Dict[str, str] = {}
        self.hub_available: dict[str, bool] = {}

    async def get_auth_token(self, session: aiohttp.ClientSession, base_url: str) -> Optional[str]:
        """Authenticates with PocketBase if credentials are provided."""
        if self.static_tokens.get(base_url):
            return self.static_tokens[base_url]
        if self._tokens.get(base_url):
            return self._tokens[base_url]

        username, password = self.credentials.get(base_url, (None, None))
        if not username or not password:
            return None

        endpoint = f"{base_url}/api/collections/users/auth-with-password"
        try:
            async with session.post(
                endpoint, json={"identity": username, "password": password},
                timeout=self.timeout, allow_redirects=False,
            ) as response:
                if response.status == 200:
                    token = (await response.json()).get("token")
                    if token:
                        self._tokens[base_url] = token
                        return token
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.debug("Beszel authentication: %s", type(exc).__name__)

        return None

    async def _fetch_from_hub(
        self, session: aiohttp.ClientSession, hub_url: str
    ) -> Optional[List[Dict[str, Any]]]:
        """Fetches systems collection records from Beszel Hub."""
        headers = {}
        token = await self.get_auth_token(session, hub_url)
        if token:
            headers["Authorization"] = token

        url = f"{hub_url}/api/collections/systems/records"
        try:
            async with session.get(
                url, params={"perPage": 50}, headers=headers, timeout=self.timeout, allow_redirects=False
            ) as resp:
                if resp.status != 200:
                    self._tokens.pop(hub_url, None)
                    logger.warning("Beszel systems: HTTP %s", resp.status)
                    return None
                items = (await resp.json())["items"]
            if not isinstance(items, list):
                return None
            await asyncio.gather(*(self._attach_stats(session, hub_url, headers, item) for item in items))
            return items
        except (aiohttp.ClientError, TimeoutError, ValueError, KeyError, TypeError) as exc:
            logger.debug("Beszel systems: %s", type(exc).__name__)

        return None

    async def _attach_stats(self, session, hub_url: str, headers: dict, item: dict) -> None:
        system_id = item.get("id", "")
        if item.get("status") != "up" or not isinstance(system_id, str) or not system_id.isalnum():
            return
        endpoint = f"{hub_url}/api/collections/system_stats/records"
        params = {"filter": f'system="{system_id}" && type="1m"', "sort": "-created", "perPage": 1}
        try:
            async with session.get(
                endpoint, params=params, headers=headers, timeout=self.timeout, allow_redirects=False
            ) as response:
                if response.status == 200 and (items := (await response.json()).get("items")):
                    item["_stats"] = items[0]
        except (aiohttp.ClientError, TimeoutError, ValueError) as exc:
            logger.debug("Beszel detail: %s", type(exc).__name__)

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

        status_str = item.get("status", "")
        is_online = (status_str == "up")
        state = {"up": "UP", "down": "DOWN", "paused": "PAUSED", "pending": "PENDING"}.get(
            status_str, "UNKNOWN"
        )
        stats_record = item.get("_stats") or {}
        stats = stats_record.get("stats") or {}
        if isinstance(stats, str):
            import json
            stats = json.loads(stats)
        cpu = float(stats.get("cpu", info.get("cpu", 0)) or 0)
        memory_bytes = stats.get("mem")
        if isinstance(memory_bytes, list) and len(memory_bytes) >= 2:
            ram_total, ram_used = (float(value) / 1024**3 for value in memory_bytes[:2])
        else:
            ram_total = float(stats.get("m", 0) or 0)
            ram_used = float(stats.get("mu", 0) or 0)
        ram_percent = float(stats.get("mp", info.get("mp", 0)) or 0)
        disk_used = float(stats.get("du", 0) or 0)
        disk_total = float(stats.get("d", 0) or 0)
        disk_free = max(0.0, disk_total - disk_used)
        disk_percent = float(stats.get("dp", info.get("dp", 0)) or 0)

        load = stats.get("la", info.get("la", [0.0, 0.0, 0.0]))
        if not isinstance(load, list) or len(load) < 3:
            load = [0.0, 0.0, 0.0]

        uptime_sec = int(info.get("u", 0))
        uptime_s = format_uptime(uptime_sec) if uptime_sec > 0 else "Н/Д"

        temp = info.get("dt")
        temp_val = float(temp) if isinstance(temp, (int, float)) and temp else None
        last_updated = stats_record.get("created") or item.get("updated") or ""

        return ServerMetrics(
            name=name,
            entity_id=str(item.get("id") or item.get("host") or name),
            hostname=item.get("host", ""),
            status={"UP": "🟢 Online", "DOWN": "🔴 Offline"}.get(state, f"⚪ {state}"),
            monitor_state=state,
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
            temperature=temp_val,
            last_updated=last_updated,
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

            now_str = datetime.now(timezone.utc).isoformat()

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
        Attempts to fetch server metrics from the Main Beszel Hub.
        If Main fails, seamlessly switches to Backup Hub.
        Returns metrics for both Main and Backup nodes.
        """
        async with aiohttp.ClientSession() as session:
            hubs = list(dict.fromkeys(url for url in (self.main_url, self.backup_url) if url))
            snapshots = await asyncio.gather(*(self._fetch_from_hub(session, url) for url in hubs))
        self.hub_available = {url: items is not None for url, items in zip(hubs, snapshots)}
        self.active_hub = next((url for url, items in zip(hubs, snapshots) if items), "—")
        servers: dict[str, ServerMetrics] = {}
        for items in snapshots:
            for item in items or ():
                try:
                    metric = self._parse_system_record(item)
                except (AttributeError, TypeError, ValueError):
                    logger.warning("Skipping an invalid Beszel record")
                    continue
                key = (metric.hostname or metric.name).casefold()
                servers.setdefault(key, metric)
        return list(servers.values())

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
