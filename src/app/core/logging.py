from __future__ import annotations

import json
import logging
import re
import sys
from typing import Any

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|api[_-]?secret|token|password|secret|access_key)\s*[=:]\s*\S+"),
]

_REDACT = "[REDACTED]"


def _redact(message: str) -> str:
    for pat in _SECRET_PATTERNS:
        message = pat.sub(lambda m: m.group(0).split("=")[0].split(":")[0] + "=" + _REDACT, message)
    return message


class _JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "ts": self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level": record.levelname,
            "logger": record.name,
            "msg": _redact(record.getMessage()),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        for key in ("job_id", "correlation_id", "strategy_id", "symbol"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        return json.dumps(payload)


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    if root.handlers:
        return
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_JsonFormatter())
    root.addHandler(handler)
    root.setLevel(getattr(logging, level.upper(), logging.INFO))


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
