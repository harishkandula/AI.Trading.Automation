"""Morning Star pattern tests."""
import sys
sys.path.insert(0, "src")

from app.indicators.candlestick_patterns import CandleAttrs, is_morning_star


def _candle(o, h, l, c):
    return CandleAttrs(o, h, l, c)


def test_classic_morning_star():
    c1 = _candle(100, 102, 80, 82)  # bearish, big body
    c2 = _candle(80, 82, 78, 81)    # indecisive doji
    c3 = _candle(82, 105, 80, 103)  # bullish, closes above c1 midpoint (91)
    ok, reasons = is_morning_star(c1, c2, c3)
    assert ok, f"Expected True, reasons: {reasons}"


def test_c1_not_bearish_fails():
    c1 = _candle(80, 102, 78, 102)  # bullish
    c2 = _candle(100, 102, 98, 101)
    c3 = _candle(101, 110, 100, 108)
    ok, _ = is_morning_star(c1, c2, c3)
    assert not ok


def test_c2_too_big_fails():
    c1 = _candle(100, 102, 80, 82)
    c2 = _candle(80, 100, 78, 90)   # big body > 30%
    c3 = _candle(90, 110, 88, 108)
    ok, _ = is_morning_star(c1, c2, c3)
    assert not ok


def test_c3_doesnt_close_above_midpoint_fails():
    c1 = _candle(100, 102, 80, 82)  # midpoint = 91
    c2 = _candle(80, 82, 78, 81)
    c3 = _candle(82, 90, 80, 88)    # closes at 88 < midpoint 91
    ok, _ = is_morning_star(c1, c2, c3)
    assert not ok


def test_c3_not_bullish_fails():
    c1 = _candle(100, 102, 80, 82)
    c2 = _candle(80, 82, 78, 81)
    c3 = _candle(95, 97, 80, 82)    # bearish
    ok, _ = is_morning_star(c1, c2, c3)
    assert not ok
