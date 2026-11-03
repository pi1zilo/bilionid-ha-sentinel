"""
HA inter-node HTTP server running over Tailscale private network.
"""

import asyncio
from datetime import datetime, timezone
import logging
from typing import Any, Callable, Dict, Optional
from aiohttp import web

from app.core.config import Settings
from app.monitoring.docker.client import DockerMonitor

logger = logging.getLogger("bilionid_ha.http_server")


class HAHttpServer:
    def __init__(
        self,
        settings: Settings,
        get_ha_info: Callable[[], Dict[str, Any]],
        on_yield_request: Optional[Callable[[], Any]] = None,
        on_force_role_request: Optional[Callable[[str], Any]] = None,
        docker_monitor: Optional[DockerMonitor] = None,
        get_local_metrics: Optional[Callable[[], Any]] = None,
    ):
        self.settings = settings
        self.get_ha_info = get_ha_info
        self.on_yield_request = on_yield_request
        self.on_force_role_request = on_force_role_request
        self.docker_monitor = docker_monitor
        self.get_local_metrics = get_local_metrics
        self.app = web.Application()
        self.runner: Optional[web.AppRunner] = None
        self.site: Optional[web.TCPSite] = None
        self._setup_routes()

    def _setup_routes(self) -> None:
        self.app.router.add_get("/health", self.handle_health)
        self.app.router.add_get("/ha/status", self.handle_ha_status)
        self.app.router.add_get("/ha/containers", self.handle_containers)
        self.app.router.add_get("/ha/metrics", self.handle_metrics)
        self.app.router.add_post("/ha/heartbeat", self.handle_heartbeat)
        self.app.router.add_post("/ha/yield", self.handle_yield)
        self.app.router.add_post("/ha/force_role", self.handle_force_role)

    def _verify_secret(self, request: web.Request) -> bool:
        secret = request.headers.get("X-HA-Secret")
        if not secret:
            auth_header = request.headers.get("Authorization", "")
            if auth_header.startswith("Bearer "):
                secret = auth_header[7:]
        return secret == self.settings.HA_SECRET_KEY

    async def handle_health(self, request: web.Request) -> web.Response:
        """Health endpoint for local and peer probes."""
        ha_info = self.get_ha_info()
        return web.json_response(
            {
                "status": "ok",
                "node_name": self.settings.NODE_NAME,
                "role": self.settings.NODE_ROLE.value,
                "state": ha_info.get("state", "UNKNOWN"),
                "is_polling": ha_info.get("is_polling", False),
                "uptime_seconds": ha_info.get("uptime_seconds", 0),
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        )

    async def handle_ha_status(self, request: web.Request) -> web.Response:
        """Detailed HA status endpoint with authentication."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        ha_info = self.get_ha_info()
        return web.json_response(ha_info)

    async def handle_containers(self, request: web.Request) -> web.Response:
        """Returns local Docker containers to peer for cross-node views."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        containers = []
        if self.docker_monitor:
            raw_containers = await self.docker_monitor.get_containers()
            containers = [c.model_dump() for c in raw_containers]

        return web.json_response({"node": self.settings.NODE_NAME, "containers": containers})

    async def handle_metrics(self, request: web.Request) -> web.Response:
        """Returns local host metrics (psutil fallback) to peer for cross-node views."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        metrics = None
        if self.get_local_metrics:
            try:
                res = self.get_local_metrics()
                if asyncio.iscoroutine(res):
                    res = await res
                if hasattr(res, "model_dump"):
                    metrics = res.model_dump()
                elif isinstance(res, dict):
                    metrics = res
            except Exception as e:
                logger.error("Error generating local metrics: %s", e)

        if metrics:
            return web.json_response({"node": self.settings.NODE_NAME, "metrics": metrics})
        return web.json_response({"error": "Metrics unavailable"}, status=503)

    async def handle_heartbeat(self, request: web.Request) -> web.Response:
        """Endpoint to receive incoming heartbeat from peer."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
        except Exception:
            data = {}

        peer = data.get("source_node", "Unknown")
        logger.debug("Received heartbeat from peer %s", peer)
        return web.json_response({"status": "received", "node": self.settings.NODE_NAME})

    async def handle_yield(self, request: web.Request) -> web.Response:
        """Yield polling to Primary node upon recovery."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        if self.on_yield_request:
            res = self.on_yield_request()
            if asyncio.iscoroutine(res):
                await res
            logger.info("Yielded polling upon peer request")
            return web.json_response({"status": "yielded", "node": self.settings.NODE_NAME})

        return web.json_response({"status": "no_action"}, status=200)

    async def handle_force_role(self, request: web.Request) -> web.Response:
        """Force role change by owner/admin."""
        if not self._verify_secret(request):
            return web.json_response({"error": "Unauthorized"}, status=401)

        try:
            data = await request.json()
            new_role = data.get("role")
            if new_role and self.on_force_role_request:
                res = self.on_force_role_request(new_role)
                if asyncio.iscoroutine(res):
                    await res
                return web.json_response({"status": "role_updated", "role": new_role})
        except Exception as e:
            return web.json_response({"error": str(e)}, status=400)

        return web.json_response({"error": "Invalid request"}, status=400)

    async def start(self) -> None:
        """Starts HTTP server on HA_HOST and HA_PORT."""
        self.runner = web.AppRunner(self.app)
        await self.runner.setup()
        self.site = web.TCPSite(self.runner, self.settings.HA_HOST, self.settings.HA_PORT)
        await self.site.start()
        logger.info(
            "HA HTTP Server listening on %s:%d (Tailscale private network)",
            self.settings.HA_HOST,
            self.settings.HA_PORT,
        )

    async def stop(self) -> None:
        """Stops HTTP server."""
        if self.runner:
            await self.runner.cleanup()
            logger.info("HA HTTP Server stopped")
