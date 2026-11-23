"""Schedule observations, normalize incoming events and deliver queued alerts."""

import asyncio
import logging
import re
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError

from app.core.config import Settings
from app.core.nodes import BILI, SUSLIK, Node, find_node, node_for_role
from app.monitoring.beszel.notifications import HOST_STATUS, parse_threshold
from app.monitoring.collector import MetricsCollector
from app.monitoring.events import Observation
from app.monitoring.models import ServerMetrics
from app.storage.db import Storage

logger = logging.getLogger("bilionid_ha.monitoring")


def parse_event_time(value: object) -> datetime | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, str)) and str(value).isdigit():
        return datetime.fromtimestamp(int(value), tz=timezone.utc)
    if not isinstance(value, str):
        raise ValueError("Invalid timestamp")
    timestamp = datetime.fromisoformat(value)
    return timestamp if timestamp.tzinfo else timestamp.replace(tzinfo=timezone.utc)


def url_host(value: object) -> str | None:
    if isinstance(value, str):
        try:
            return urlsplit(value).hostname
        except ValueError:
            pass
    return None


class MonitoringService:
    def __init__(self, settings: Settings, storage: Storage, collector: MetricsCollector):
        self.settings = settings
        self.storage = storage
        self.collector = collector
        self.tz = ZoneInfo(settings.ALERT_TIMEZONE)
        self._failures: dict[tuple[str, str], int] = {}
        self._kuma_disagreements: dict[str, tuple[str, int]] = {}
        self._running_containers: set[str] = set()

    def _stamp(self, at: datetime) -> str:
        return at.astimezone(self.tz).strftime("%d.%m.%Y %H:%M:%S %Z")

    def _render(
        self, event: Observation, at: datetime, incident_id: int,
        started: datetime | None = None, initial_detail: str = "",
    ) -> str:
        origin = node_for_role(self.settings.is_primary).label
        source = f"{event.source} · {origin} #{incident_id}"
        stamp = self._stamp(at)
        lines = []
        if started is None:
            lines.append(f"🔴 {event.name}")
            if event.detail:
                lines.append(event.detail[:500])
            if event.node:
                lines.append(f"Ответственный — {event.node.mention}")
            lines.append(f"{stamp} · {source}")
        else:
            seconds = max(0, int((at - started).total_seconds()))
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            duration = " ".join(f"{value} {unit}" for value, unit in ((hours, "ч"), (minutes, "мин"), (seconds, "с")) if value)
            lines.append(f"🟢 {event.name} — восстановлено")
            if initial_detail:
                lines.append(f"Было: {initial_detail[:500]}")
            if event.detail and event.detail != initial_detail:
                lines.append(f"Сейчас: {event.detail[:500]}")
            local_start, local_end = started.astimezone(self.tz), at.astimezone(self.tz)
            start = local_start.strftime("%H:%M:%S") if local_start.date() == local_end.date() else self._stamp(started)
            lines.append(f"{event.duration_label}: {duration or '0 с'} · {start} → {stamp}")
            lines.append(source)
        if event.link:
            lines.append(event.link)
        return "\n".join(lines)

    def _render_event(self, source: str, name: str, detail: str, at: datetime, event_id: int, node: Node | None = None) -> str:
        origin = node_for_role(self.settings.is_primary).label
        contact = f"\nОтветственный — {node.mention}" if node else ""
        return f"ℹ️ {name}\n{detail}{contact}\n{self._stamp(at)} · {source} · {origin} #{event_id}"

    async def observe(self, observation: Observation) -> None:
        await self.storage.apply_observation(observation, self._render)

    async def report_handover(self, active: bool) -> None:
        name = node_for_role(self.settings.is_primary).label
        now = datetime.now(timezone.utc)
        detail = "Узел начал принимать команды Telegram." if active else "Узел прекратил принимать команды Telegram."
        await self.storage.record_event(
            "HA", name, detail, now,
            lambda event_id: self._render_event("HA", name, detail, now, event_id),
        )

    async def source_health(
        self, source: str, entity: str, name: str, available: bool, detail: str = "", *,
        node: Node | None = None, recovery_detail: str = "",
    ) -> None:
        key = (source, entity)
        if available:
            self._failures.pop(key, None)
            await self.observe(Observation(source, entity, name, "UP", recovery_detail, node=node))
            return
        count = self._failures.get(key, 0) + 1
        self._failures[key] = min(count, self.settings.MONITOR_SOURCE_FAILURES)
        if count >= self.settings.MONITOR_SOURCE_FAILURES:
            await self.observe(Observation(source, entity, name, "DOWN", detail, node=node))

    async def _check_beszel(self) -> None:
        servers = await self.collector.get_servers(force_refresh=True)
        beszel = self.collector.beszel
        for role, url, node in (("Main", beszel.main_url, BILI), ("Backup", beszel.backup_url, SUSLIK)):
            if url and (role == "Main" or url != beszel.main_url):
                await self.source_health(
                    "Beszel", f"hub:{role}", f"{node.label} · API хаба Beszel",
                    beszel.hub_available.get(url, False), "Хаб не вернул список систем.", node=node,
                    recovery_detail="Список систем снова доступен.",
                )
        for server in servers:
            if server.source != "beszel":
                continue
            if server.monitor_state in {"UP", "DOWN"}:
                key = (server.hostname or server.name).casefold()
                node = find_node(self.settings, server.hostname, server.name)
                name = node.label if node else server.name
                detail = (
                    "Хаб потерял связь с агентом." if server.monitor_state == "DOWN"
                    else "Хаб снова получает данные от агента на сервере."
                )
                await self.observe(Observation("Beszel", f"host:{key}", f"{name} · агент Beszel", server.monitor_state, detail, node=node))
                if server.monitor_state == "UP":
                    await self._check_beszel_uptime(server, key)
                    if not self.settings.BESZEL_WEBHOOK_SECRET:
                        await self._check_resource_limits(server, key)

    async def _check_resource_limits(self, server: ServerMetrics, host: str) -> None:
        node = find_node(self.settings, server.hostname, server.name)
        name = node.label if node else server.name
        for metric, label, value, limit, extra in (
            ("cpu", "загрузка CPU", server.cpu_percent, 95, ""),
            ("ram", "использование памяти", server.ram_percent, 95,
             f" · {server.ram_used_gb:g} из {server.ram_total_gb:g} ГиБ" if server.ram_total_gb else ""),
            ("disk", "заполнение диска", server.disk_percent, 92,
             f" · свободно {server.disk_free_gb:g} ГиБ" if server.disk_total_gb else ""),
        ):
            state = "DOWN" if value >= limit else "UP"
            detail = f"{value:.1f}% · порог {limit}%{extra}"
            await self.observe(Observation(
                "Beszel", f"limit:{host}:{metric}", f"{name} · {label}", state, detail,
                duration_label="Вне порога", node=node,
            ))

    async def _check_containers(self) -> None:
        containers_by_server = await self.collector.get_all_containers()
        open_ids = await self.storage.open_observation_ids("Docker")
        for host, containers in containers_by_server.items():
            node = find_node(self.settings, host)
            name = node.label if node else host
            for container in containers:
                key = f"{host.casefold()}:{container.name.casefold()}"
                subject = f"{name} · контейнер {container.name}"
                state = container.state.casefold()
                if state == "running":
                    self._running_containers.add(key)
                    await self.observe(Observation("Docker", key, subject, "UP", node=node))
                elif state in {"exited", "dead", "restarting"} and (
                    key in self._running_containers or key in open_ids
                ):
                    detail = {"exited": "Контейнер остановился.", "dead": "Контейнер завершился с ошибкой.",
                              "restarting": "Docker перезапускает контейнер."}[state]
                    if exit_code := re.search(r"\bExited \((-?\d+)\)", container.status):
                        detail += f" Код выхода {exit_code[1]}."
                    await self.observe(Observation("Docker", key, subject, "DOWN", detail, node=node))

    async def _check_beszel_uptime(self, server: ServerMetrics, host: str) -> None:
        if not server.beszel_hub or server.uptime_seconds <= 0:
            return
        try:
            sample = parse_event_time(server.uptime_sampled_at)
            if sample is None:
                return
            lag = datetime.now(timezone.utc) - sample
            if not timedelta(seconds=-5) <= lag <= timedelta(minutes=3):
                return
            boot = sample - timedelta(seconds=server.uptime_seconds)
        except (ValueError, OverflowError, OSError):
            return
        node = find_node(self.settings, server.hostname, server.name)
        name = f"{node.label if node else server.name} · признаки перезагрузки"
        detail = "Новая загрузка по uptime Beszel. Время приблизительное."
        await self.storage.apply_beszel_uptime(
            host, f"{server.beszel_hub}#{server.entity_id}", sample, boot, name, detail,
            lambda event_id: self._render_event("Beszel", name, detail, boot, event_id, node),
        )

    async def _check_kuma(self) -> None:
        overview = await self.collector.get_uptime_kuma()
        await self.source_health(
            "Kuma", "source", "Uptime Kuma", overview.available, overview.error_message or "",
        )
        webhook_enabled = bool(self.settings.UPTIME_KUMA_WEBHOOK_SECRET)
        open_ids = await self.storage.open_observation_ids("Kuma") if webhook_enabled else set()
        disagreements = {}
        for monitor in overview.monitors:
            if monitor.status not in {"UP", "DOWN"}:
                continue
            if webhook_enabled:
                # Older exporters lack monitor IDs and cannot reconcile webhook events.
                if not monitor.id.isdecimal() or ((monitor.id in open_ids) == (monitor.status == "DOWN")):
                    continue
                previous_status, count = self._kuma_disagreements.get(monitor.id, ("", 0))
                count = count + 1 if previous_status == monitor.status else 1
                if count < max(2, self.settings.MONITOR_SOURCE_FAILURES):
                    disagreements[monitor.id] = (monitor.status, count)
                    continue
            await self.observe(self._kuma_observation(monitor.id, monitor.name, monitor.status, monitor.msg, monitor.url))
        self._kuma_disagreements = disagreements

    def _kuma_observation(self, entity: str, name: str, state, detail: str | None, url: str | None, at=None) -> Observation:
        known_name = find_node(self.settings, name)
        node = known_name or find_node(self.settings, url_host(url))
        title = f"{node.label} · {name}" if node else name
        if known_name:
            title = f"{known_name.label} · проверка Uptime Kuma"
        link = url if url and url.startswith(("https://", "http://")) and len(url) <= 400 else ""
        return Observation("Kuma", entity, title, state, detail or "", at, link=link, node=node)

    async def _loop(self, check, interval: float) -> None:
        while True:
            try:
                await check()
            except Exception:
                logger.exception("Background check failed")
            await asyncio.sleep(interval)

    def start_checks(self) -> list[asyncio.Task]:
        tasks = []
        interval = self.settings.MONITOR_INTERVAL_SEC
        if self.settings.BESZEL_ENABLED:
            tasks.append(asyncio.create_task(self._loop(self._check_beszel, interval)))
        if self.settings.UPTIME_KUMA_ENABLED and not self._kuma_on_backup():
            tasks.append(asyncio.create_task(self._loop(self._check_kuma, interval)))
        if self.settings.DOCKER_MONITOR_ENABLED or self.settings.HA_PEER_ENABLED:
            tasks.append(asyncio.create_task(self._loop(self._check_containers, interval)))
        return tasks

    def _kuma_on_backup(self) -> bool:
        return self.settings.HA_AUTO_FAILOVER and bool(self.settings.UPTIME_KUMA_WEBHOOK_SECRET)

    def start_webhook_checks(self) -> list[asyncio.Task]:
        if self.settings.is_standby and self.settings.UPTIME_KUMA_ENABLED and self._kuma_on_backup():
            return [asyncio.create_task(self._loop(self._check_kuma, self.settings.MONITOR_INTERVAL_SEC))]
        return []

    async def accept_kuma(self, document: dict) -> None:
        monitor, heartbeat = document.get("monitor"), document.get("heartbeat")
        if monitor is None and heartbeat is None:
            return  # Test notification from the Kuma UI.
        if not isinstance(monitor, dict) or not isinstance(heartbeat, dict):
            raise ValueError("Invalid Kuma notification")
        monitor_id = monitor.get("id")
        if not isinstance(monitor_id, int) or monitor_id <= 0:
            raise ValueError("Kuma event requires a monitor ID")
        code = heartbeat.get("status")
        if code not in (0, 1):
            return
        await self.observe(self._kuma_observation(
            str(monitor_id), str(monitor.get("name") or f"Монитор {monitor_id}")[:160],
            "UP" if code == 1 else "DOWN", str(heartbeat.get("msg") or "")[:500],
            str(monitor.get("url") or ""),
            parse_event_time(heartbeat.get("time")),
        ))

    async def accept_beszel(self, document: dict) -> None:
        title, message = document.get("title"), document.get("message")
        hub = document.get("hub", "default")
        if any(not isinstance(value, str) or not value.strip() for value in (title, message, hub)):
            raise ValueError("Beszel notification requires a title and message")
        title, message, hub = title.strip(), message.strip(), hub.strip()[:80].casefold()
        if status := HOST_STATUS.fullmatch(title):
            if not self.settings.BESZEL_ENABLED:
                host = status["host"]
                node = find_node(self.settings, host)
                name = node.label if node else host[:140]
                online = status["state"].casefold() == "up"
                detail = "Хаб снова получает данные от агента." if online else "Хаб потерял связь с агентом."
                await self.observe(Observation(
                    "Beszel", f"webhook-host:{hub}:{host.casefold()}", f"{name} · агент Beszel",
                    "UP" if online else "DOWN", detail, node=node,
                ))
            return  # Host status comes from a single source.
        event = parse_threshold(title, message, hub)
        if event:
            host, _, metric = event.name.rpartition(" · ")
            if node := find_node(self.settings, host):
                event = replace(event, name=f"{node.label} · {metric}", node=node)
            await self.observe(event)
        else:
            if title.casefold() == "test alert":
                title, message = "Проверка уведомлений Beszel", "Бот получил тестовое сообщение от хаба."
            title, message = title[:160], message[:500]
            now = datetime.now(timezone.utc)
            await self.storage.record_event(
                "Beszel", title, message, now,
                lambda event_id: self._render_event("Beszel", title, message, now, event_id),
            )

    async def accept_zabbix(self, document: dict) -> None:
        event_id = str(document.get("event_id", ""))
        status = document.get("status")
        if not event_id.isdecimal() or status not in {"PROBLEM", "RECOVERY"}:
            raise ValueError("Zabbix event requires an ID and a valid status")
        name = str(document.get("name") or f"Событие {event_id}")[:160]
        node = find_node(self.settings, str(document.get("host") or ""), name)
        if node:
            name = node.label if find_node(self.settings, name) else f"{node.label} · {name}"
        await self.observe(Observation(
            "Zabbix", event_id, name,
            "DOWN" if status == "PROBLEM" else "UP", str(document.get("detail") or "")[:500],
            parse_event_time(document.get("timestamp")), severity=str(document.get("severity") or "WARNING")[:32], node=node,
        ))

    async def deliver(self, send_message) -> None:
        while True:
            try:
                item = await self.storage.next_alert()
                if item is None:
                    await asyncio.sleep(0.5)
                    continue
                try:
                    await send_message(item["text"])
                except TelegramRetryAfter as exc:
                    await self.storage.update_alert_delivery(item["id"], retry_delay=exc.retry_after)
                    await asyncio.sleep(exc.retry_after)
                except (TelegramBadRequest, TelegramForbiddenError, TelegramUnauthorizedError) as exc:
                    logger.error("Alert %s rejected by Telegram: %s", item["id"], type(exc).__name__)
                    await self.storage.update_alert_delivery(item["id"], permanent_failure=True)
                except Exception as exc:
                    logger.warning("Alert %s pending: %s", item["id"], type(exc).__name__)
                    delay = min(300, 2 ** min(item["attempts"] + 1, 9))
                    await self.storage.update_alert_delivery(item["id"], retry_delay=delay)
                else:
                    await self.storage.update_alert_delivery(item["id"])
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Alert queue unavailable")
                await asyncio.sleep(2)
