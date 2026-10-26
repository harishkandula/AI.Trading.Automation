"""
Universe-level backtest runner.

Fetches OHLCV data via HybridDataProvider (S3 Parquet → Groww → NSE Bhavcopy),
generates signals for every symbol in the universe, then runs the event-driven
engine once per strategy.

No look-ahead: identical to production signal generation — the engine processes
bars in chronological order and signals only enter on the *next* bar's open.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any

from app.backtesting.engine import run_backtest
from app.backtesting.metrics import compute_metrics
from app.data.providers.hybrid_provider import HybridDataProvider
from app.models.candle import Timeframe
from app.models.trade import Trade

# Side-effect imports — trigger @register decorators on all strategy classes
import app.strategies.gap_up_volume  # noqa: F401
import app.strategies.intraday_support_supertrend  # noqa: F401
import app.strategies.morning_star  # noqa: F401
import app.strategies.rsi_supertrend_volume  # noqa: F401
import app.strategies.s00_benchmark  # noqa: F401
import app.strategies.s01_rs_momentum  # noqa: F401
import app.strategies.s02_vcp  # noqa: F401
import app.strategies.s03_trend_pullback  # noqa: F401

from app.strategies.registry import get as _get_strategy

log = logging.getLogger(__name__)

# Extra days prepended to fetch_start so indicators have enough history to warm up
_WARMUP_BUFFER_DAYS = 120


def run_universe_backtest(
    symbols: list[str],
    strategy_ids: list[str],
    start: date,
    end: date,
    initial_capital: float = 100_000.0,
    cost_profile: str = "default_delivery_estimate",
    provider: HybridDataProvider | None = None,
) -> dict[str, Any]:
    """
    Back-test each requested strategy over the given universe.

    Parameters
    ----------
    symbols:
        NSE ticker symbols (e.g. ``["RELIANCE", "INFY"]``).
    strategy_ids:
        Registry IDs of strategies to run.  Intraday strategies that need 5-minute
        candles are skipped automatically.
    start / end:
        Target signal window.  Signals with a planned entry *before* ``start``
        (warmup period) are discarded so they do not inflate returns.
    initial_capital:
        Starting portfolio value in INR, applied independently per strategy.
    cost_profile:
        Cost model key passed to the engine
        (``"default_delivery_estimate"`` or ``"zero_cost"``).
    provider:
        Optional HybridDataProvider instance (injected for testing).

    Returns
    -------
    dict[strategy_id, {"metrics": dict, "trades": list[dict], "signals_count": int,
    "symbols_scanned": int}]
    """
    if provider is None:
        provider = HybridDataProvider()

    fetch_start = start - timedelta(days=_WARMUP_BUFFER_DAYS)

    # ------------------------------------------------------------------ #
    # Fetch daily candles once — shared by all daily strategies            #
    # ------------------------------------------------------------------ #
    candles_by_symbol: dict[str, list[Any]] = {}
    for symbol in symbols:
        bare = symbol.removesuffix(".NS")
        try:
            bars = provider.get_historical_candles(bare, Timeframe.D1, fetch_start, end)
            if bars:
                candles_by_symbol[bare] = bars
            else:
                log.warning("No candles returned for %s", bare)
        except Exception:
            log.warning("Failed to fetch candles for %s", bare, exc_info=True)

    log.info(
        "Fetched candles for %d / %d symbols", len(candles_by_symbol), len(symbols)
    )

    results: dict[str, Any] = {}

    for strategy_id in strategy_ids:
        try:
            strategy_cls = _get_strategy(strategy_id)
        except KeyError:
            log.warning("Strategy %r not registered; skipping", strategy_id)
            continue

        strategy = strategy_cls()

        # Intraday strategies require 5-minute candles — skip on daily run
        if strategy.timeframe == Timeframe.M5:
            log.info("Skipping intraday strategy %s (requires 5 m candles)", strategy_id)
            continue

        log.info("Running backtest: %s  %s → %s", strategy_id, start, end)

        all_signals: list[Any] = []
        for symbol, candles in candles_by_symbol.items():
            if len(candles) < strategy.warmup_bars + 5:
                log.debug(
                    "Too few candles for %s on %s (%d < %d)",
                    strategy_id, symbol, len(candles), strategy.warmup_bars + 5,
                )
                continue
            try:
                for sig in strategy.generate_signals(symbol, candles):
                    # Discard signals whose planned entry falls outside the target window
                    if sig.planned_entry_timestamp is not None:
                        entry_d = sig.planned_entry_timestamp.date()
                        if start <= entry_d <= end:
                            all_signals.append(sig)
            except Exception:
                log.warning(
                    "Signal generation failed — strategy=%s symbol=%s",
                    strategy_id, symbol, exc_info=True,
                )

        log.info("  %s: %d signals generated", strategy_id, len(all_signals))

        state = run_backtest(
            all_signals,
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
            total_signals=len(all_signals),
        )

        # Serialise closed trades to plain dicts for JSON / S3 storage
        trade_dicts = [
            {
                "trade_id": t.trade_id,
                "symbol": t.symbol,
                "strategy_id": t.strategy_id,
                "entry_price": t.entry_price,
                "entry_quantity": t.entry_quantity,
                "realized_pnl": round(t.realized_pnl, 4),
                "status": t.status.value,
                "entry_timestamp": (
                    t.entry_timestamp.isoformat() if t.entry_timestamp else None
                ),
                "exit_timestamp": (
                    t.events[-1].timestamp.isoformat() if t.events else None
                ),
            }
            for t in state.closed_trades
        ]

        results[strategy_id] = {
            "metrics": metrics,
            "trades": trade_dicts,
            "signals_count": len(all_signals),
            "symbols_scanned": len(candles_by_symbol),
        }

    return results
