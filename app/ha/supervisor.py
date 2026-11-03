"""
HA Supervisor implementing heartbeat, lease management, health probes,
failover takeover, recovery yield, and split-brain aversion.
"""

import asyncio
from datetime import datetime, timezone
import logging
import socket
from typing import Any, Callable, Dict, Optional
import aiohttp

from app.core.config import NodeRole, NodeState, Settings
from app.ha.http_server import HAHttpServer
from app.ha.state import HealthStatus, NodeHAInfo, PeerHealth
from app.monitoring.docker.client import DockerMonitor
from app.storage.db import Storage

logger = logging.getLogger("bilionid_ha.supervisor")


class HASupervisor:
    def __init__(
        self,
        settings: Settings,
        storage: Storage,
        start_bot_callback: Optional[Callable[[], Any]] = None,
        stop_bot_callback: Optional[Callable[[], Any]] = None,
        send_alert_callback: Optional[Callable[[str], Any]] = None,
        docker_monitor: Optional[DockerMonitor] = None,
        get_local_metrics: Optional[Callable[[], Any]] = None,
    ):
        self.settings = settings
        self.storage = storage
        self.start_bot = start_bot_callback
        self.stop_bot = stop_bot_callback
        self.send_alert = send_alert_callback
        self.docker_monitor = docker_monitor
        self.get_local_metrics = get_local_metrics

        # Initial State
        self.role = settings.NODE_ROLE
        self.state = (
            NodeState.LEADER_ACTIVE
            if self.role == NodeRole.PRIMARY
            else NodeState.FOLLOWER_PASSIVE
        )
        self.is_polling = False
        self.start_time = datetime.now(timezone.utc)
        self.running = False
        self._loop_task: Optional[asyncio.Task] = None
        self._background_tasks: set = set()

        self.peer_health = PeerHealth(
            peer_name=settings.PEER_NODE_NAME,
            peer_ip=settings.PEER_TAILSCALE_IP,
        )

        # HTTP Server for HA
        self.http_server = HAHttpServer(
            settings=settings,
            get_ha_info=self.get_ha_info,
            on_yield_request=self.handle_yield_request,
            on_force_role_request=self.handle_force_role,
            docker_monitor=docker_monitor,
            get_local_metrics=get_local_metrics,
        )

    @property
    def uptime_seconds(self) -> int:
        return int((datetime.now(timezone.utc) - self.start_time).total_seconds())

    def get_ha_info(self) -> Dict[str, Any]:
        return {
            "node_name": self.settings.NODE_NAME,
            "role": self.role.value,
            "state": self.state.value,
            "is_polling": self.is_polling,
            "uptime_seconds": self.uptime_seconds,
            "peer_name": self.peer_health.peer_name,
            "peer_reachable": self.peer_health.is_reachable,
            "peer_consecutive_failures": self.peer_health.consecutive_failures,
            "peer_consecutive_successes": self.peer_health.consecutive_successes,
            "last_seen_healthy": (
                self.peer_health.last_seen_healthy.isoformat()
                if self.peer_health.last_seen_healthy
                else None
            ),
        }

    async def start(self) -> None:
        """Starts HA HTTP server and main supervisor loop."""
        self.running = True
        await self.storage.init_db()
        await self.http_server.start()

        if self.role == NodeRole.PRIMARY:
            # Primary node acquires lease and starts bot immediately
            await self.storage.acquire_or_renew_lease(
                self.settings.NODE_NAME, self.settings.HA_LEASE_DURATION_SEC
            )
            await self.storage.record_ha_event(
                event_type="PRIMARY_STARTUP",
                source_node=self.settings.NODE_NAME,
                details="Primary node started. Polling enabled.",
            )
            # Signal Backup to yield if it was active while Main was restarting
            t = asyncio.create_task(self._send_yield_to_peer())
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)
            await self._start_polling()

            # Send operational alert on Primary startup/recovery
            msg = (
                "🟢 **ОСНОВНОЙ СЕРВЕР ЗАПУЩЕН (PRIMARY)**\n\n"
                f"✅ Узел **{self.settings.NODE_NAME}** успешно стартовал и взял управление кластером.\n"
                "📡 Telegram polling активен на Primary."
            )
            await self._notify(msg)
        else:
            await self.storage.record_ha_event(
                event_type="STANDBY_STARTUP",
                source_node=self.settings.NODE_NAME,
                details="Standby node started. Monitoring primary health.",
            )
            logger.info("Backup node initialized in STANDBY (polling disabled)")

        self._loop_task = asyncio.create_task(self._supervisor_loop())
        logger.info("HA Supervisor background loop started for role %s", self.role.value)

    async def stop(self) -> None:
        """Gracefully stops HA Supervisor and Bot polling."""
        self.running = False
        if self._loop_task:
            self._loop_task.cancel()
            try:
                await self._loop_task
            except asyncio.CancelledError:
                pass

        for t in list(self._background_tasks):
            t.cancel()
            try:
                await t
            except asyncio.CancelledError:
                pass
        self._background_tasks.clear()

        await self._stop_polling()
        await self.storage.release_lease(self.settings.NODE_NAME)
        await self.http_server.stop()
        logger.info("HA Supervisor stopped cleanly")

    async def _start_polling(self) -> None:
        if not self.is_polling:
            self.is_polling = True
            logger.info("Enabling Telegram polling on %s...", self.settings.NODE_NAME)
            if self.start_bot:
                res = self.start_bot()
                if asyncio.iscoroutine(res):
                    await res

    async def _stop_polling(self) -> None:
        if self.is_polling:
            self.is_polling = False
            logger.info("Stopping Telegram polling on %s...", self.settings.NODE_NAME)
            if self.stop_bot:
                res = self.stop_bot()
                if asyncio.iscoroutine(res):
                    await res

    async def _supervisor_loop(self) -> None:
        """Periodic supervisor loop running every HA_HEARTBEAT_INTERVAL_SEC."""
        while self.running:
            try:
                if self.role == NodeRole.PRIMARY:
                    await self._primary_cycle()
                elif self.role == NodeRole.STANDBY:
                    await self._standby_cycle()
            except Exception as e:
                logger.error("Exception in HA supervisor cycle: %s", e, exc_info=True)

            await asyncio.sleep(self.settings.HA_HEARTBEAT_INTERVAL_SEC)

    async def _primary_cycle(self) -> None:
        """Primary node cycle: renews lease, probes peer, sends heartbeat."""
        await self.storage.acquire_or_renew_lease(
            self.settings.NODE_NAME, self.settings.HA_LEASE_DURATION_SEC
        )
        if not self.is_polling and self.state == NodeState.LEADER_ACTIVE:
            await self._start_polling()

        # Probe Backup node
        await self._probe_peer()

        # Send heartbeat to peer
        await self._send_heartbeat()

    async def _standby_cycle(self) -> None:
        """Standby node cycle: continuously monitors Primary node and handles failover / recovery."""
        await self._probe_peer()

        # Evaluate failover or recovery
        if self.state == NodeState.FOLLOWER_PASSIVE:
            # Check if Primary has failed long enough to trigger takeover
            if (
                not self.peer_health.is_reachable
                and self.peer_health.consecutive_failures * self.settings.HA_HEARTBEAT_INTERVAL_SEC
                >= self.settings.HA_FAILOVER_TIMEOUT_SEC
            ):
                await self._evaluate_and_execute_takeover()

        elif self.state == NodeState.FAILOVER_TAKEOVER:
            # Standby is currently active! Renew lease
            await self.storage.acquire_or_renew_lease(
                self.settings.NODE_NAME, self.settings.HA_LEASE_DURATION_SEC
            )

            # Check if Primary has recovered and has been continuously healthy for HA_RECOVERY_TIMEOUT_SEC
            recovery_threshold_cycles = int(
                self.settings.HA_RECOVERY_TIMEOUT_SEC / self.settings.HA_HEARTBEAT_INTERVAL_SEC
            )
            if (
                self.peer_health.is_reachable
                and self.peer_health.ha_endpoint_ok
                and self.peer_health.consecutive_successes >= recovery_threshold_cycles
            ):
                await self._execute_recovery_yield()

    async def _probe_peer(self) -> None:
        """Probes the peer via HTTP health check, TCP socket, and Beszel check."""
        self.peer_health.last_probe_time = datetime.now(timezone.utc)
        peer_url = f"{self.settings.peer_ha_url}/health"

        ha_ok = False
        beszel_ok = False

        # 1. HA Endpoint Probe
        try:
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get(peer_url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get("status") == "ok":
                            ha_ok = True
        except Exception as e:
            self.peer_health.last_error = f"HA endpoint error: {e}"

        # 2. Corroborating Beszel Hub probe for Main node
        if self.role == NodeRole.STANDBY:
            try:
                timeout = aiohttp.ClientTimeout(total=3.0)
                async with aiohttp.ClientSession(timeout=timeout) as session:
                    async with session.get(
                        f"{self.settings.BESZEL_MAIN_URL}/api/health"
                    ) as resp:
                        if resp.status == 200:
                            beszel_ok = True
            except Exception:
                beszel_ok = False

        self.peer_health.ha_endpoint_ok = ha_ok
        self.peer_health.beszel_hub_ok = beszel_ok

        # Determine overall reachability
        # If either HA endpoint or Beszel responds, the node is not dead!
        if ha_ok or beszel_ok:
            self.peer_health.is_reachable = True
            self.peer_health.consecutive_successes += 1
            self.peer_health.consecutive_failures = 0
            self.peer_health.last_seen_healthy = datetime.now(timezone.utc)
        else:
            self.peer_health.is_reachable = False
            self.peer_health.consecutive_failures += 1
            self.peer_health.consecutive_successes = 0

    async def _send_heartbeat(self) -> None:
        """Sends heartbeat packet to peer."""
        peer_url = f"{self.settings.peer_ha_url}/ha/heartbeat"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        payload = {
            "source_node": self.settings.NODE_NAME,
            "role": self.role.value,
            "state": self.state.value,
        }
        try:
            timeout = aiohttp.ClientTimeout(total=2.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.post(peer_url, json=payload):
                    pass
        except Exception:
            pass

    async def _send_yield_to_peer(self) -> None:
        """Signals peer to yield polling to Primary."""
        peer_url = f"{self.settings.peer_ha_url}/ha/yield"
        headers = {"X-HA-Secret": self.settings.HA_SECRET_KEY}
        try:
            timeout = aiohttp.ClientTimeout(total=3.0)
            async with aiohttp.ClientSession(headers=headers, timeout=timeout) as session:
                async with session.post(peer_url) as resp:
                    if resp.status == 200:
                        logger.info("Successfully sent yield signal to %s", self.settings.PEER_NODE_NAME)
        except Exception as e:
            logger.debug("Yield signal to peer skipped or unreachable: %s", e)

    async def _evaluate_and_execute_takeover(self) -> None:
        """
        Safety check before promoting Backup to Active:
        1. Verify Backup itself can reach external network (e.g. Telegram API).
        2. Verify lease is expired.
        3. If all safe, promote and start polling.
        """
        logger.warning(
            "Primary node (%s) unreachable for >%ds (%d failed checks). Evaluating safe failover...",
            self.settings.PEER_NODE_NAME,
            int(self.settings.HA_FAILOVER_TIMEOUT_SEC),
            self.peer_health.consecutive_failures,
        )

        # 1. External internet connectivity check (avoid split-brain when Backup is disconnected)
        external_ok = await self._check_external_connectivity()
        if not external_ok:
            logger.error(
                "CRITICAL: Backup node cannot reach external internet / Telegram API! "
                "Aborting automatic takeover to prevent split-brain partition."
            )
            self.state = NodeState.DEGRADED
            await self.storage.record_ha_event(
                event_type="FAILOVER_ABORTED_NO_INTERNET",
                source_node=self.settings.NODE_NAME,
                details="Takeover aborted: Backup node has no external internet connectivity.",
            )
            return

        # 2. Acquire local lease
        acquired = await self.storage.acquire_or_renew_lease(
            self.settings.NODE_NAME, self.settings.HA_LEASE_DURATION_SEC
        )
        if not acquired:
            logger.warning("Could not acquire HA lease for takeover. Skipping.")
            return

        # 3. Promote Backup!
        self.state = NodeState.FAILOVER_TAKEOVER
        await self.storage.record_ha_event(
            event_type="FAILOVER_TAKEOVER",
            source_node=self.settings.NODE_NAME,
            target_node=self.settings.PEER_NODE_NAME,
            details=f"Backup node promoted to active after {self.peer_health.consecutive_failures} failed peer checks.",
        )
        logger.critical(
            "🚨 FAILOVER ACTIVATED: Promoting %s to active polling instance!",
            self.settings.NODE_NAME,
        )

        await self._start_polling()

        # Send alert to Telegram Chat
        msg = (
            "🚨 **АВАРИЯ ИНФРАСТРУКТУРЫ: FAILOVER**\n\n"
            f"⚠️ Основной сервер **{self.settings.PEER_NODE_NAME}** недоступен более {int(self.settings.HA_FAILOVER_TIMEOUT_SEC)} сек.\n"
            f"🔄 Резервный экземпляр **{self.settings.NODE_NAME}** принял управление на себя.\n"
            "Telegram polling переключен на Backup."
        )
        await self._notify(msg)

    async def _execute_recovery_yield(self) -> None:
        """Gracefully yields polling back to Primary upon full recovery."""
        logger.info(
            "Primary node (%s) continuously healthy for >%ds (%d checks). Yielding polling back...",
            self.settings.PEER_NODE_NAME,
            int(self.settings.HA_RECOVERY_TIMEOUT_SEC),
            self.peer_health.consecutive_successes,
        )

        await self._stop_polling()
        await self.storage.release_lease(self.settings.NODE_NAME)
        self.state = NodeState.FOLLOWER_PASSIVE

        await self.storage.record_ha_event(
            event_type="RECOVERY_YIELD",
            source_node=self.settings.NODE_NAME,
            target_node=self.settings.PEER_NODE_NAME,
            details="Polling gracefully yielded back to recovered Primary node.",
        )

        msg = (
            "✅ **ВОССТАНОВЛЕНИЕ: FAILBACK TO PRIMARY**\n\n"
            f"🟢 Основной сервер **{self.settings.PEER_NODE_NAME}** полностью восстановился и стабилен.\n"
            f"🔄 Резервный узел **{self.settings.NODE_NAME}** вернулся в режим ожидания (STANDBY).\n"
            "Управление возвращено на Primary."
        )
        await self._notify(msg)

    async def handle_yield_request(self) -> None:
        """Called when peer explicitly asks this node to yield polling."""
        was_active = self.is_polling or self.state == NodeState.FAILOVER_TAKEOVER
        if self.role == NodeRole.STANDBY:
            logger.info("Yield request received from Primary. Stopping polling on Standby.")
            if self.is_polling:
                await self._stop_polling()
            self.state = NodeState.FOLLOWER_PASSIVE
            await self.storage.release_lease(self.settings.NODE_NAME)
            await self.storage.record_ha_event(
                event_type="RECOVERY_YIELD",
                source_node=self.settings.NODE_NAME,
                target_node=self.settings.PEER_NODE_NAME,
                details="Standby yielded polling to Primary upon explicit request.",
            )
            if was_active:
                msg = (
                    "🔄 **ПЕРЕДАЧА УПРАВЛЕНИЯ НА PRIMARY**\n\n"
                    f"🟢 Основной сервер **{self.settings.PEER_NODE_NAME}** вернулся в строй.\n"
                    f"Резервный узел **{self.settings.NODE_NAME}** уступил управление и вернулся в режим ожидания (STANDBY)."
                )
                await self._notify(msg)

    async def handle_force_role(self, new_role_str: str) -> None:
        """Forces role switch manually."""
        new_role_str = new_role_str.upper()
        if new_role_str == "ACTIVE":
            logger.warning("Manual takeover forced!")
            self.state = NodeState.MANUAL_OVERRIDE
            await self._start_polling()
        elif new_role_str == "STANDBY":
            logger.warning("Manual standby forced!")
            await self._stop_polling()
            self.state = NodeState.FOLLOWER_PASSIVE

    def handle_telegram_conflict_409(self) -> None:
        """
        Called if Telegram Bot API returns 409 Conflict.
        If on Backup: immediately abort polling to resolve split-brain!
        """
        if self.role == NodeRole.STANDBY and self.is_polling:
            logger.critical(
                "⚡ SPLIT-BRAIN CONFLICT DETECTED (Telegram 409)! "
                "Stepping down Backup polling immediately to preserve single-leader invariant."
            )
            t = asyncio.create_task(self._emergency_stepdown())
            self._background_tasks.add(t)
            t.add_done_callback(self._background_tasks.discard)

    async def _emergency_stepdown(self) -> None:
        await self._stop_polling()
        self.state = NodeState.FOLLOWER_PASSIVE
        await self.storage.record_ha_event(
            event_type="SPLIT_BRAIN_AVERTED",
            source_node=self.settings.NODE_NAME,
            details="Telegram 409 Conflict handled. Polling stopped on Backup immediately.",
        )

    async def _check_external_connectivity(self) -> bool:
        """Probes public Telegram API to ensure outbound internet works."""
        try:
            timeout = aiohttp.ClientTimeout(total=4.0)
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.get("https://api.telegram.org", timeout=timeout) as resp:
                    return resp.status in (200, 302, 404, 400)
        except Exception:
            return False

    async def _notify(self, message: str) -> None:
        if self.send_alert:
            try:
                res = self.send_alert(message)
                if asyncio.iscoroutine(res):
                    await res
            except Exception as e:
                logger.error("Failed to deliver alert message: %s", e)
