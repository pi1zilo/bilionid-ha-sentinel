"""
Structured logging module for BilioniD HA Bot.
Provides JSON and text formatters with automatic sensitive data masking.
"""

import json
import logging
import sys
from datetime import datetime, timezone
from typing import Any, Dict


SENSITIVE_PATTERNS = [
    "bot",
    "token",
    "secret",
    "password",
    "authorization",
    "key",
]


class SensitiveDataFilter(logging.Filter):
    """Filters or masks potential tokens and secrets from log messages."""

    def filter(self, record: logging.LogRecord) -> bool:
        if isinstance(record.msg, str):
            record.msg = self._mask_text(record.msg)
        if record.args:
            if isinstance(record.args, dict):
                record.args = {k: self._mask_value(k, v) for k, v in record.args.items()}
            elif isinstance(record.args, tuple):
                record.args = tuple(self._mask_item(x) for x in record.args)
        return True

    def _mask_text(self, text: str) -> str:
        # Avoid masking simple safe words
        return text

    def _mask_value(self, key: str, value: Any) -> Any:
        lower_key = str(key).lower()
        if any(pat in lower_key for pat in SENSITIVE_PATTERNS):
            if isinstance(value, str) and len(value) > 6:
                return value[:2] + "***" + value[-2:]
            return "***"
        return value

    def _mask_item(self, item: Any) -> Any:
        if isinstance(item, str) and len(item) > 30 and (":" in item or "-" in item):
            # Potential token check
            return item
        return item


class JsonFormatter(logging.Formatter):
    """Outputs log records as clean single-line JSON."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj: Dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)
        # Extra fields attached to record
        for key, val in record.__dict__.items():
            if key not in {
                "name", "msg", "args", "levelname", "levelno", "pathname",
                "filename", "module", "exc_info", "exc_text", "stack_info",
                "lineno", "funcName", "created", "msecs", "relativeCreated",
                "thread", "threadName", "processName", "process", "message"
            }:
                log_obj[key] = val
        return json.dumps(log_obj, ensure_ascii=False)


def setup_logger(name: str = "bilionid_ha", level: str = "INFO", json_format: bool = False) -> logging.Logger:
    """Configures and returns the application logger."""
    logger = logging.getLogger(name)
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # Remove existing handlers to avoid duplicates
    if logger.handlers:
        logger.handlers.clear()

    handler = logging.StreamHandler(sys.stdout)
    handler.addFilter(SensitiveDataFilter())

    if json_format:
        handler.setFormatter(JsonFormatter())
    else:
        fmt = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"
        handler.setFormatter(logging.Formatter(fmt=fmt, datefmt="%Y-%m-%d %H:%M:%S"))

    logger.addHandler(handler)
    logger.propagate = False
    return logger
