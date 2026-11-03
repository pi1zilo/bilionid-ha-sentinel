"""
Data models for infrastructure monitoring metrics.
"""

from typing import List, Optional
from pydantic import BaseModel, Field


class ServerMetrics(BaseModel):
    name: str = Field(description="Server identifier name")
    hostname: str = Field(default="", description="Server hostname or IP")
    status: str = Field(default="Online", description="Online, Degraded, or Offline")
    is_online: bool = Field(default=True)
    cpu_percent: float = Field(default=0.0)
    ram_used_gb: float = Field(default=0.0)
    ram_total_gb: float = Field(default=0.0)
    ram_percent: float = Field(default=0.0)
    disk_used_gb: float = Field(default=0.0)
    disk_total_gb: float = Field(default=0.0)
    disk_free_gb: float = Field(default=0.0)
    disk_percent: float = Field(default=0.0)
    load_1m: float = Field(default=0.0)
    load_5m: float = Field(default=0.0)
    load_15m: float = Field(default=0.0)
    uptime_str: str = Field(default="Unknown")
    uptime_seconds: int = Field(default=0)
    docker_containers_total: int = Field(default=0)
    docker_containers_running: int = Field(default=0)
    docker_containers_stopped: int = Field(default=0)
    temperature: Optional[float] = Field(default=None)
    network_rx_mb: Optional[float] = Field(default=None)
    network_tx_mb: Optional[float] = Field(default=None)
    last_updated: str = Field(default="")
    source: str = Field(default="beszel", description="beszel, local_fallback, or simulated")
    error_message: Optional[str] = Field(default=None)


class ContainerInfo(BaseModel):
    server: str = Field(description="Server where container is located")
    id: str = Field(default="")
    name: str = Field(description="Container name")
    image: str = Field(default="")
    state: str = Field(default="unknown", description="running, exited, paused, etc.")
    status: str = Field(default="", description="Docker status string, e.g. Up 2 hours")
    health: Optional[str] = Field(default=None, description="healthy, unhealthy, starting")


class UptimeKumaMonitor(BaseModel):
    id: str = Field(default="")
    name: str = Field(description="Monitor name")
    url: Optional[str] = Field(default=None)
    status: str = Field(default="UNKNOWN", description="UP, DOWN, PENDING, PAUSED")
    ping_ms: Optional[float] = Field(default=None)
    uptime_24h: Optional[float] = Field(default=None)
    last_check: Optional[str] = Field(default=None)
    msg: Optional[str] = Field(default=None)


class UptimeKumaOverview(BaseModel):
    available: bool = Field(default=True)
    monitors: List[UptimeKumaMonitor] = Field(default_factory=list)
    total_count: int = Field(default=0)
    up_count: int = Field(default=0)
    down_count: int = Field(default=0)
    error_message: Optional[str] = Field(default=None)
