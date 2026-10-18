"""Tests for compute_metrics (app.backtesting.metrics)."""
from __future__ import annotations

import sys
from datetime import date, datetime
from zoneinfo import ZoneInfo

sys.path.insert(0, "src")

from app.backtesting.metrics import compute_metrics
from app.models.trade import Trade, TradeEvent, TradeStatus

IST = ZoneInfo("Asia/Kolkata")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _ts(d: date) -> datetime:
    return datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)


def _exit_event(d: date, price: float) -> TradeEvent:
    return TradeEvent(
        event_type="EXIT_CLOSED_TIME",
        timestamp=_ts(d),
        quantity=10,
        price=price,
        cost=0.0,
    )


def _trade(
    trade_id: str,
    symbol: str,
    entry_date: date,
    exit_date: date,
    entry_price: float,
    exit_price: float,
    qty: float = 10.0,
    status: TradeStatus = TradeStatus.CLOSED_TIME,
) -> Trade:
    pnl = (exit_price - entry_price) * qty
    return Trade(
        trade_id=trade_id,
        signal_id=f"sig_{trade_id}",
        strategy_id="test",
        strategy_version="1.0",
        symbol=symbol,
        exchange="NSE",
        entry_timestamp=_ts(entry_date),
        entry_price=entry_price,
        entry_quantity=qty,
        stop_price=entry_price * 0.95,
        realized_pnl=pnl,
        remaining_quantity=0,
        status=status,
        events=[_exit_event(exit_date, exit_price)],
    )


def _equity_curve(trades: list[Trade], initial: float = 10_000.0) -> list[tuple[date, float]]:
    """Build a simple equity curve from trades for metric tests."""
    eq = initial
    curve: list[tuple[date, float]] = []
    for t in trades:
        if t.entry_timestamp:
            curve.append((t.entry_timestamp.date(), eq))
        if t.events:
            eq += t.realized_pnl
            curve.append((t.events[-1].timestamp.date(), eq))
    return curve


# ---------------------------------------------------------------------------
# Tests: empty trades
# ---------------------------------------------------------------------------


def test_empty_trades_returns_error_dict() -> None:
    result = compute_metrics([], [], 10_000.0)
    assert "error" in result


# ---------------------------------------------------------------------------
# Tests: three winning trades
# ---------------------------------------------------------------------------


def test_three_winning_trades() -> None:
    trades = [
        _trade("t1", "AAA", date(2024, 1, 2), date(2024, 1, 7), 100.0, 110.0),
        _trade("t2", "BBB", date(2024, 1, 9), date(2024, 1, 14), 200.0, 220.0),
        _trade("t3", "CCC", date(2024, 1, 16), date(2024, 1, 21), 150.0, 165.0),
    ]
    curve = _equity_curve(trades)
    result = compute_metrics(trades, curve, 10_000.0)
    assert result["executed_trades"] == 3
    assert result["winners"] == 3
    assert result["losers"] == 0
    assert result["win_rate_pct"] == 100.0
    assert result["net_return_pct"] > 0


# ---------------------------------------------------------------------------
# Tests: two wins one loss — correct win_rate
# ---------------------------------------------------------------------------


def test_two_wins_one_loss_win_rate() -> None:
    trades = [
        _trade("t1", "AAA", date(2024, 1, 2), date(2024, 1, 7), 100.0, 110.0),
        _trade("t2", "BBB", date(2024, 1, 9), date(2024, 1, 14), 200.0, 180.0),  # loss
        _trade("t3", "CCC", date(2024, 1, 16), date(2024, 1, 21), 150.0, 165.0),
    ]
    curve = _equity_curve(trades)
    result = compute_metrics(trades, curve, 10_000.0)
    assert result["executed_trades"] == 3
    assert result["winners"] == 2
    assert result["losers"] == 1
    # win_rate should be 2/3 ≈ 66.67 %
    assert abs(result["win_rate_pct"] - 66.67) < 0.01


# ---------------------------------------------------------------------------
# Tests: max drawdown with a known equity curve
# ---------------------------------------------------------------------------


def test_max_drawdown_known_curve() -> None:
    """
    Equity sequence: 10 000 → 12 000 → 9 000 → 11 000
    Peak = 12 000, trough = 9 000  → drawdown = 3000/12000 = 25 %
    """
    from app.backtesting.metrics import _max_drawdown

    equity = [10_000.0, 12_000.0, 9_000.0, 11_000.0]
    dd, _ = _max_drawdown(equity)
    assert abs(dd - 0.25) < 1e-9


def test_max_drawdown_no_drawdown() -> None:
    from app.backtesting.metrics import _max_drawdown

    equity = [10_000.0, 11_000.0, 12_000.0]
    dd, dur = _max_drawdown(equity)
    assert dd == 0.0
    assert dur == 0


def test_max_drawdown_empty() -> None:
    from app.backtesting.metrics import _max_drawdown

    dd, dur = _max_drawdown([])
    assert dd == 0.0
    assert dur == 0


# ---------------------------------------------------------------------------
# Tests: profit_factor
# ---------------------------------------------------------------------------


def test_profit_factor_with_wins_and_losses() -> None:
    # Gross wins = 100 * 10 = 1000; gross losses = 20 * 10 = 200 → PF = 5.0
    trades = [
        _trade("t1", "A", date(2024, 1, 2), date(2024, 1, 7), 100.0, 200.0),   # +1000
        _trade("t2", "B", date(2024, 1, 9), date(2024, 1, 14), 100.0, 80.0),   # -200
    ]
    curve = _equity_curve(trades)
    result = compute_metrics(trades, curve, 10_000.0)
    assert result["profit_factor"] == 5.0


def test_profit_factor_no_losses_is_inf() -> None:
    trades = [
        _trade("t1", "A", date(2024, 1, 2), date(2024, 1, 7), 100.0, 120.0),
    ]
    curve = _equity_curve(trades)
    result = compute_metrics(trades, curve, 10_000.0)
    import math
    assert math.isinf(result["profit_factor"])


# ---------------------------------------------------------------------------
# Tests: avg_holding_sessions
# ---------------------------------------------------------------------------


def test_avg_holding_sessions_computed() -> None:
    # Entry Jan 2, exit Jan 7 → 5 calendar days
    # Entry Jan 9, exit Jan 11 → 2 calendar days
    trades = [
        _trade("t1", "A", date(2024, 1, 2), date(2024, 1, 7), 100.0, 110.0),
        _trade("t2", "B", date(2024, 1, 9), date(2024, 1, 11), 100.0, 105.0),
    ]
    curve = _equity_curve(trades)
    result = compute_metrics(trades, curve, 10_000.0)
    # avg should be (5 + 2) / 2 = 3.5
    assert result["avg_holding_sessions"] == 3.5
