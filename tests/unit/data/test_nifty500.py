"""Tests for NIFTY 500 universe."""
from __future__ import annotations

from app.data.nifty500_symbols import (
    NIFTY500,
    SYMBOL_TO_SECTOR,
    SYMBOL_TO_SEGMENT,
    all_symbols,
    all_sectors,
    symbols_by_sector,
)


def test_nifty500_has_enough_symbols():
    assert len(NIFTY500) >= 400


def test_no_duplicate_symbols():
    symbols = [m.symbol for m in NIFTY500]
    assert len(symbols) == len(set(symbols))


def test_all_have_segment():
    for m in NIFTY500:
        assert m.segment in ("LARGE", "MID", "SMALL"), f"{m.symbol} has bad segment {m.segment}"


def test_all_have_sector():
    for m in NIFTY500:
        assert m.sector, f"{m.symbol} has empty sector"


def test_large_cap_count():
    large = all_symbols("LARGE")
    assert len(large) >= 80


def test_mid_cap_count():
    mid = all_symbols("MID")
    assert len(mid) >= 50


def test_small_cap_count():
    small = all_symbols("SMALL")
    assert len(small) >= 100


def test_all_symbols_no_filter_equals_total():
    assert len(all_symbols()) == len(NIFTY500)


def test_all_sectors_returns_list():
    sectors = all_sectors()
    assert len(sectors) > 5
    assert len(sectors) == len(set(sectors))


def test_symbols_by_sector_banking():
    banking = symbols_by_sector("Banking")
    assert len(banking) > 0
    for sym in banking:
        assert SYMBOL_TO_SECTOR[sym] == "Banking"


def test_symbol_to_meta_lookups():
    assert "RELIANCE" in SYMBOL_TO_SEGMENT
    assert SYMBOL_TO_SEGMENT["RELIANCE"] == "LARGE"
    assert SYMBOL_TO_SECTOR["RELIANCE"] == "Energy"
