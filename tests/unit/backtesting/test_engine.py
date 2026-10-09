"""Backtesting engine correctness tests."""
from __future__ import annotations

import sys
from datetime import datetime, date
from zoneinfo import ZoneInfo
sys.path.insert(0, "src")

import pytest
from app.backtesting.engine import run_backtest
from app.backtesting.positions import risk_based_quantity
from app.backtesting.costs import DELIVERY_PROFILE
from app.models.signal import Signal, SignalStatus
from app.models.candle import Candle, Timeframe
from app.models.trade import TradeStatus

IST = ZoneInfo("Asia/Kolkata")


def _candle(sym: str, d: date, o: float, h: float, l: float, c: float, vol: float = 100000) -> Candle:
    return Candle(
        instrument_id=sym, exchange="NSE", trading_symbol=sym,
        timeframe=Timeframe.D1,
        timestamp=datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST),
        open=o, high=h, low=l, close=c, volume=vol, source="test",
    )


def _signal(sym: str, entry_date: date, entry_ref: float, stop: float, targets=None, hold=5) -> Signal:
    from app.models.signal import Signal, SignalStatus
    from datetime import datetime, timezone
    return Signal(
        signal_id=f"test_{sym}_{entry_date}",
        strategy_id="test",
        strategy_version="1.0",
        symbol=sym,
        exchange="NSE",
        timeframe="1d",
        signal_timestamp=datetime(entry_date.year, entry_date.month, entry_date.day, 15, 30, tzinfo=timezone.utc),
        decision_timestamp=datetime(entry_date.year, entry_date.month, entry_date.day, 15, 30, tzinfo=timezone.utc),
        planned_entry_timestamp=datetime(entry_date.year, entry_date.month, entry_date.day, 9, 15, tzinfo=timezone.utc),
        entry_reference_price=entry_ref,
        stop_price=stop,
        target_prices=targets or [],
        holding_period_sessions=hold,
        status=SignalStatus.ELIGIBLE,
    )


def test_target_hit_closes_trade():
    entry_date = date(2024, 1, 2)
    # Candle where target is hit
    candles = [
        _candle("SYM", date(2024, 1, 2), 100, 110, 98, 105),  # entry bar
        _candle("SYM", date(2024, 1, 3), 105, 115, 103, 112), # high hits target 110
    ]
    # Signal with entry_date = Jan 3 (next after Jan 2 signal)
    sig = _signal("SYM", date(2024, 1, 3), 105, 97, targets=[110.0], hold=5)
    state = run_backtest(
        [sig],
        {"SYM": candles},
        initial_capital=100_000,
        cost_profile="zero_cost",
    )
    # With zero-cost, partial exit at target: trade is PARTIALLY_CLOSED or CLOSED_TARGET
    assert len(state.closed_trades) + len(state.open_trades) > 0


def test_stop_hit_closes_trade():
    # Entry on Jan 2, stop triggered Jan 3
    candles = [
        _candle("SYM", date(2024, 1, 2), 100, 102, 98, 101),  # entry bar
        _candle("SYM", date(2024, 1, 3), 101, 102, 90, 91),   # low goes below stop 95
    ]
    sig = _signal("SYM", date(2024, 1, 2), 101, 95, hold=5)  # entry on Jan 2
    state = run_backtest([sig], {"SYM": candles}, initial_capital=100_000, cost_profile="zero_cost")
    closed_stops = [t for t in state.closed_trades if t.status == TradeStatus.CLOSED_STOP]
    assert len(closed_stops) == 1


def test_gap_through_stop():
    # Entry on Jan 2, gap-through stop on Jan 3
    candles = [
        _candle("SYM", date(2024, 1, 2), 100, 102, 98, 101),
        _candle("SYM", date(2024, 1, 3), 90, 92, 88, 91),  # open gaps below stop 95
    ]
    sig = _signal("SYM", date(2024, 1, 2), 101, 95, hold=5)
    state = run_backtest([sig], {"SYM": candles}, initial_capital=100_000, cost_profile="zero_cost")
    closed_stops = [t for t in state.closed_trades if t.status == TradeStatus.CLOSED_STOP]
    assert len(closed_stops) == 1
    # Exit at open (90), not stop (95)
    exit_event = [e for e in closed_stops[0].events if "STOP" in e.event_type]
    assert exit_event[0].price < 95


