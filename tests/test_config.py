"""
Unit tests for configuration loading, validation, and secret masking.
"""

import pytest
from app.core.config import NodeRole, Settings


def test_default_settings():
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11",
        TELEGRAM_CHAT_ID=-100123456789,
    )
    assert settings.NODE_ROLE == NodeRole.PRIMARY
    assert settings.is_primary is True
    assert settings.is_standby is False
    assert settings.HA_PORT == 29010
    assert settings.peer_ha_url == "http://100.77.242.10:29010"


def test_chat_only_settings(monkeypatch):
    monkeypatch.setenv("TELEGRAM_ADMIN_USER_ID", "123")
    monkeypatch.setenv("TELEGRAM_LOGS_USER_ID", "123")
    settings = Settings(
        TELEGRAM_BOT_TOKEN="123456:example",
        TELEGRAM_CHAT_ID=-100123456789,
        TELEGRAM_COMMAND_CHAT_ID=0,
    )
    settings.validate_runtime()
    assert settings.command_chat_id == settings.TELEGRAM_CHAT_ID


@pytest.mark.parametrize("name", ["TELEGRAM_CHAT_ID", "TELEGRAM_COMMAND_CHAT_ID"])
def test_telegram_chat_settings_reject_private_ids(name):
    with pytest.raises(ValueError, match=name):
        Settings(**{name: 123})


def test_standby_settings():
    settings = Settings(
        NODE_ROLE=NodeRole.STANDBY,
        NODE_NAME="SUSLIKVPN",
        TAILSCALE_IP="100.77.242.10",
        PEER_NODE_NAME="BilioniD-factories",
        PEER_TAILSCALE_IP="100.68.106.100",
        HA_PORT=29010,
    )
    assert settings.NODE_ROLE == NodeRole.STANDBY
    assert settings.is_primary is False
    assert settings.is_standby is True
    assert settings.peer_ha_url == "http://100.68.106.100:29010"


def test_secret_masking():
    token = "1234567890:ABCdefGHIjklMNOpqrSTUvwxYZ_example"
    secret = "my-super-secret-key-1234567890abcdef"
    settings = Settings(
        TELEGRAM_BOT_TOKEN=token,
        HA_SECRET_KEY=secret,
    )
    masked = settings.masked_dict()

    assert token not in str(masked)
    assert secret not in str(masked)
    assert masked["TELEGRAM_BOT_TOKEN"].startswith("123...")
    assert masked["HA_SECRET_KEY"].startswith("my-...")
