"""Support/resistance – no future bars test."""
import sys
sys.path.insert(0, "src")

from app.indicators.support_resistance import rolling_support, rolling_resistance


def test_support_uses_only_prior_bars():
    lows = [float(100 - i) for i in range(25)]  # 100, 99, 98...
    result = rolling_support(lows, lookback=5)
    # At i=20, support = min(lows[15:20]) = min(85, 84, 83, 82, 81) = 81
    assert result[20] == 81.0
    # At i=5, support = min(lows[0:5]) = min(100,99,98,97,96) = 96
    assert result[5] == 96.0


def test_warm_up_nones():
    result = rolling_support([100.0] * 30, lookback=20)
    assert all(v is None for v in result[:20])
    assert result[20] is not None


def test_resistance_uses_only_prior_bars():
    highs = [float(i) for i in range(25)]
    result = rolling_resistance(highs, lookback=5)
    assert result[5] == 4.0  # max(highs[0:5]) = 4


def test_support_not_affected_by_future():
    lows = [100.0] * 20 + [50.0] * 5  # future bars have low=50
    result = rolling_support(lows, lookback=20)
    # At i=20, support = min(lows[0:20]) = 100 (future 50 not included)
    assert result[20] == 100.0
