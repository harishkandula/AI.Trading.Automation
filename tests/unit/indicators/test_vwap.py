"""VWAP unit tests – session reset."""
import pytest
import sys
from datetime import datetime
from zoneinfo import ZoneInfo
sys.path.insert(0, "src")

from app.indicators.vwap import session_vwap

IST = ZoneInfo("Asia/Kolkata")


def _ts(date_str: str, hour: int, minute: int) -> datetime:
    return datetime.fromisoformat(f"{date_str}T{hour:02d}:{minute:02d}:00+05:30")


def test_single_bar_vwap_equals_typical_price():
    ts = [_ts("2024-01-02", 9, 15)]
    highs, lows, closes, volumes = [110.0], [90.0], [100.0], [1000.0]
    result = session_vwap(ts, highs, lows, closes, volumes)
    assert result[0] == pytest.approx((110 + 90 + 100) / 3)


def test_vwap_resets_on_new_day():
    ts = [_ts("2024-01-02", 9, 15), _ts("2024-01-02", 9, 20), _ts("2024-01-03", 9, 15)]
    h = [110.0, 112.0, 120.0]
    l = [90.0, 92.0, 100.0]
    c = [100.0, 102.0, 110.0]
    v = [1000.0, 2000.0, 500.0]
    result = session_vwap(ts, h, l, c, v)
    # Day 2 bar 1 VWAP should equal typical price of that bar alone
    expected_day2_bar1 = (120 + 100 + 110) / 3
    assert result[2] == pytest.approx(expected_day2_bar1)


def test_zero_volume_returns_none():
    ts = [_ts("2024-01-02", 9, 15)]
    result = session_vwap(ts, [110.0], [90.0], [100.0], [0.0])
    assert result[0] is None
