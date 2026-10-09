"""Backtest metrics computation."""
from __future__ import annotations

import math
import statistics
from datetime import date
from typing import Any

from app.models.trade import Trade, TradeStatus


def compute_metrics(
    trades: list[Trade],
    equity_curve: list[tuple[date, float]],
    initial_capital: float,
    rejected_count: int = 0,
    ambiguous_bar_count: int = 0,
    total_signals: int = 0,
) -> dict[str, Any]:
    closed = [t for t in trades if t.status not in (TradeStatus.PENDING_ENTRY, TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)]
    if not closed:
        return {"error": "No closed trades", "total_signals": total_signals}

    pnls = [t.realized_pnl for t in closed]
    entries = [t.entry_price or 0.0 for t in closed]
    entry_values = [p * t.entry_quantity for p, t in zip(entries, closed)]

    winners = [t for t in closed if t.realized_pnl > 0]
    losers = [t for t in closed if t.realized_pnl < 0]
    breakevens = [t for t in closed if t.realized_pnl == 0]

    win_rate = len(winners) / len(closed) if closed else 0.0
    avg_win_pct = statistics.mean([(t.realized_pnl / (t.entry_price * t.entry_quantity)) for t in winners]) if winners else 0.0
    avg_loss_pct = statistics.mean([(t.realized_pnl / (t.entry_price * t.entry_quantity)) for t in losers]) if losers else 0.0

    gross_wins = sum(t.realized_pnl for t in winners)
    gross_losses = abs(sum(t.realized_pnl for t in losers))
    profit_factor = gross_wins / gross_losses if gross_losses > 0 else float("inf")
    expectancy_pct = win_rate * avg_win_pct + (1 - win_rate) * avg_loss_pct

    # Ending capital
    ending_capital = initial_capital + sum(pnls)
    gross_return_pct = (ending_capital - initial_capital) / initial_capital * 100

    # Drawdown
    eq_values = [v for _, v in equity_curve]
    max_dd, max_dd_dur = _max_drawdown(eq_values)

    # Sharpe (daily returns)
    daily_returns = _daily_returns(equity_curve)
    sharpe = _sharpe(daily_returns) if len(daily_returns) > 1 else None

    # Holding periods
    holding_periods = _holding_periods(closed)

    # Performance by year
    by_year: dict[str, float] = {}
    for t in closed:
        if t.entry_timestamp:
            yr = str(t.entry_timestamp.year)
            by_year[yr] = by_year.get(yr, 0.0) + t.realized_pnl

    # Profitable months
    profitable_months = _profitable_months(equity_curve)

    # Max consecutive
    max_con_win, max_con_loss = _max_consecutive(closed)

    return {
        "starting_capital": initial_capital,
        "ending_capital": round(ending_capital, 2),
        "gross_return_pct": round(gross_return_pct, 2),
        "net_return_pct": round(gross_return_pct, 2),  # costs already in pnl
        "total_signals": total_signals,
        "eligible_trades": len(closed) + rejected_count,
        "executed_trades": len(closed),
        "rejected_trades": rejected_count,
        "winners": len(winners),
        "losers": len(losers),
        "breakeven_trades": len(breakevens),
        "win_rate_pct": round(win_rate * 100, 2),
        "avg_winner_pct": round(avg_win_pct * 100, 2),
        "avg_loser_pct": round(avg_loss_pct * 100, 2),
        "payoff_ratio": round(abs(avg_win_pct / avg_loss_pct), 2) if avg_loss_pct != 0 else None,
        "expectancy_pct": round(expectancy_pct * 100, 2),
        "profit_factor": round(profit_factor, 2),
        "max_drawdown_pct": round(max_dd * 100, 2),
        "max_drawdown_duration_sessions": max_dd_dur,
        "sharpe_ratio": round(sharpe, 3) if sharpe is not None else None,
        "max_consecutive_wins": max_con_win,
        "max_consecutive_losses": max_con_loss,
        "avg_holding_sessions": round(statistics.mean(holding_periods), 1) if holding_periods else 0,
        "median_holding_sessions": round(statistics.median(holding_periods), 1) if holding_periods else 0,
        "ambiguous_bar_count": ambiguous_bar_count,
        "profitable_months_pct": round(profitable_months, 1),
        "performance_by_year": {k: round(v, 2) for k, v in sorted(by_year.items())},
        "survivorship_bias_warning": True,
        "cost_model_note": "Estimated – verify current statutory rates",
    }


def _max_drawdown(equity: list[float]) -> tuple[float, int]:
    if not equity:
        return 0.0, 0
    peak = equity[0]
    max_dd = 0.0
    max_dur = 0
    dur = 0
    for v in equity:
        if v >= peak:
            peak = v
            dur = 0
        else:
            dd = (peak - v) / peak
            max_dd = max(max_dd, dd)
            dur += 1
            max_dur = max(max_dur, dur)
    return max_dd, max_dur


def _daily_returns(equity_curve: list[tuple[date, float]]) -> list[float]:
    values = [v for _, v in equity_curve]
    if len(values) < 2:
        return []
    return [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]


def _sharpe(returns: list[float], risk_free: float = 0.0) -> float | None:
    if len(returns) < 2:
        return None
    mean = statistics.mean(returns) - risk_free / 252
    std = statistics.stdev(returns)
    if std == 0:
        return None
    return mean / std * math.sqrt(252)


def _holding_periods(trades: list[Trade]) -> list[int]:
    periods = []
    for t in trades:
        if t.entry_timestamp and t.events:
            last_exit = [e for e in t.events if "EXIT" in e.event_type]
            if last_exit:
                delta = last_exit[-1].timestamp - t.entry_timestamp
                periods.append(max(1, delta.days))
    return periods


def _max_consecutive(trades: list[Trade]) -> tuple[int, int]:
    max_win = max_loss = cur_win = cur_loss = 0
    for t in trades:
        if t.realized_pnl > 0:
            cur_win += 1
            cur_loss = 0
        elif t.realized_pnl < 0:
            cur_loss += 1
            cur_win = 0
        else:
            cur_win = cur_loss = 0
        max_win = max(max_win, cur_win)
        max_loss = max(max_loss, cur_loss)
    return max_win, max_loss


def _profitable_months(equity_curve: list[tuple[date, float]]) -> float:
    if not equity_curve:
        return 0.0
    monthly: dict[str, list[float]] = {}
    for d, v in equity_curve:
        key = f"{d.year}-{d.month:02d}"
        monthly.setdefault(key, []).append(v)
    profitable = sum(
        1 for vals in monthly.values()
        if len(vals) >= 2 and vals[-1] > vals[0]
    )
    return profitable / len(monthly) * 100 if monthly else 0.0
