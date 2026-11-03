"""
HA state definitions, transitions, and peer health structures.
"""

from datetime import datetime, timezone
from enum import Enum
from typing import Optional
from pydantic import BaseModel, Field

from app.core.config import NodeRole, NodeState


class HealthStatus(str, Enum):
    HEALTHY = "HEALTHY"
    DEGRADED = "DEGRADED"
    UNHEALTHY = "UNHEALTHY"
    UNKNOWN = "UNKNOWN"


class NodeHAInfo(BaseModel):
    node_name: str
    role: NodeRole
    state: NodeState
    is_polling: bool = False
    uptime_seconds: int = 0
    lease_holder: Optional[str] = None
    lease_expires_at: Optional[str] = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class PeerHealth(BaseModel):
    peer_name: str
    peer_ip: str
    is_reachable: bool = False
    ha_endpoint_ok: bool = False
    beszel_hub_ok: bool = False
    consecutive_failures: int = 0
    consecutive_successes: int = 0
    last_seen_healthy: Optional[datetime] = None
    last_probe_time: Optional[datetime] = None
    last_error: Optional[str] = None

    @property
    def seconds_since_healthy(self) -> float:
        if not self.last_seen_healthy:
            return 999999.0
        return (datetime.now(timezone.utc) - self.last_seen_healthy).total_seconds()

    @property
    def seconds_of_continuous_health(self) -> float:
        if not self.is_reachable or not self.last_seen_healthy:
            return 0.0
        # If consecutive successes > 0 and healthy
        return float(self.consecutive_successes * 5.0)
