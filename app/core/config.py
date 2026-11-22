"""
Configuration management for BilioniD HA Bot.
Loads settings from environment variables or .env file with validation and secrets protection.
"""

from enum import Enum
from pathlib import Path
from typing import Optional
from zoneinfo import ZoneInfo
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class NodeRole(str, Enum):
    PRIMARY = "PRIMARY"
    STANDBY = "STANDBY"


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
        default="BilioniD-factories",
        description="Human readable name of this node"
    )
    TAILSCALE_IP: str = Field(
        default="100.68.106.100",
        description="Tailscale IP of this node"
    )
    PEER_NODE_NAME: str = Field(
        default="SUSLIKVPN",
        description="Human readable name of the peer node"
    )
    PEER_TAILSCALE_IP: str = Field(
        default="100.77.242.10",
        description="Tailscale IP of the other node"
    )

    # High Availability & Inter-node Communication
    HA_PORT: int = Field(
        default=29010,
        ge=1, le=65535,
        description="Port for internal HA communication over Tailscale"
    )
    HA_HOST: str = Field(
        default="0.0.0.0",
        description="Bind host for HA HTTP server"
    )
    HA_SECRET_KEY: str = Field(
        default="",
        description="Pre-shared authentication key for inter-node communication"
    )
    HA_PEER_ENABLED: bool = False
    HA_PEER_PORT: int | None = Field(default=None, ge=1, le=65535)
    HA_WATCH_PRIMARY: bool = False
    HA_HEARTBEAT_INTERVAL_SEC: float = Field(
        default=5.0,
        gt=0,
        description="Interval between peer heartbeat / health checks in seconds"
    )
    # Telegram Bot Settings
    TELEGRAM_BOT_TOKEN: str = Field(
        default="",
        description="Telegram Bot API Token"
    )
    TELEGRAM_CHAT_ID: int = Field(
        default=0,
        le=0,
        description="Telegram group ID for operational alerts"
    )
    TELEGRAM_COMMAND_CHAT_ID: int = Field(default=0, le=0)
    TELEGRAM_POLLING_ENABLED: bool | None = None
    TELEGRAM_API_BASE: str = ""
    RATE_LIMIT_COMMANDS_PER_MIN: int = Field(
        default=30,
        description="Max commands per minute per user"
    )

    # Beszel Hub Monitoring
    BESZEL_MAIN_URL: str = Field(
        default="http://100.68.106.100:29001",
        description="URL of Main Beszel Hub"
    )
    BESZEL_ENABLED: bool = True
    BESZEL_BACKUP_URL: str = Field(
        default="http://100.77.242.10:29000",
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
    BESZEL_BACKUP_USERNAME: str = ""
    BESZEL_BACKUP_PASSWORD: str = ""
    BESZEL_BACKUP_AUTH_TOKEN: str = ""
    BESZEL_WEBHOOK_SECRET: str = ""

    # Uptime Kuma Monitoring (Optional)
    UPTIME_KUMA_ENABLED: bool = Field(
        default=True,
        description="Enable Uptime Kuma integration"
    )
    UPTIME_KUMA_URL: str = Field(
        default="http://100.68.106.100:29000",
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
    UPTIME_KUMA_STATUS_PAGE_SLUG: str = ""
    UPTIME_KUMA_WEBHOOK_SECRET: str = ""

    ZABBIX_URL: str = ""
    ZABBIX_API_TOKEN: str = ""
    ZABBIX_WEBHOOK_SECRET: str = ""

    MONITOR_INTERVAL_SEC: float = Field(default=15, gt=0)
    MONITOR_SOURCE_FAILURES: int = Field(default=2, ge=1)
    ALERT_TIMEZONE: str = "Europe/Moscow"
    SERVER_LOGS_ENABLED: bool = False

    # Docker Monitoring
    DOCKER_SOCKET_PATH: str = Field(
        default="/var/run/docker.sock",
        description="Path to Docker socket for local container inspection"
    )
    DOCKER_MONITOR_ENABLED: bool = Field(
        default=False,
        description="Enable local Docker container status monitoring"
    )
    DOCKER_PROXY_URL: str = ""

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

    @property
    def is_primary(self) -> bool:
        return self.NODE_ROLE == NodeRole.PRIMARY

    @property
    def is_standby(self) -> bool:
        return self.NODE_ROLE == NodeRole.STANDBY

    @property
    def polling_enabled(self) -> bool:
        return self.is_primary if self.TELEGRAM_POLLING_ENABLED is None else self.TELEGRAM_POLLING_ENABLED

    @property
    def command_chat_id(self) -> int:
        return self.TELEGRAM_COMMAND_CHAT_ID or self.TELEGRAM_CHAT_ID

    @property
    def peer_ha_url(self) -> str:
        return f"http://{self.PEER_TAILSCALE_IP}:{self.HA_PEER_PORT or self.HA_PORT}"

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
            "BESZEL_BACKUP_PASSWORD",
            "BESZEL_BACKUP_AUTH_TOKEN",
            "BESZEL_WEBHOOK_SECRET",
            "UPTIME_KUMA_WEBHOOK_SECRET",
            "ZABBIX_API_TOKEN",
            "ZABBIX_WEBHOOK_SECRET",
        }
        for k in sensitive_keys:
            if d.get(k):
                val = str(d[k])
                if len(val) > 8:
                    d[k] = val[:3] + "..." + val[-3:]
                else:
                    d[k] = "***"
        return d

    def validate_runtime(self) -> None:
        """Validate requirements for the features enabled on this node."""
        if self.HA_PEER_ENABLED and (
            len(self.HA_SECRET_KEY) < 32
            or self.HA_SECRET_KEY.startswith(("default-secret", "change-this"))
        ):
            raise ValueError("HA_SECRET_KEY must be a unique secret of at least 32 characters")
        if self.HA_WATCH_PRIMARY and (not self.HA_PEER_ENABLED or not self.is_standby):
            raise ValueError("HA_WATCH_PRIMARY requires a STANDBY with HA_PEER_ENABLED")
        if self.polling_enabled or self.HA_WATCH_PRIMARY:
            if not self.TELEGRAM_BOT_TOKEN or not self.TELEGRAM_CHAT_ID:
                raise ValueError("TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID are required")
        for secret in (self.BESZEL_WEBHOOK_SECRET, self.UPTIME_KUMA_WEBHOOK_SECRET, self.ZABBIX_WEBHOOK_SECRET):
            if secret and len(secret) < 32:
                raise ValueError("Webhook secrets must be at least 32 characters")
        if bool(self.ZABBIX_URL) != bool(self.ZABBIX_API_TOKEN):
            raise ValueError("ZABBIX_URL and ZABBIX_API_TOKEN must be set together")
        ZoneInfo(self.ALERT_TIMEZONE)


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
