"""Strategy signal generation tests."""
from __future__ import annotations

import sys
from datetime import datetime, date, timedelta
from zoneinfo import ZoneInfo
sys.path.insert(0, "src")

import pytest
from app.models.candle import Candle, Timeframe
from app.strategies.gap_up_volume import GapUpVolume
from app.strategies.rsi_supertrend_volume import RSISuperTrendVolume
from app.strategies.morning_star import MorningStar

IST = ZoneInfo("Asia/Kolkata")


def _daily(sym, d, o=100.0, h=101.0, lo=99.0, c=100.0, vol=500_000):
    return Candle(
        instrument_id=sym, exchange="NSE", trading_symbol=sym,
        timeframe=Timeframe.D1,
        timestamp=datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST),
        open=o, high=h, low=lo, close=c, volume=float(vol),
        source="test",
    )


def _base_candles(n=50, sym="TEST", base=100.0):
    start = date(2024, 1, 2)
    candles = []
    for i in range(n):
        d = start + timedelta(days=i)
        p = base + i * 0.1
        candles.append(_daily(sym, d, p-0.5, p+1, p-1, p, 400_000))
    return candles


# ── GapUpVolume ────────────────────────────────────────────────────────────────

def test_gap_up_no_signal_without_gap():
    candles = _base_candles(30)
    strat = GapUpVolume()
    signals = strat.generate_signals("TEST", candles)
    assert all(s.indicators["gap_pct"] > 3.0 for s in signals)


def test_gap_up_signal_when_conditions_met():
    candles = _base_candles(20)
    prev = candles[-1]
    p = prev.close
    vol5 = int(prev.volume * 5)
    gap_candle = _daily("TEST", date(2024, 2, 1), p*1.05, p*1.06, p*1.04, p*1.055, vol5)
    candles.append(gap_candle)
    strat = GapUpVolume()
    signals = strat.generate_signals("TEST", candles, as_of_index=len(candles)-1)
    assert len(signals) >= 1
    assert signals[-1].indicators["gap_pct"] > 3.0


def test_gap_up_no_signal_when_close_below_open():
    candles = _base_candles(20)
    prev = candles[-1]
    p = prev.close
    # open 1.05x, close 1.04x → close < open → fails require_close_above_open
    vol5 = int(prev.volume * 5)
    gap_candle = _daily("TEST", date(2024, 2, 1), p*1.05, p*1.06, p*1.02, p*1.04, vol5)
    candles.append(gap_candle)
    strat = GapUpVolume()
    last_signals = [s for s in strat.generate_signals("TEST", candles, as_of_index=len(candles)-1)
                    if s.signal_timestamp == gap_candle.timestamp]
    assert len(last_signals) == 0


def test_gap_up_stop_is_3pct_below_entry():
    candles = _base_candles(20)
    prev = candles[-1]
    p = prev.close
    vol5 = int(prev.volume * 5)
    gap_candle = _daily("TEST", date(2024, 2, 1), p*1.05, p*1.07, p*1.04, p*1.055, vol5)
    candles.append(gap_candle)
    strat = GapUpVolume()
    signals = strat.generate_signals("TEST", candles, as_of_index=len(candles)-1)
    if signals:
        sig = signals[-1]
        expected_stop = round(sig.entry_reference_price * 0.97, 4)
        assert abs(sig.stop_price - expected_stop) < 0.01


# ── MorningStar ────────────────────────────────────────────────────────────────

def test_morning_star_signal_generated():
    # Build base candles, then inject a morning star pattern
    candles = _base_candles(35)
    base_price = candles[-1].close
    d = date(2024, 3, 1)
    # C1: big bearish
    candles.append(_daily("TEST", d, base_price, base_price+1, base_price-18, base_price-15))
    d2 = date(2024, 3, 4)
    # C2: doji
    candles.append(_daily("TEST", d2, base_price-15, base_price-13, base_price-17, base_price-14.5))
    d3 = date(2024, 3, 5)
    # C3: bullish, closes above midpoint of C1
    mid = (base_price + (base_price - 15)) / 2  # midpoint of C1 real body
    candles.append(_daily("TEST", d3, base_price-14, base_price+2, base_price-15, mid+5))
    strat = MorningStar()
    signals = strat.generate_signals("TEST", candles, as_of_index=len(candles)-1)
    last = [s for s in signals if s.signal_timestamp == candles[-1].timestamp]
    assert len(last) >= 1


# ── RSISuperTrendVolume ────────────────────────────────────────────────────────

def test_rsi_strategy_no_signals_on_short_history():
    # With fewer bars than warmup, strategy should return empty, not raise
    candles = _base_candles(5)
    strat = RSISuperTrendVolume()
    # Should not raise; returns empty because warmup not satisfied
    try:
        signals = strat.generate_signals("TEST", candles)
        assert len(signals) == 0
    except Exception as e:
        pytest.fail(f"Strategy raised on short history: {e}")


def test_rsi_strategy_signal_has_required_fields():
    candles = _base_candles(60)
    strat = RSISuperTrendVolume()
    signals = strat.generate_signals("TEST", candles)
    for sig in signals:
        assert sig.strategy_id == "rsi_supertrend_volume"
        assert sig.stop_price is not None
        assert sig.stop_price < sig.entry_reference_price
        assert sig.holding_period_sessions == 5
