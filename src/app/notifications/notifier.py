"""Central notification service — wraps Telegram send + template formatting."""
from __future__ import annotations

import json
import logging
from datetime import date

import boto3  # type: ignore[import-untyped]

from app.models.signal import Signal
from app.notifications import telegram
from app.notifications.templates import (
    format_daily_summary,
    format_error_alert,
    format_signal_alert,
)

log = logging.getLogger(__name__)

_MAX_SIGNALS_PER_MESSAGE = 10


class Notifier:
    """High-level notification service backed by Telegram."""

    def __init__(self, bot_token: str, chat_id: str) -> None:
        self._bot_token = bot_token
        self._chat_id = chat_id

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def send_signal_alert(self, signals: list[Signal]) -> bool:
        """Format and send signal alerts, chunked to max 10 per message."""
        if not signals:
            return telegram.send(
                self._bot_token,
                self._chat_id,
                "<b>Market AI Signal Scan</b>\n\nNo signals generated.",
            )

        ok = True
        chunks = _chunk(signals, _MAX_SIGNALS_PER_MESSAGE)
        for i, chunk in enumerate(chunks):
            scan_date = chunk[0].signal_timestamp.date()
            text = format_signal_alert(chunk, scan_date)
            result = telegram.send(self._bot_token, self._chat_id, text)
            if not result:
                log.warning(
                    "Signal alert chunk %d/%d failed to send", i + 1, len(chunks)
                )
                ok = False
        return ok

    def send_daily_summary(
        self,
        scan_date: date,
        universe_size: int,
        scanned: int,
        signals: list[Signal],
        errors: int = 0,
    ) -> bool:
        """Send daily scan summary message."""
        text = format_daily_summary(scan_date, universe_size, scanned, signals, errors)
        return telegram.send(self._bot_token, self._chat_id, text)

    def send_error_alert(self, context: str, error: str) -> bool:
        """Send an error notification."""
        text = format_error_alert(context, error)
        return telegram.send(self._bot_token, self._chat_id, text)

    # ------------------------------------------------------------------
    # Factory
    # ------------------------------------------------------------------

    @classmethod
    def from_secrets(
        cls,
        secret_id: str = "market-ai-dev/telegram",
        region: str = "ap-south-1",
    ) -> Notifier:
        """Construct a Notifier by reading credentials from AWS Secrets Manager."""
        client = boto3.client("secretsmanager", region_name=region)
        resp = client.get_secret_value(SecretId=secret_id)
        secret = json.loads(resp["SecretString"])
        return cls(
            bot_token=secret["bot_token"],
            chat_id=secret["chat_id"],
        )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _chunk(items: list[Signal], size: int) -> list[list[Signal]]:
    return [items[i : i + size] for i in range(0, len(items), size)]
