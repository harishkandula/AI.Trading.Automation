"""Tests for EMA indicator."""
from __future__ import annotations

import pytest

from app.indicators.ema import ema, ema_slope, latest_ema
from app.core.exceptions import InsufficientDataError


def test_ema_length_matches_input():
    closes = [float(i + 1) for i in range(30)]
    result = ema(closes, 10)
    assert len(result) == 30


def test_ema_warmup_nones():
    closes = [100.0] * 20
    result = ema(closes, 10)
    assert result[:9] == [None] * 9
    assert result[9] is not None


def test_ema_constant_series_equals_constant():
    closes = [50.0] * 30
    result = ema(closes, 10)
    for v in result[9:]:
        assert v is not None
        assert abs(v - 50.0) < 1e-9


def test_ema_rising_series_ema_below_price():
    closes = [float(i) for i in range(1, 51)]
    result = ema(closes, 10)
    last_price = closes[-1]
    last_ema = result[-1]
    assert last_ema is not None
    assert last_ema < last_price


def test_latest_ema_returns_float():
    closes = [100.0 + i * 0.5 for i in range(30)]
    val = latest_ema(closes, 10)
    assert isinstance(val, float)


def test_ema_raises_on_insufficient_data():
    with pytest.raises(InsufficientDataError):
        ema([100.0] * 5, 10)


def test_ema_slope_positive_on_uptrend():
    closes = [float(i) for i in range(1, 60)]
    slope = ema_slope(closes, 20, lookback=5)
    assert slope > 0


def test_ema_slope_negative_on_downtrend():
    closes = [float(60 - i) for i in range(60)]
    slope = ema_slope(closes, 20, lookback=5)
    assert slope < 0