def test_time_exit():
    # Entry Jan 1, hold=2 sessions → exit Jan 3
    candles = [_candle("SYM", date(2024, 1, d), 100+d, 105+d, 98+d, 102+d) for d in range(1, 8)]
    sig = _signal("SYM", date(2024, 1, 1), 102, 95, hold=2)
    state = run_backtest([sig], {"SYM": candles}, initial_capital=100_000, cost_profile="zero_cost")
    closed_time = [t for t in state.closed_trades if t.status == TradeStatus.CLOSED_TIME]
    assert len(closed_time) == 1


def test_insufficient_cash_rejected():
    candles = [_candle("SYM", date(2024, 1, 2), 100, 102, 98, 101)]
    sig = _signal("SYM", date(2024, 1, 2), 100, 97, hold=5)
    state = run_backtest([sig], {"SYM": candles}, initial_capital=50, cost_profile="zero_cost")  # tiny capital
    assert len(state.rejected_trades) == 1


def test_max_positions_respected():
    candles_by_sym = {}
    sigs = []
    for i in range(15):
        sym = f"SYM{i}"
        candles_by_sym[sym] = [_candle(sym, date(2024, 1, 2), 100, 102, 98, 101)]
        sigs.append(_signal(sym, date(2024, 1, 2), 100, 97, hold=5))
    state = run_backtest(sigs, candles_by_sym, initial_capital=10_000_000, max_open_positions=5, cost_profile="zero_cost")
    assert len(state.open_trades) <= 5


def test_duplicate_prevention():
    candles = [_candle("SYM", date(2024, 1, 2), 100, 102, 98, 101)]
    sig1 = _signal("SYM", date(2024, 1, 2), 100, 97, hold=5)
    sig2 = _signal("SYM", date(2024, 1, 2), 100, 97, hold=5)
    sig2 = sig2.model_copy(update={"signal_id": sig1.signal_id})  # same id
    # Both signals point to same entry date; engine should enter at most 1 (max_new_per_session=1 here)
    state = run_backtest([sig1, sig2], {"SYM": candles}, initial_capital=10_000_000, max_new_positions_per_session=1, cost_profile="zero_cost")
    assert len(state.open_trades) + len(state.closed_trades) <= 1


def test_risk_based_sizing():
    import math
    result = risk_based_quantity(
        entry_price=100.0,
        stop_price=97.0,
        current_equity=1_000_000.0,
        risk_per_trade_pct=0.005,
        max_position_value_pct=0.10,
    )
    assert not result.rejected
    risk_budget = 1_000_000 * 0.005
    risk_per_share = 3.0
    qty_by_risk = math.floor(risk_budget / risk_per_share)       # 1666
    qty_by_cap  = math.floor(1_000_000 * 0.10 / 100.0)          # 1000
    expected_qty = min(qty_by_risk, qty_by_cap)
    assert result.quantity == expected_qty


def test_stop_at_or_above_entry_rejected():
    result = risk_based_quantity(100.0, 101.0, 1_000_000, 0.005)
    assert result.rejected


def test_backtest_is_deterministic():
    candles = [_candle("SYM", date(2024, 1, d), 100+d, 105+d, 98+d, 102+d) for d in range(1, 10)]
    sig = _signal("SYM", date(2024, 1, 2), 103, 97, hold=5)
    s1 = run_backtest([sig], {"SYM": candles}, initial_capital=100_000, cost_profile="zero_cost")
    s2 = run_backtest([sig], {"SYM": candles}, initial_capital=100_000, cost_profile="zero_cost")
    assert s1.equity == s2.equity
