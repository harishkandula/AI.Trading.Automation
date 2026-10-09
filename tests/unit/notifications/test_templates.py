"""Notification template tests."""
import sys
from datetime import date, datetime, timezone
sys.path.insert(0, "src")

from app.notifications.templates import format_daily_report, format_signal, DISCLAIMER


def _sig():
    return {
        "strategy_id": "rsi_supertrend_volume",
        "symbol": "TESTCO",
        "decision_timestamp": "2024-01-15T15:30:00+00:00",
        "entry_reference_price": 250.0,
        "stop_price": 242.5,
        "holding_period_sessions": 5,
        "qualification_reasons": ["RSI(14)=65.2", "Supertrend positive", "Volume condition"],
    }


def test_format_signal_contains_strategy():
    text = format_signal(_sig())
    assert "rsi_supertrend_volume" in text


def test_format_signal_contains_disclaimer_in_report():
    report = format_daily_report(
        scan_date=date(2024, 1, 15),
        universe_size=100,
        scanned=100,
        rejected_data_quality=2,
        signals=[_sig()],
    )
    assert "RESEARCH SIGNAL" in report
    assert "NOT A TRADE INSTRUCTION" in report


def test_empty_signals_renders():
    report = format_daily_report(
        scan_date=date(2024, 1, 15),
        universe_size=50,
        scanned=50,
        rejected_data_quality=0,
        signals=[],
    )
    assert "Signals found: 0" in report


def test_multiple_signals_renders():
    sigs = [_sig() for _ in range(3)]
    report = format_daily_report(date(2024, 1, 15), 100, 100, 0, sigs)
    assert "TESTCO" in report
