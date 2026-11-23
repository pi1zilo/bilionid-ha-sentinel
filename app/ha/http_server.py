"""Liveness, authenticated peer views and monitoring webhooks."""

import logging
from hmac import compare_digest
from string import hexdigits

from aiohttp import web

from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.docker.client import DockerMonitor
from app.monitoring.server_logs import DEFAULT_LOG_LINES, JournalUnavailable, parse_log_lines, read_server_journal
from app.services.monitoring import MonitoringService

logger = logging.getLogger("bilionid_ha.http_server")


def has_secret(request: web.Request, header: str, expected: str) -> bool:
    supplied = request.headers.get(header, "")
    return bool(expected) and compare_digest(supplied.encode(), expected.encode())


class HAHttpServer:
    def __init__(
        self, settings: Settings, supervisor: HASupervisor, monitoring: MonitoringService,
        docker_monitor: DockerMonitor,
    ):
        self.settings = settings
        self.supervisor = supervisor
        self.monitoring = monitoring
        self.docker_monitor = docker_monitor
        self.app = web.Application(client_max_size=64 * 1024)
        self.app.router.add_get("/health", self.health)
        self.app.router.add_get("/ready", self.ready)
        if settings.HA_PEER_ENABLED:
            self.app.router.add_get("/ha/status", self.peer_status)
            self.app.router.add_get("/ha/containers", self.peer_containers)
            self.app.router.add_get("/ha/incidents", self.peer_incidents)
            self.app.router.add_get("/ha/metrics", self.peer_metrics)
            if settings.HA_AUTO_FAILOVER and settings.is_standby:
                self.app.router.add_post("/ha/grant", self.peer_grant)
                self.app.router.add_post("/ha/preference", self.peer_preference)
            if settings.SERVER_LOGS_ENABLED:
                self.app.router.add_get("/ha/logs", self.peer_logs)
        webhook_receiver = settings.is_standby if settings.HA_AUTO_FAILOVER else settings.polling_enabled
        if webhook_receiver and settings.BESZEL_WEBHOOK_SECRET:
            self.app.router.add_post("/events/beszel", self.beszel_event)
        if webhook_receiver and settings.UPTIME_KUMA_WEBHOOK_SECRET:
            self.app.router.add_post("/events/kuma", self.kuma_event)
        if webhook_receiver and settings.ZABBIX_WEBHOOK_SECRET:
            self.app.router.add_post("/events/zabbix", self.zabbix_event)
        self.runner = web.AppRunner(self.app, access_log=None)

    async def health(self, request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def ready(self, request: web.Request) -> web.Response:
        info = self.supervisor.get_ha_info()
        ok = not info["polling_enabled"] or info["polling_ready"]
        return web.json_response({"status": "ready" if ok else "degraded"}, status=200 if ok else 503)

    def _authorize_peer(self, request: web.Request) -> None:
        if not has_secret(request, "X-HA-Secret", self.settings.HA_SECRET_KEY):
            raise web.HTTPUnauthorized()

    async def peer_status(self, request: web.Request) -> web.Response:
        self._authorize_peer(request)
        info = self.supervisor.get_ha_info()
        seen = info["last_peer_seen"]
        info["last_peer_seen"] = seen.isoformat() if seen else None
        return web.json_response(info)

    async def peer_containers(self, request: web.Request) -> web.Response:
        self._authorize_peer(request)
        containers = await self.docker_monitor.get_containers()
        return web.json_response({"containers": [item.model_dump() for item in containers]})

    async def peer_metrics(self, request: web.Request) -> web.Response:
        self._authorize_peer(request)
        return web.json_response(self.monitoring.collector.local_metrics().model_dump())

    async def peer_incidents(self, request: web.Request) -> web.Response:
        self._authorize_peer(request)
        incidents = await self.monitoring.storage.get_recent_incidents(limit=10)
        return web.json_response({"node": self.settings.NODE_NAME, "incidents": incidents})

    async def _handover_request(self, request: web.Request) -> dict:
        self._authorize_peer(request)
        try:
            document = await request.json()
        except (ValueError, TypeError):
            raise web.HTTPBadRequest() from None
        if not isinstance(document, dict) or document.get("node") != self.settings.PEER_NODE_NAME:
            raise web.HTTPBadRequest()
        if self.supervisor.handover is None:
            raise web.HTTPServiceUnavailable()
        return document

    async def peer_grant(self, request: web.Request) -> web.Response:
        document = await self._handover_request(request)
        session = document.get("session")
        if not isinstance(session, str) or len(session) != 32 or any(c not in hexdigits for c in session):
            raise web.HTTPBadRequest()
        granted = await self.supervisor.handover.grant(session)
        return web.json_response({"node": self.settings.NODE_NAME, "granted": granted})

    async def peer_preference(self, request: web.Request) -> web.Response:
        document = await self._handover_request(request)
        target = document.get("target")
        if target not in ("main", "backup"):
            raise web.HTTPBadRequest()
        await self.supervisor.handover.prefer(target)
        return web.json_response({"node": self.settings.NODE_NAME, "target": target})

    async def peer_logs(self, request: web.Request) -> web.Response:
        self._authorize_peer(request)
        try:
            lines = parse_log_lines(request.query.get("lines", str(DEFAULT_LOG_LINES)))
        except ValueError:
            raise web.HTTPBadRequest(text="Invalid journal line count") from None
        try:
            text = await read_server_journal(lines)
        except JournalUnavailable:
            raise web.HTTPServiceUnavailable(text="System journal unavailable") from None
        return web.Response(text=text, content_type="text/plain", charset="utf-8")

    async def _event(self, request: web.Request, secret: str, accept) -> web.Response:
        if not has_secret(request, "X-Monitor-Secret", secret):
            raise web.HTTPUnauthorized()
        try:
            document = await request.json()
            if not isinstance(document, dict):
                raise ValueError("Expected a JSON object")
            await accept(document)
        except (ValueError, TypeError, OverflowError, OSError):
            raise web.HTTPBadRequest(text="Invalid event") from None
        return web.Response(status=204)

    async def kuma_event(self, request: web.Request) -> web.Response:
        return await self._event(request, self.settings.UPTIME_KUMA_WEBHOOK_SECRET, self.monitoring.accept_kuma)

    async def beszel_event(self, request: web.Request) -> web.Response:
        return await self._event(request, self.settings.BESZEL_WEBHOOK_SECRET, self.monitoring.accept_beszel)

    async def zabbix_event(self, request: web.Request) -> web.Response:
        return await self._event(request, self.settings.ZABBIX_WEBHOOK_SECRET, self.monitoring.accept_zabbix)

    async def start(self) -> None:
        await self.runner.setup()
        await web.TCPSite(self.runner, self.settings.HA_HOST, self.settings.HA_PORT).start()
        logger.info("Health server listening on port %d", self.settings.HA_PORT)

    async def stop(self) -> None:
        await self.runner.cleanup()
