"""
Background alert watcher for container failures, server reachability, and critical resource alerts.
Runs exclusively on the active cluster leader to ensure single-notifier semantics.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, Optional
from app.core.config import Settings
from app.monitoring.collector import MetricsCollector

logger = logging.getLogger("bilionid_ha.watcher")


class AlertWatcher:
    def __init__(
        self,
        settings: Settings,
        collector: MetricsCollector,
        notify_callback: Callable[[str], Any],
        interval_sec: float = 15.0,
    ):
        self.settings = settings
        self.collector = collector
        self.notify = notify_callback
        self.interval_sec = interval_sec
        self.running = False
        self._task: Optional[asyncio.Task] = None

        # State tracking: key = "node:container_name", value = status string
        self._container_states: Dict[str, str] = {}
        self._containers_initialized = False

        # State tracking: key = server_name, value = is_online bool
        self._server_online_states: Dict[str, bool] = {}
        self._servers_initialized = False

        # Resource alert cooldowns: key = "server:metric", value = timestamp float
        self._resource_alert_cooldowns: Dict[str, float] = {}

    def start(self) -> None:
        """Starts background monitoring loop."""
        if self.running:
            return
        self.running = True
        self._task = asyncio.create_task(self._watch_loop())
        logger.info("System Alert Watcher started (interval: %.1fs)", self.interval_sec)

    async def stop(self) -> None:
        """Stops background monitoring loop cleanly."""
        if not self.running:
            return
        self.running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None
        logger.info("System Alert Watcher stopped")

    async def _watch_loop(self) -> None:
        """Periodic watcher loop."""
        # Initial small delay to let systems settle
        await asyncio.sleep(3.0)
        while self.running:
            try:
                await self._check_containers()
                await self._check_servers()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("Error in AlertWatcher cycle: %s", e, exc_info=True)

            try:
                await asyncio.sleep(self.interval_sec)
            except asyncio.CancelledError:
                break

    async def _check_containers(self) -> None:
        """Inspects all containers across both nodes for state transitions."""
        try:
            container_map = await self.collector.get_all_containers()
        except Exception as e:
            logger.debug("Could not collect containers in watcher: %s", e)
            return

        current_states: Dict[str, str] = {}
        for node_name, containers in container_map.items():
            for c in containers:
                key = f"{node_name}:{c.name}"
                current_states[key] = (c.state or "").lower()

        if not self._containers_initialized:
            self._container_states = current_states
            self._containers_initialized = True
            return

        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        for key, current_state in current_states.items():
            prev_state = self._container_states.get(key)
            if not prev_state:
                continue
            node_name, container_name = key.split(":", 1)

            # Container failure transition (running -> exited / dead / restarting)
            if prev_state == "running" and current_state in ("exited", "dead", "restarting"):
                msg = (
                    f"⚠️ **СБОЙ КОНТЕЙНЕРА НА {node_name}**\n\n"
                    f"📦 Контейнер: `{container_name}`\n"
                    f"📊 Статус: `{current_state.upper()}`\n"
                    f"🖥️ Узел: **{node_name}**\n"
                    f"⏱️ Время: `{now_str}`"
                )
                await self._send_alert(msg)

            # Container recovery transition (exited / dead / restarting -> running)
            elif prev_state in ("exited", "dead", "restarting") and current_state == "running":
                msg = (
                    f"✅ **КОНТЕЙНЕР ВОССТАНОВЛЕН НА {node_name}**\n\n"
                    f"📦 Контейнер: `{container_name}` снова активен и работает.\n"
                    f"🖥️ Узел: **{node_name}**\n"
                    f"⏱️ Время: `{now_str}`"
                )
                await self._send_alert(msg)

        self._container_states = current_states

    async def _check_servers(self) -> None:
        """Inspects Beszel monitored server telemetry and resource thresholds."""
        try:
            servers = await self.collector.get_servers()
        except Exception as e:
            logger.debug("Could not collect servers in watcher: %s", e)
            return

        if not servers:
            return

        now = asyncio.get_running_loop().time()
        now_str = datetime.now(timezone.utc).strftime("%H:%M:%S UTC")

        for s in servers:
            name = s.name
            is_online = s.is_online

            if not self._servers_initialized:
                continue

            prev_online = self._server_online_states.get(name)
            if prev_online is True and not is_online:
                msg = (
                    f"🔴 **СЕРВЕР НЕДОСТУПЕН (BESZEL)**\n\n"
                    f"🖥️ Сервер: **{name}** ({s.hostname})\n"
                    f"📊 Статус: `{s.status}`\n"
                    f"⏱️ Время: `{now_str}`"
                )
                await self._send_alert(msg)
            elif prev_online is False and is_online:
                msg = (
                    f"🟢 **СЕРВЕР ВОССТАНОВЛЕН (BESZEL)**\n\n"
                    f"🖥️ Сервер: **{name}** ({s.hostname}) снова в сети.\n"
                    f"⏱️ Время: `{now_str}`"
                )
                await self._send_alert(msg)

            # Critical resource thresholds (>95% CPU, >95% RAM, >92% Disk)
            if is_online:
                if s.cpu_percent >= 95.0:
                    cd_key = f"{name}:cpu"
                    if now - self._resource_alert_cooldowns.get(cd_key, 0) > 900:
                        self._resource_alert_cooldowns[cd_key] = now
                        msg = (
                            f"🔥 **КРИТИЧЕСКАЯ НАГРУЗКА CPU: {name}**\n\n"
                            f"⚙️ CPU: `{s.cpu_percent:.1f}%`\n"
                            f"🖥️ Узел: **{name}**\n"
                            f"⏱️ Время: `{now_str}`"
                        )
                        await self._send_alert(msg)

                if s.ram_percent >= 95.0:
                    cd_key = f"{name}:ram"
                    if now - self._resource_alert_cooldowns.get(cd_key, 0) > 900:
                        self._resource_alert_cooldowns[cd_key] = now
                        msg = (
                            f"🔥 **КРИТИЧЕСКИЙ РАСХОД ПАМЯТИ: {name}**\n\n"
                            f"🧠 RAM: `{s.ram_percent:.1f}%` ({s.ram_used_gb:.1f}/{s.ram_total_gb:.1f} GB)\n"
                            f"🖥️ Узел: **{name}**\n"
                            f"⏱️ Время: `{now_str}`"
                        )
                        await self._send_alert(msg)

                if s.disk_percent >= 92.0:
                    cd_key = f"{name}:disk"
                    if now - self._resource_alert_cooldowns.get(cd_key, 0) > 900:
                        self._resource_alert_cooldowns[cd_key] = now
                        msg = (
                            f"💾 **ДИСК ПОЧТИ ЗАПОЛНЕН: {name}**\n\n"
                            f"💿 Диск: `{s.disk_percent:.1f}%`\n"
                            f"🖥️ Узел: **{name}**\n"
                            f"⏱️ Время: `{now_str}`"
                        )
                        await self._send_alert(msg)

        self._server_online_states = {s.name: s.is_online for s in servers}
        self._servers_initialized = True

    async def _send_alert(self, msg: str) -> None:
        """Dispatches notification via callback."""
        try:
            logger.info("AlertWatcher dispatching notification: %s", msg.splitlines()[0])
            res = self.notify(msg)
            if asyncio.iscoroutine(res):
                await res
        except Exception as e:
            logger.error("AlertWatcher failed to dispatch notification: %s", e)
