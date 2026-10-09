"""Supertrend unit tests – trend direction transitions."""
import pytest
import sys
sys.path.insert(0, "src")

from app.indicators.supertrend import supertrend, latest_supertrend


def _trending_up(n: int = 50, start: float = 100.0, step: float = 1.0):
    closes = [start + i * step for i in range(n)]
    highs = [c + 2 for c in closes]
    lows = [c - 2 for c in closes]
    return highs, lows, closes


def _trending_down(n: int = 50, start: float = 200.0, step: float = -1.0):
    closes = [start + i * step for i in range(n)]
    highs = [c + 2 for c in closes]
    lows = [max(1.0, c - 2) for c in closes]
    return highs, lows, closes


def test_warm_up_nones():
    h, l, c = _trending_up(30)
    results = supertrend(h, l, c, period=10)
    assert all(r.direction is None for r in results[:10])
    assert results[10].direction is not None


def test_uptrend_gives_positive_direction():
    h, l, c = _trending_up(50)
    results = supertrend(h, l, c, period=10)
    non_none = [r for r in results if r.direction is not None]
    assert non_none[-1].direction == 1


def test_downtrend_gives_negative_direction():
    h, l, c = _trending_down(50)
    results = supertrend(h, l, c, period=10)
    non_none = [r for r in results if r.direction is not None]
    assert non_none[-1].direction == -1


def test_latest_supertrend_uptrend():
    h, l, c = _trending_up(50)
    result = latest_supertrend(h, l, c)
    assert result.direction == 1
    assert result.value is not None
    assert result.value < c[-1]  # line is below price in uptrend
