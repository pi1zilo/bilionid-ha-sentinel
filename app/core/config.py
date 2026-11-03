"""
Configuration management for BilioniD HA Bot.
Loads settings from environment variables or .env file with validation and secrets protection.
"""

from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Union
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NodeRole(str, Enum):
    PRIMARY = "PRIMARY"
    STANDBY = "STANDBY"


class NodeState(str, Enum):
    LEADER_ACTIVE = "LEADER_ACTIVE"       # Active polling instance
    FOLLOWER_PASSIVE = "FOLLOWER_PASSIVE" # Standby instance, polling stopped
    FAILOVER_TAKEOVER = "FAILOVER_TAKEOVER" # Promoted standby during primary failure
    RECOVERING = "RECOVERING"             # Handing over back to primary
    DEGRADED = "DEGRADED"                 # Ambiguous or error state
    MANUAL_OVERRIDE = "MANUAL_OVERRIDE"   # Manually forced state


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=True,
    )

    # Node Identification & Role
    NODE_ROLE: NodeRole = Field(
        default=NodeRole.PRIMARY,
        description="Role of this node: PRIMARY (Main) or STANDBY (Backup)"
    )
    NODE_NAME: str = Field(
        default="Primary-Node",
        description="Human readable name of this node"
    )
    TAILSCALE_IP: str = Field(
        default="100.64.0.1",
        description="Tailscale IP of this node"
    )
    PEER_NODE_NAME: str = Field(
        default="Backup-Node",
        description="Human readable name of the peer node"
    )
    PEER_TAILSCALE_IP: str = Field(
        default="100.64.0.2",
        description="Tailscale IP of the other node"
    )

    # High Availability & Inter-node Communication
    HA_PORT: int = Field(
        default=29010,
        description="Port for internal HA communication over Tailscale"
    )
    HA_HOST: str = Field(
        default="0.0.0.0",
        description="Bind host for HA HTTP server"
    )
    HA_SECRET_KEY: str = Field(
        default="default-secret-change-in-production-32chars",
        description="Pre-shared authentication key for inter-node communication"
    )
    HA_HEARTBEAT_INTERVAL_SEC: float = Field(
        default=5.0,
        description="Interval between peer heartbeat / health checks in seconds"
    )
    HA_FAILOVER_TIMEOUT_SEC: float = Field(
        default=30.0,
        description="Time without healthy heartbeat before considering failover (seconds)"
    )
    HA_RECOVERY_TIMEOUT_SEC: float = Field(
        default=30.0,
        description="Time Primary must be continuously healthy before yielding back (seconds)"
    )
    HA_LEASE_DURATION_SEC: float = Field(
        default=15.0,
        description="Duration of active polling lease before expiration (seconds)"
    )

    # Telegram Bot Settings
    TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API Token"
    )
    TELEGRAM_CHAT_ID: int = Field(
        default=0,
        description="Telegram Chat ID for operational alerts"
    )
    TELEGRAM_ADMIN_USER_ID: Union[int, str] = Field(
        default=0,
        description="Telegram User ID(s) of authorized admins (single ID, comma-separated, or '*' for all)"
    )
    DISABLE_AUTH: bool = Field(
        default=False,
        description="Disable authorization check completely to allow everyone in all chats"
    )
    RATE_LIMIT_COMMANDS_PER_MIN: int = Field(
        default=30,
        description="Max commands per minute per user"
    )

    # Beszel Hub Monitoring
    BESZEL_MAIN_URL: str = Field(
        default="http://100.64.0.1:29001",
        description="URL of Main Beszel Hub"
    )
    BESZEL_BACKUP_URL: str = Field(
        default="http://100.64.0.2:29000",
        description="URL of Backup Beszel Hub"
    )
    BESZEL_USERNAME: Optional[str] = Field(
        default=None,
        description="Beszel PocketBase username/email if auth is required"
    )
    BESZEL_PASSWORD: Optional[str] = Field(
        default=None,
        description="Beszel PocketBase password if auth is required"
    )
    BESZEL_AUTH_TOKEN: Optional[str] = Field(
        default=None,
        description="Static Beszel / PocketBase auth token if provided"
    )

    # Uptime Kuma Monitoring (Optional)
    UPTIME_KUMA_ENABLED: bool = Field(
        default=True,
        description="Enable Uptime Kuma integration"
    )
    UPTIME_KUMA_URL: str = Field(
        default="http://100.64.0.1:29000",
        description="URL of Uptime Kuma on Main"
    )
    UPTIME_KUMA_API_KEY: Optional[str] = Field(
        default=None,
        description="API key or token for Uptime Kuma if configured"
    )
    UPTIME_KUMA_USERNAME: Optional[str] = Field(
        default=None,
        description="Username for Uptime Kuma if basic auth / socket auth is used"
    )
    UPTIME_KUMA_PASSWORD: Optional[str] = Field(
        default=None,
        description="Password for Uptime Kuma"
    )

    # Docker Monitoring
    DOCKER_SOCKET_PATH: str = Field(
        default="/var/run/docker.sock",
        description="Path to Docker socket for local container inspection"
    )
    DOCKER_MONITOR_ENABLED: bool = Field(
        default=True,
        description="Enable local Docker container status monitoring"
    )

    # Storage & Logging
    DATA_DIR: Path = Field(
        default=Path("./data"),
        description="Directory for local database and persistent state"
    )
    LOG_LEVEL: str = Field(
        default="INFO",
        description="Logging level: DEBUG, INFO, WARNING, ERROR, CRITICAL"
    )
    LOG_FORMAT_JSON: bool = Field(
        default=True,
        description="Log in structured JSON format"
    )

    @field_validator("TELEGRAM_BOT_TOKEN")
    @classmethod
    def validate_bot_token(cls, v: str) -> str:
        v = v.strip()
        return v

    @field_validator("TELEGRAM_ADMIN_USER_ID", mode="before")
    @classmethod
    def validate_admin_user_id(cls, v: Any) -> str:
        if v is None:
            return "0"
        return str(v).strip()

    @field_validator("TELEGRAM_CHAT_ID", mode="before")
    @classmethod
    def validate_chat_id(cls, v: Any) -> int:
        if v is None or str(v).strip() == "":
            return 0
        try:
            return int(str(v).strip())
        except Exception:
            return 0

    @property
    def is_primary(self) -> bool:
        return self.NODE_ROLE == NodeRole.PRIMARY

    @property
    def is_standby(self) -> bool:
        return self.NODE_ROLE == NodeRole.STANDBY

    @property
    def allowed_admin_ids(self) -> List[int]:
        val = str(self.TELEGRAM_ADMIN_USER_ID).strip()
        if val in ("*", "all", "0", "") or self.DISABLE_AUTH:
            return []
        import re
        ids: List[int] = []
        for part in re.split(r"[,;|\s]+", val):
            part = part.strip()
            if part.isdigit() and int(part) != 0:
                ids.append(int(part))
        return ids

    @property
    def primary_admin_id(self) -> int:
        ids = self.allowed_admin_ids
        return ids[0] if ids else 0

    @property
    def peer_ha_url(self) -> str:
        return f"http://{self.PEER_TAILSCALE_IP}:{self.HA_PORT}"

    @property
    def local_ha_url(self) -> str:
        return f"http://{self.TAILSCALE_IP}:{self.HA_PORT}"

    @property
    def db_path(self) -> Path:
        return self.DATA_DIR / "state.db"

    def masked_dict(self) -> dict:
        """Returns safe dictionary with secrets masked for diagnostics and logging."""
        d = self.model_dump()
        sensitive_keys = {
            "TELEGRAM_BOT_TOKEN",
            "HA_SECRET_KEY",
            "BESZEL_PASSWORD",
            "BESZEL_AUTH_TOKEN",
            "UPTIME_KUMA_PASSWORD",
            "UPTIME_KUMA_API_KEY",
        }
        for k in sensitive_keys:
            if d.get(k):
                val = str(d[k])
                if len(val) > 8:
                    d[k] = val[:3] + "..." + val[-3:]
                else:
                    d[k] = "***"
        return d


# Global settings instance placeholder (instantiated in main / tests)
_settings: Optional[Settings] = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings


def reset_settings(new_settings: Optional[Settings] = None) -> None:
    global _settings
    _settings = new_settings
