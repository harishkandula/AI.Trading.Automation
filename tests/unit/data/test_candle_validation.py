"""Candle model validation tests."""
import sys
from datetime import datetime, timezone
sys.path.insert(0, "src")

import pytest
from pydantic import ValidationError
from app.models.candle import Candle, Timeframe


def _valid():
    return dict(
        instrument_id="TEST", exchange="NSE", trading_symbol="TEST",
        timeframe=Timeframe.D1,
        timestamp=datetime(2024, 1, 2, 9, 15, tzinfo=timezone.utc),
        open=100.0, high=110.0, low=90.0, close=105.0, volume=10000.0,
    )


def test_valid_candle_passes():
    c = Candle(**_valid())
    assert c.close == 105.0


def test_high_less_than_close_fails():
    d = _valid()
    d["high"] = 90.0  # < close 105
    with pytest.raises(ValidationError):
        Candle(**d)


def test_low_greater_than_close_fails():
    d = _valid()
    d["low"] = 110.0  # > close 105
    with pytest.raises(ValidationError):
        Candle(**d)


def test_negative_price_fails():
    d = _valid()
    d["close"] = -1.0
    with pytest.raises(ValidationError):
        Candle(**d)


def test_zero_price_fails():
    d = _valid()
    d["open"] = 0.0
    with pytest.raises(ValidationError):
        Candle(**d)


def test_negative_volume_fails():
    d = _valid()
    d["volume"] = -100.0
    with pytest.raises(ValidationError):
        Candle(**d)


def test_typical_price():
    c = Candle(**_valid())
    assert c.typical_price == pytest.approx((110.0 + 90.0 + 105.0) / 3)
