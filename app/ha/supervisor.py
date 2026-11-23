"""Peer diagnostics and the standby's independent primary watchdog."""

import asyncio
import logging
from datetime import datetime, timezone
from typing import Callable
from zoneinfo import ZoneInfo

import aiohttp

from app.core.config import Settings
from app.core.nodes import node_for_role
from app.ha.handover import TwoNodeHandover
from app.ha.state import PeerHealth
from app.services.monitoring import MonitoringService

logger = logging.getLogger("bilionid_ha.supervisor")


class HASupervisor:
    def __init__(self, settings: Settings, monitoring: MonitoringService):
        self.settings = settings
        self.monitoring = monitoring
        self.start_time = datetime.now(timezone.utc)
        self.peer_health = PeerHealth(peer_name=settings.PEER_NODE_NAME, peer_ip=settings.PEER_TAILSCALE_IP)
        self.peer_polling: bool | None = None
        self.bot_status: Callable[[], dict] = dict
        self.polling_enabled = settings.polling_enabled and not settings.HA_AUTO_FAILOVER
        self.handover: TwoNodeHandover | None = None

    def get_ha_info(self) -> dict:
        bot = self.bot_status()
        polling_ready = bool(bot.get("polling_ready"))
        desired = self.polling_enabled
        state = "FOLLOWER_PASSIVE"
        if desired:
            state = "LEADER_ACTIVE" if polling_ready else "DEGRADED"
        return {
            **bot,
            "node_name": self.settings.NODE_NAME,
            "role": self.settings.NODE_ROLE.value,
            "state": state,
            "polling_enabled": desired,
            "polling_ready": polling_ready,
            "uptime_seconds": int((datetime.now(timezone.utc) - self.start_time).total_seconds()),
            "peer_configured": self.settings.HA_PEER_ENABLED,
            "peer_name": self.peer_health.peer_name,
            "peer_reachable": self.peer_health.is_reachable,
            "peer_polling_ready": self.peer_polling,
            "last_peer_seen": self.peer_health.last_seen_healthy,
        }

    async def probe_peer(self) -> None:
        timeout = aiohttp.ClientTimeout(total=3)
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        url = f"{self.settings.peer_ha_url}/ha/status"
        is_reachable = False
        self.peer_polling = None
        detail = "HA API не ответил."
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(url, headers=headers, allow_redirects=False) as response:
                    response.raise_for_status()
                    status = await response.json()
            if status.get("node_name") != self.settings.PEER_NODE_NAME:
                raise ValueError("Unexpected peer identity")
            is_reachable = True
            self.peer_polling = bool(status.get("polling_ready"))
            detail = "HA API отвечает. Бот не получает команды Telegram."
            if status.get("last_poll_error") == "TelegramConflictError":
                detail = "Telegram отклонил polling. Другой экземпляр уже получает команды."
        except (aiohttp.ClientError, TimeoutError, ValueError, AttributeError) as exc:
            logger.debug("Peer status check: %s", type(exc).__name__)
            if isinstance(exc, aiohttp.ClientResponseError):
                detail = f"HA API вернул HTTP {exc.status}."
            elif isinstance(exc, TimeoutError):
                detail = "HA API не ответил за 3 с."

        self.peer_health.is_reachable = is_reachable
        if is_reachable:
            self.peer_health.last_seen_healthy = datetime.now(timezone.utc)

        if self.settings.HA_WATCH_PRIMARY and not self.polling_enabled:
            node = node_for_role(not self.settings.is_primary)
            if not is_reachable and self.peer_health.last_seen_healthy:
                previous = self.peer_health.last_seen_healthy.astimezone(ZoneInfo(self.settings.ALERT_TIMEZONE))
                detail += f" Последний ответ {previous:%d.%m %H:%M:%S %Z}."
            await self.monitoring.source_health(
                "HA", self.settings.PEER_NODE_NAME, f"{node.label} · бот Telegram",
                is_reachable and bool(self.peer_polling), detail, node=node,
                recovery_detail="HA API отвечает. Бот снова получает команды Telegram.",
            )

    async def run(self) -> None:
        while True:
            try:
                await self.probe_peer()
            except Exception:
                logger.exception("Peer watch failed")
            await asyncio.sleep(self.settings.HA_HEARTBEAT_INTERVAL_SEC)
