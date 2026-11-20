"""Liveness, authenticated peer views and monitoring webhooks."""

import logging
from hmac import compare_digest

from aiohttp import web

from app.core.config import Settings
from app.ha.supervisor import HASupervisor
from app.monitoring.docker.client import DockerMonitor
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
        if settings.polling_enabled and settings.UPTIME_KUMA_WEBHOOK_SECRET:
            self.app.router.add_post("/events/kuma", self.kuma_event)
        if settings.polling_enabled and settings.ZABBIX_WEBHOOK_SECRET:
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

    async def zabbix_event(self, request: web.Request) -> web.Response:
        return await self._event(request, self.settings.ZABBIX_WEBHOOK_SECRET, self.monitoring.accept_zabbix)

    async def start(self) -> None:
        await self.runner.setup()
        await web.TCPSite(self.runner, self.settings.HA_HOST, self.settings.HA_PORT).start()
        logger.info("Health server listening on port %d", self.settings.HA_PORT)

    async def stop(self) -> None:
        await self.runner.cleanup()
