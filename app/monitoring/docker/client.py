"""
Docker container monitoring via local read-only docker socket
and HA peer federation.
"""

import asyncio
from pathlib import Path
import logging
from typing import List, Optional
import docker

from app.monitoring.models import ContainerInfo

logger = logging.getLogger("bilionid_ha.docker")


class DockerMonitor:
    def __init__(
        self,
        server_name: str = "Local",
        socket_path: str = "/var/run/docker.sock",
        enabled: bool = True,
        proxy_url: str = "",
    ):
        self.server_name = server_name
        self.socket_path = socket_path
        self.enabled = enabled
        self.proxy_url = proxy_url
        self._client: Optional[docker.DockerClient] = None

    def _get_client(self) -> Optional[docker.DockerClient]:
        if not self.enabled:
            return None
        if self._client is not None:
            return self._client
        try:
            if self.proxy_url:
                self._client = docker.DockerClient(base_url=self.proxy_url, timeout=4)
                return self._client
            if Path(self.socket_path).exists():
                self._client = docker.DockerClient(base_url=f"unix://{self.socket_path}", timeout=4)
                return self._client
        except Exception as e:
            logger.debug("Docker client initialization skipped: %s", e)
        return None

    async def get_containers(self) -> List[ContainerInfo]:
        """Returns list of Docker containers on the local machine."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, self._list_containers_sync)

    def _list_containers_sync(self) -> List[ContainerInfo]:
        client = self._get_client()
        if client is None:
            return []

        containers_list: List[ContainerInfo] = []
        try:
            for c in client.containers.list(all=True):
                # Health status if available
                health = None
                attrs = c.attrs or {}
                state_obj = attrs.get("State", {})
                if "Health" in state_obj:
                    health = state_obj["Health"].get("Status")

                containers_list.append(
                    ContainerInfo(
                        server=self.server_name,
                        id=c.short_id,
                        name=c.name.lstrip("/"),
                        image=c.image.tags[0] if c.image.tags else str(c.image.id[:12]),
                        state=c.status,
                        status=c.attrs.get("Status", c.status),
                        health=health,
                    )
                )
        except Exception as e:
            logger.warning("Error querying Docker containers: %s", e)

        return containers_list
