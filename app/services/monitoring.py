"""Schedule observations, normalize incoming events and deliver queued alerts."""

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from aiogram.exceptions import TelegramBadRequest, TelegramForbiddenError, TelegramRetryAfter, TelegramUnauthorizedError

from app.core.config import Settings
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


class MonitoringService:
    def __init__(self, settings: Settings, storage: Storage, collector: MetricsCollector):
        self.settings = settings
        self.storage = storage
        self.collector = collector
        self.tz = ZoneInfo(settings.ALERT_TIMEZONE)
        self._failures: dict[tuple[str, str], int] = {}
        self._kuma_disagreements: dict[str, tuple[str, int]] = {}

    def _stamp(self, at: datetime) -> str:
        return at.astimezone(self.tz).strftime("%d.%m.%Y %H:%M:%S %Z")

    def _render(
        self, event: Observation, at: datetime, incident_id: int,
        started: datetime | None = None, initial_detail: str = "",
    ) -> str:
        source = f"{event.source} · {self.settings.NODE_NAME} #{incident_id}"
        if started is None:
            label = "Время события" if event.occurred_at else "Обнаружено"
            text = f"🔴 {event.name}\n{source}\n{label}: {self._stamp(at)}"
            if event.detail:
                text += f"\n{event.detail[:500]}"
        else:
            seconds = max(0, int((at - started).total_seconds()))
            hours, remainder = divmod(seconds, 3600)
            minutes, seconds = divmod(remainder, 60)
            text = (
                f"🟢 {event.name} — восстановлено\n{source}\n"
                f"Первый сигнал: {self._stamp(started)}\nВосстановление: {self._stamp(at)}\n"
                f"{event.duration_label}: {hours} ч {minutes} мин {seconds} с"
            )
            if initial_detail:
                text += f"\nПри первом сигнале: {initial_detail[:500]}"
            if event.detail:
                text += f"\nПосле восстановления: {event.detail[:500]}"
        if event.link:
            text += f"\n{event.link}"
        return text

    def _render_event(self, source: str, name: str, detail: str, at: datetime, event_id: int) -> str:
        return f"ℹ️ {name}\n{source} · {self.settings.NODE_NAME} #{event_id}\n{self._stamp(at)}\n{detail}"

    async def observe(self, observation: Observation) -> None:
        await self.storage.apply_observation(observation, self._render)

    async def source_health(self, source: str, entity: str, name: str, available: bool, detail: str = "") -> None:
        key = (source, entity)
        if available:
            self._failures.pop(key, None)
            await self.observe(Observation(source, entity, name, "UP"))
            return
        count = self._failures.get(key, 0) + 1
        self._failures[key] = min(count, self.settings.MONITOR_SOURCE_FAILURES)
        if count >= self.settings.MONITOR_SOURCE_FAILURES:
            await self.observe(Observation(source, entity, name, "DOWN", detail))

    async def _check_beszel(self) -> None:
        servers = await self.collector.get_servers(force_refresh=True)
        beszel = self.collector.beszel
        for role, url in (("Main", beszel.main_url), ("Backup", beszel.backup_url)):
            if url and (role == "Main" or url != beszel.main_url):
                await self.source_health(
                    "Beszel", f"hub:{role}", f"Beszel Hub {role}",
                    beszel.hub_available.get(url, False), "Бот не получил данные от API хаба.",
                )
        for server in servers:
            if server.monitor_state in {"UP", "DOWN"}:
                key = (server.hostname or server.name).casefold()
                detail = (
                    "Хаб потерял связь с агентом на сервере." if server.monitor_state == "DOWN"
                    else "Хаб снова получает данные от агента на сервере."
                )
                await self.observe(Observation("Beszel", f"host:{key}", server.name, server.monitor_state, detail))
                if server.monitor_state == "UP":
                    await self._check_beszel_uptime(server, key)

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
        name = f"{server.name} · признаки перезагрузки"
        detail = "По времени работы системы Beszel определил новую загрузку. Время загрузки приблизительное."
        await self.storage.apply_beszel_uptime(
            host, f"{server.beszel_hub}#{server.entity_id}", sample, boot, name, detail,
            lambda event_id: self._render_event("Beszel", name, detail, boot, event_id),
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
            detail = monitor.msg or monitor.url or ""
            await self.observe(Observation("Kuma", monitor.id, monitor.name, monitor.status, detail))
        self._kuma_disagreements = disagreements

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
        if self.settings.UPTIME_KUMA_ENABLED:
            tasks.append(asyncio.create_task(self._loop(self._check_kuma, interval)))
        return tasks

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
        await self.observe(Observation(
            "Kuma", str(monitor_id), str(monitor.get("name") or f"Monitor {monitor_id}")[:160],
            "UP" if code == 1 else "DOWN", str(heartbeat.get("msg") or "")[:500],
            parse_event_time(heartbeat.get("time")),
        ))

    async def accept_beszel(self, document: dict) -> None:
        title, message = document.get("title"), document.get("message")
        hub = document.get("hub", "default")
        if any(not isinstance(value, str) or not value.strip() for value in (title, message, hub)):
            raise ValueError("Beszel notification requires a title and message")
        title, message, hub = title.strip(), message.strip(), hub.strip()[:80].casefold()
        if HOST_STATUS.fullmatch(title) and self.settings.BESZEL_ENABLED:
            return  # The existing polling check reports host reachability.
        event = parse_threshold(title, message, hub)
        if event:
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
        await self.observe(Observation(
            "Zabbix", event_id, str(document.get("name") or f"Событие {event_id}")[:160],
            "DOWN" if status == "PROBLEM" else "UP", str(document.get("detail") or "")[:500],
            parse_event_time(document.get("timestamp")), severity=str(document.get("severity") or "WARNING")[:32],
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
