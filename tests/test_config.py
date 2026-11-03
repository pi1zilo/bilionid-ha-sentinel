"""
Unit tests for configuration loading, validation, and secret masking.
"""

import pytest
from app.core.config import NodeRole, Settings


def test_default_settings():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        TELEGRAM_ADMIN_USER_ID=987654321,
        TELEGRAM_CHAT_ID=-100123456789,
    )
    assert settings.NODE_ROLE == NodeRole.PRIMARY
    assert settings.is_primary is True
    assert settings.is_standby is False
    assert settings.HA_PORT == 29010
    assert settings.peer_ha_url == "http://100.64.0.2:29010"


def test_standby_settings():
    settings = Settings(
        NODE_ROLE=NodeRole.STANDBY,
        NODE_NAME="backup-node",
        TAILSCALE_IP="100.64.0.2",
        PEER_NODE_NAME="primary-node",
        PEER_TAILSCALE_IP="100.64.0.1",
        HA_PORT=29010,
    )
    assert settings.NODE_ROLE == NodeRole.STANDBY
    assert settings.is_primary is False
    assert settings.is_standby is True
    assert settings.peer_ha_url == "http://100.64.0.1:29010"


def test_secret_masking():
    token = "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ_example"
    secret = "my-super-secret-key-1234567890abcdef"
    settings = Settings(
        TELEGRAM_BOT_TOKEN=token,
        HA_SECRET_KEY=secret,
        TELEGRAM_ADMIN_USER_ID=123,
    )
    masked = settings.masked_dict()

    assert token not in str(masked)
    assert secret not in str(masked)
    assert masked["TELEGRAM_BOT_TOKEN"].startswith("123...")
    assert masked["HA_SECRET_KEY"].startswith("my-...")
