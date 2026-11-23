"""Structured logs with configured credentials redacted at serialization."""

import base64
import json
import logging
import sys
from datetime import datetime, timezone
from urllib.parse import quote


class RedactingFormatter(logging.Formatter):
    def __init__(self, secrets=(), json_format: bool = False):
        super().__init__("%(asctime)s [%(levelname)s] %(name)s: %(message)s")
        values = {variant for secret in secrets if secret for variant in (
            secret, quote(secret, safe=""), base64.b64encode(f":{secret}".encode()).decode(),
        )}
        self.secrets = sorted(values, key=len, reverse=True)
        self.json_format = json_format

    def redact(self, text: str) -> str:
        for secret in self.secrets:
            text = text.replace(secret, "***")
        return text

    def format(self, record: logging.LogRecord) -> str:
        if not self.json_format:
            return self.redact(super().format(record))
        payload = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": self.redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exception"] = self.redact(self.formatException(record.exc_info))
        return json.dumps(payload, ensure_ascii=False)


def setup_logger(
    name: str = "bilionid_ha", level: str = "INFO", json_format: bool = False, *, secrets=()
) -> logging.Logger:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(RedactingFormatter(secrets, json_format))
    logging.basicConfig(handlers=[handler], level=getattr(logging, level.upper(), logging.INFO), force=True)
    return logging.getLogger(name)
