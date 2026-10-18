"""Unit tests for Notifier central notification service."""
from __future__ import annotations

import json
import sys
from datetime import date, datetime, timezone
from unittest.mock import MagicMock, patch

sys.path.insert(0, "src")

from app.models.signal import Signal, SignalStatus
from app.notifications.notifier import Notifier, _chunk


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_signal(
    symbol: str = "RELIANCE.NS",
    strategy_id: str = "rsi_supertrend_volume",
    entry: float = 2500.0,
    stop: float = 2425.0,
    sig_date: date | None = None,
) -> Signal:
    if sig_date is None:
        sig_date = date(2024, 1, 15)
    ts = datetime(sig_date.year, sig_date.month, sig_date.day, 15, 30, tzinfo=timezone.utc)
    return Signal(
        signal_id=f"test-{symbol}-{strategy_id}",
        strategy_id=strategy_id,
        strategy_version="1.0.0",
        symbol=symbol,
        exchange="NSE",
        timeframe="1d",
        signal_timestamp=ts,
        decision_timestamp=ts,
        entry_reference_price=entry,
        stop_price=stop,
        target_prices=[round(entry * 1.05, 2), round(entry * 1.10, 2)],
        holding_period_sessions=5,
        indicators={"rsi": 65.2, "supertrend": 2450.0},
        qualification_reasons=["RSI in range", "Supertrend positive"],
        status=SignalStatus.ELIGIBLE,
    )


# ---------------------------------------------------------------------------
# _chunk helper
# ---------------------------------------------------------------------------

def test_chunk_empty():
    assert _chunk([], 10) == []


def test_chunk_exact_multiple():
    items = [_make_signal() for _ in range(10)]
    chunks = _chunk(items, 5)
    assert len(chunks) == 2
    assert all(len(c) == 5 for c in chunks)


def test_chunk_remainder():
    items = [_make_signal() for _ in range(3)]
    chunks = _chunk(items, 10)
    assert len(chunks) == 1
    assert len(chunks[0]) == 3


# ---------------------------------------------------------------------------
# send_signal_alert — empty list
# ---------------------------------------------------------------------------

@patch("app.notifications.telegram.send")
def test_send_signal_alert_empty(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    result = notifier.send_signal_alert([])
    assert result is True
    mock_send.assert_called_once()
    call_args = mock_send.call_args[0]
    assert "No signals" in call_args[2]


# ---------------------------------------------------------------------------
# send_signal_alert — 3 signals
# ---------------------------------------------------------------------------

@patch("app.notifications.telegram.send")
def test_send_signal_alert_three_signals(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")

    signals = [
        _make_signal("RELIANCE.NS", "gap_up_volume"),
        _make_signal("INFY.NS", "rsi_supertrend_volume"),
        _make_signal("TCS.NS", "morning_star"),
    ]
    result = notifier.send_signal_alert(signals)
    assert result is True
    # 3 signals fit in a single message chunk
    mock_send.assert_called_once()
    message = mock_send.call_args[0][2]
    assert "Signal Alert" in message
    assert "RELIANCE" in message


@patch("app.notifications.telegram.send")
def test_send_signal_alert_groups_by_strategy(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")

    signals = [
        _make_signal("RELIANCE.NS", "gap_up_volume"),
        _make_signal("INFY.NS", "gap_up_volume"),
        _make_signal("TCS.NS", "morning_star"),
    ]
    notifier.send_signal_alert(signals)
    message = mock_send.call_args[0][2]
    assert "gap_up_volume" in message
    assert "morning_star" in message


@patch("app.notifications.telegram.send")
def test_send_signal_alert_chunks_at_11(mock_send):
    """11 signals should produce 2 messages (chunk size = 10)."""
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    signals = [_make_signal(f"SYM{i}.NS") for i in range(11)]
    notifier.send_signal_alert(signals)
    assert mock_send.call_count == 2


# ---------------------------------------------------------------------------
# send_daily_summary
# ---------------------------------------------------------------------------

@patch("app.notifications.telegram.send")
def test_send_daily_summary_no_signals(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    result = notifier.send_daily_summary(
        scan_date=date(2024, 1, 15),
        universe_size=50,
        scanned=50,
        signals=[],
        errors=0,
    )
    assert result is True
    mock_send.assert_called_once()
    message = mock_send.call_args[0][2]
    assert "2024-01-15" in message
    assert "50" in message


@patch("app.notifications.telegram.send")
def test_send_daily_summary_with_signals(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    signals = [_make_signal("RELIANCE.NS")]
    result = notifier.send_daily_summary(
        scan_date=date(2024, 1, 15),
        universe_size=50,
        scanned=48,
        signals=signals,
        errors=2,
    )
    assert result is True
    message = mock_send.call_args[0][2]
    assert "RELIANCE" in message


@patch("app.notifications.telegram.send")
def test_send_error_alert(mock_send):
    mock_send.return_value = True
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    result = notifier.send_error_alert(
        context="morning_handler", error="Connection timeout"
    )
    assert result is True
    message = mock_send.call_args[0][2]
    assert "morning_handler" in message
    assert "Connection timeout" in message


# ---------------------------------------------------------------------------
# from_secrets classmethod
# ---------------------------------------------------------------------------

def test_from_secrets_reads_correct_keys():
    secret_payload = json.dumps({"bot_token": "BOT123", "chat_id": "CHAT456"})
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": secret_payload}

    with patch("boto3.client", return_value=mock_client) as mock_boto:
        notifier = Notifier.from_secrets(
            secret_id="market-ai-dev/telegram", region="ap-south-1"
        )

    mock_boto.assert_called_once_with("secretsmanager", region_name="ap-south-1")
    mock_client.get_secret_value.assert_called_once_with(
        SecretId="market-ai-dev/telegram"
    )
    assert notifier._bot_token == "BOT123"  # noqa: SLF001
    assert notifier._chat_id == "CHAT456"  # noqa: SLF001


def test_from_secrets_default_args():
    secret_payload = json.dumps({"bot_token": "X", "chat_id": "Y"})
    mock_client = MagicMock()
    mock_client.get_secret_value.return_value = {"SecretString": secret_payload}

    with patch("boto3.client", return_value=mock_client):
        notifier = Notifier.from_secrets()

    assert notifier._bot_token == "X"  # noqa: SLF001


# ---------------------------------------------------------------------------
# send returns False propagates correctly
# ---------------------------------------------------------------------------

@patch("app.notifications.telegram.send")
def test_send_signal_alert_propagates_failure(mock_send):
    mock_send.return_value = False
    notifier = Notifier(bot_token="TOKEN", chat_id="CHAT")
    signals = [_make_signal()]
    result = notifier.send_signal_alert(signals)
    assert result is False
