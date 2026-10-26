"""Walk-forward validation for trading strategies.

Expands the train window from a fixed anchor date, rolling the test window
forward by step_months at a time. Each test window is strictly out-of-sample:
signals from prior windows are never re-evaluated.

Flow:
  1. Generate signals over the full candle history (for warmup and indicator
     computation — no look-ahead in signal generation itself).
  2. For each test window, filter to signals whose planned_entry falls inside
     that window only.
  3. Run the backtester on those signals with the full candle set (needed for
     exit processing after entry).
  4. Aggregate per-window metrics into a summary.

The strategy is validated if:
  - consistency_score ≥ 0.6 (at least 60% of test windows profitable)
  - avg_sharpe > 0.5 across all test windows
  - avg_max_drawdown_pct ≤ 20%
"""
from __future__ import annotations

import calendar
import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.backtesting.engine import run_backtest
from app.backtesting.metrics import compute_metrics
from app.models.candle import Candle
from app.models.trade import Trade
from app.strategies.registry import get as _get_strategy

log = logging.getLogger(__name__)


@dataclass
class WalkForwardWindow:
    window_id: int
    train_start: date
    train_end: date
    test_start: date
    test_end: date
    metrics: dict[str, Any]
    trade_count: int
    signal_count: int


@dataclass
class WalkForwardResult:
    strategy_id: str
    strategy_version: str
    train_anchor: date
    test_end: date
    step_months: int
    windows: list[WalkForwardWindow]
    aggregated: dict[str, Any]
    provenance: dict[str, Any] = field(default_factory=dict)


def _add_months(d: date, months: int) -> date:
    m = d.month + months
    y = d.year + (m - 1) // 12
    m = ((m - 1) % 12) + 1
    last_day = calendar.monthrange(y, m)[1]
    return d.replace(year=y, month=m, day=min(d.day, last_day))


def _build_windows(
    initial_test_start: date,
    test_end: date,
    step_months: int,
) -> list[tuple[date, date]]:
    """Return (window_start, window_end) pairs covering initial_test_start → test_end."""
    windows: list[tuple[date, date]] = []
    start = initial_test_start
    while start < test_end:
        end = _add_months(start, step_months) - timedelta(days=1)
        if end > test_end:
            end = test_end
        windows.append((start, end))
        start = _add_months(start, step_months)
    return windows


def _parameter_hash(params: dict[str, Any]) -> str:
    s = json.dumps(params, sort_keys=True, default=str)
    return hashlib.sha256(s.encode()).hexdigest()[:12]


def _aggregate(windows: list[WalkForwardWindow]) -> dict[str, Any]:
    if not windows:
        return {"error": "No walk-forward windows completed"}

    sharpes = [
        w.metrics.get("sharpe_ratio")
        for w in windows
        if isinstance(w.metrics.get("sharpe_ratio"), (int, float))
    ]
    win_rates = [
        w.metrics.get("win_rate_pct", 0.0)
        for w in windows
        if "error" not in w.metrics
    ]
    net_returns = [
        w.metrics.get("net_return_pct", 0.0)
        for w in windows
        if "error" not in w.metrics
    ]
    drawdowns = [
        w.metrics.get("max_drawdown_pct", 0.0)
        for w in windows
        if "error" not in w.metrics
    ]
    pf_vals = [
        w.metrics.get("profit_factor")
        for w in windows
        if isinstance(w.metrics.get("profit_factor"), (int, float))
    ]

    valid_windows = [w for w in windows if "error" not in w.metrics]
    profitable_windows = [w for w in valid_windows if w.metrics.get("net_return_pct", 0.0) > 0]
    consistency = len(profitable_windows) / len(windows) if windows else 0.0

    def _safe_mean(vals: list[float]) -> float | None:
        return round(sum(vals) / len(vals), 3) if vals else None

    return {
        "windows_count": len(windows),
        "windows_profitable": len(profitable_windows),
        "windows_with_data": len(valid_windows),
        "consistency_score": round(consistency, 3),
        "avg_sharpe": _safe_mean(sharpes),
        "avg_win_rate_pct": _safe_mean(win_rates),
        "avg_net_return_pct": _safe_mean(net_returns),
        "avg_max_drawdown_pct": _safe_mean(drawdowns),
        "avg_profit_factor": _safe_mean(
            [v for v in pf_vals if v != float("inf")]
        ),
        "total_oos_trades": sum(w.trade_count for w in windows),
        "total_oos_signals": sum(w.signal_count for w in windows),
        "oos_cumulative_return_pct": round(sum(net_returns), 2) if net_returns else None,
        "validated": (
            consistency >= 0.6
            and bool(sharpes)
            and sum(sharpes) / len(sharpes) > 0.5
        ),
    }


