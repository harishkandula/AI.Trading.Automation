"""Telegram notification – non-fatal, stores failures separately."""
from __future__ import annotations

import logging
from typing import Any

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

logger = logging.getLogger(__name__)

_FAILED_NOTIFICATIONS: list[dict[str, Any]] = []


@retry(stop=stop_after_attempt(3), wait=wait_exponential(min=2, max=10))
def _post(bot_token: str, chat_id: str, text: str) -> None:
    url = f"https://api.telegram.org/bot{bot_token}/sendMessage"
    resp = httpx.post(
        url, json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"}, timeout=10
    )
    resp.raise_for_status()


def send(bot_token: str, chat_id: str, message: str) -> bool:
    """Returns True on success. Never raises – stores failure for retry."""
    try:
        _post(bot_token, chat_id, message)
        return True
    except Exception as exc:
        logger.warning(f"Telegram send failed: {type(exc).__name__}: {exc}")
        _FAILED_NOTIFICATIONS.append(
            {"bot_token": "[REDACTED]", "chat_id": chat_id, "message": message}
        )
        return False


def get_failed_notifications() -> list[dict[str, Any]]:
    return list(_FAILED_NOTIFICATIONS)
