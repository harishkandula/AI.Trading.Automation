"""ATR unit tests."""
import pytest
import sys
sys.path.insert(0, "src")

from app.indicators.atr import wilder_atr, true_range
from app.core.exceptions import InsufficientDataError


def test_true_range_basic():
    tr = true_range(high=110, low=90, prev_close=100)
    assert tr == 20.0  # high-low dominates


def test_true_range_gap_up():
    tr = true_range(high=115, low=112, prev_close=100)
    assert tr == pytest.approx(15.0)  # high - prev_close


def test_atr_warm_up_nones():
    h = [101.0] * 20
    lo = [99.0] * 20
    c = [100.0] * 20
    result = wilder_atr(h, lo, c, period=14)
    assert all(v is None for v in result[:14])
    assert result[14] is not None


def test_atr_insufficient_data():
    with pytest.raises(InsufficientDataError):
        wilder_atr([101]*5, [99]*5, [100]*5, period=14)


def test_atr_constant_series():
    h = [101.0] * 20
    lo = [99.0] * 20
    c = [100.0] * 20
    result = wilder_atr(h, lo, c, period=14)
    # True range is always 2.0 for constant series
    for v in result[14:]:
        assert v == pytest.approx(2.0)
