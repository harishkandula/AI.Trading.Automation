"""RSI unit tests – manually verifiable sequence."""
import pytest
import sys
sys.path.insert(0, "src")

from app.indicators.rsi import wilder_rsi, latest_rsi
from app.core.exceptions import InsufficientDataError


def test_rsi_requires_period_plus_one_bars():
    with pytest.raises(InsufficientDataError):
        wilder_rsi([100.0] * 14, period=14)  # need 15


def test_rsi_warm_up_nones():
    closes = [float(x) for x in range(1, 20)]
    result = wilder_rsi(closes, period=14)
    assert all(v is None for v in result[:14])
    assert result[14] is not None


def test_rsi_constant_series_gives_100():
    # No losses in a constant series -> RSI = 100
    closes = [100.0] * 20
    result = wilder_rsi(closes, period=14)
    assert result[14] == 100.0


def test_rsi_range():
    closes = [
        100, 102, 101, 103, 99, 98, 100, 105, 107, 104, 106, 108, 107, 109, 111, 113, 110, 112
    ]
    result = wilder_rsi([float(c) for c in closes], period=14)
    for v in result[14:]:
        assert 0 <= v <= 100


def test_rsi_known_sequence():
    # Descending: only losses -> RSI should be 0
    closes = [float(100 - i) for i in range(20)]
    result = wilder_rsi(closes, period=14)
    assert result[-1] == 0.0


def test_latest_rsi_returns_float():
    closes = [float(x) for x in range(1, 25)]
    val = latest_rsi(closes, period=14)
    assert isinstance(val, float)
