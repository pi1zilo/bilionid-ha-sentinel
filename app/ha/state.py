"""Last authenticated observation of the peer's HA endpoint."""

from datetime import datetime

from pydantic import BaseModel


class PeerHealth(BaseModel):
    peer_name: str
    peer_ip: str
    is_reachable: bool = False
    last_seen_healthy: datetime | None = None
