"""Tests for the Relative Strength engine."""
from __future__ import annotations

from app.research.relative_strength import compute_rs_for_symbol, _return_over


def test_return_over_basic():
    closes = [100.0, 105.0, 110.0]
    r = _return_over(closes, 2)
    assert r is not None
    assert abs(r - 0.1) < 1e-9


def test_return_over_insufficient_data():
    closes = [100.0, 110.0]
    assert _return_over(closes, 5) is None


def test_rs_outperformance():
    # Stock up 20%, index up 10% — RS should be > 0
    stock = [100.0] * 21 + [120.0]
    index = [100.0] * 21 + [110.0]
    result = compute_rs_for_symbol(stock, index)
    assert result["rs_20d"] is not None
    assert result["rs_20d"] > 0


def test_rs_underperformance():
    # Stock up 5%, index up 15% — RS should be < 0
    stock = [100.0] * 21 + [105.0]
    index = [100.0] * 21 + [115.0]
    result = compute_rs_for_symbol(stock, index)
    assert result["rs_20d"] is not None
    assert result["rs_20d"] < 0


def test_rs_none_when_insufficient_data():
    short = [100.0] * 5
    index = [100.0] * 5
    result = compute_rs_for_symbol(short, index)
    assert result["rs_20d"] is None
    assert result["rs_60d"] is None


def test_rs_vs_sector_computed_when_sector_provided():
    stock = [100.0] * 21 + [125.0]
    index = [100.0] * 21 + [110.0]
    sector = [100.0] * 21 + [112.0]
    result = compute_rs_for_symbol(stock, index, sector)
    assert result["rs_vs_sector_20d"] is not None


def test_rs_vs_sector_none_when_not_provided():
    stock = [100.0] * 61 + [120.0]
    index = [100.0] * 61 + [110.0]
    result = compute_rs_for_symbol(stock, index)
    assert result["rs_vs_sector_20d"] is None
    assert result["rs_vs_sector_60d"] is None