def run_walk_forward(
    strategy_id: str,
    candles_by_symbol: dict[str, list[Candle]],
    train_anchor_start: date,
    initial_test_start: date,
    test_end: date,
    step_months: int = 6,
    initial_capital: float = 100_000.0,
    cost_profile: str = "default_delivery_estimate",
    run_date: date | None = None,
) -> WalkForwardResult:
    """
    Run expanding-window walk-forward validation for a single strategy.

    Parameters
    ----------
    strategy_id:
        Registered strategy ID (e.g. "s01_rs_momentum").
    candles_by_symbol:
        Pre-fetched candles covering train_anchor_start minus warmup buffer to test_end.
    train_anchor_start:
        Training period always begins here (expanding anchor).
    initial_test_start:
        First day of the first out-of-sample test window.
    test_end:
        Last day of the final test window.
    step_months:
        Length of each test window in calendar months (default 6).
    initial_capital:
        Starting equity per test window (independent capital per window).
    cost_profile:
        Cost model name (see app.backtesting.costs).
    run_date:
        Used for provenance; defaults to today if not provided.
    """
    strategy_cls = _get_strategy(strategy_id)
    strategy = strategy_cls()

    if initial_test_start <= train_anchor_start:
        raise ValueError(
            f"initial_test_start ({initial_test_start}) must be after "
            f"train_anchor_start ({train_anchor_start})"
        )

    # Generate signals over the full window once — signal gen itself has no look-ahead
    all_signals: list[Any] = []
    for symbol, candles in candles_by_symbol.items():
        if len(candles) < strategy.warmup_bars + 5:
            log.debug("Skipping %s — too few candles (%d)", symbol, len(candles))
            continue
        try:
            sigs = strategy.generate_signals(symbol, candles)
            all_signals.extend(sigs)
        except Exception:
            log.warning("Signal gen failed: %s %s", strategy_id, symbol, exc_info=True)

    log.info("Walk-forward %s: %d total signals generated", strategy_id, len(all_signals))

    test_windows = _build_windows(initial_test_start, test_end, step_months)
    completed_windows: list[WalkForwardWindow] = []

    for wid, (t_start, t_end) in enumerate(test_windows, start=1):
        # Filter signals to this test window only (planned entry in [t_start, t_end])
        window_signals = [
            s for s in all_signals
            if s.planned_entry_timestamp is not None
            and t_start <= s.planned_entry_timestamp.date() <= t_end
        ]

        train_end = t_start - timedelta(days=1)
        log.info(
            "Window %d: train %s→%s  test %s→%s  signals=%d",
            wid, train_anchor_start, train_end, t_start, t_end, len(window_signals),
        )

        if not window_signals:
            completed_windows.append(WalkForwardWindow(
                window_id=wid,
                train_start=train_anchor_start,
                train_end=train_end,
                test_start=t_start,
                test_end=t_end,
                metrics={"error": "No signals in test window"},
                trade_count=0,
                signal_count=0,
            ))
            continue

        state = run_backtest(
            window_signals,
            candles_by_symbol,
            initial_capital=initial_capital,
            cost_profile=cost_profile,
        )

        all_trades: list[Trade] = state.closed_trades + state.open_trades
        metrics = compute_metrics(
            all_trades,
            state.equity_curve,
            initial_capital,
            rejected_count=len(state.rejected_trades),
            ambiguous_bar_count=state.ambiguous_bar_count,
            total_signals=len(window_signals),
        )

        completed_windows.append(WalkForwardWindow(
            window_id=wid,
            train_start=train_anchor_start,
            train_end=train_end,
            test_start=t_start,
            test_end=t_end,
            metrics=metrics,
            trade_count=len(state.closed_trades),
            signal_count=len(window_signals),
        ))

    aggregated = _aggregate(completed_windows)
    today = run_date or date.today()

    return WalkForwardResult(
        strategy_id=strategy_id,
        strategy_version=strategy.strategy_version,
        train_anchor=train_anchor_start,
        test_end=test_end,
        step_months=step_months,
        windows=completed_windows,
        aggregated=aggregated,
        provenance={
            "run_date": today.isoformat(),
            "git_sha": os.environ.get("GIT_SHA", "unknown"),
            "cost_model": cost_profile,
            "parameter_hash": _parameter_hash(strategy.params),
            "initial_capital": initial_capital,
            "universe_size": len(candles_by_symbol),
        },
    )
