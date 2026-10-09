#!/usr/bin/env python3
"""
Run a backtest for one strategy or all strategies.

Usage (PowerShell):
    python -m scripts.run_backtest --strategy all --universe mock --start 2023-01-01 --end 2024-12-31
    python -m scripts.run_backtest --strategy rsi_supertrend_volume --universe mock
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date

sys.path.insert(0, "src")

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.data.providers.mock import MockDataProvider
from app.models.candle import Timeframe
from app.backtesting.engine import run_backtest
from app.backtesting.metrics import compute_metrics
import app.strategies.rsi_supertrend_volume  # noqa: F401 – register
import app.strategies.morning_star          # noqa: F401 – register
import app.strategies.gap_up_volume        # noqa: F401 – register
import app.strategies.intraday_support_supertrend  # noqa: F401 – register
from app.strategies.registry import all_ids, get

configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtests")
    parser.add_argument("--strategy", default="all", help="Strategy ID or 'all'")
    parser.add_argument("--universe", default="mock", help="Universe name (mock supported)")
    parser.add_argument("--start", default="2022-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2024-12-31", help="End date YYYY-MM-DD")
    parser.add_argument("--capital", type=float, default=settings.research_capital)
    parser.add_argument("--cost-profile", default="default_delivery_estimate")
    parser.add_argument("--output", default="reports/backtest_results.json")
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    provider = MockDataProvider()
    instruments = provider.get_instruments()
    logger.info(f"Universe: {len(instruments)} instruments")

    strategies_to_run = all_ids() if args.strategy == "all" else [args.strategy]

    all_results = {}

    for strategy_id in strategies_to_run:
        try:
            strategy_cls = get(strategy_id)
        except KeyError as e:
            logger.error(str(e))
            continue

        strategy = strategy_cls()

        # Skip intraday strategies on daily backtest (need 5m candles)
        if strategy.timeframe == Timeframe.M5:
            logger.info(f"Skipping {strategy_id} (intraday – needs 5m candles, not available in mock daily backtest)")
            continue

        logger.info(f"Running backtest: {strategy_id} {start} -> {end}")

        candles_by_symbol: dict = {}
        all_signals = []

        for inst in instruments:
            candles = provider.get_historical_candles(inst.trading_symbol, Timeframe.D1, start, end)
            if len(candles) < strategy.warmup_bars + 5:
                continue
            candles_by_symbol[inst.trading_symbol] = candles
            sigs = strategy.generate_signals(inst.trading_symbol, candles)
            all_signals.extend(sigs)

        logger.info(f"  Signals: {len(all_signals)}")

        state = run_backtest(
            all_signals,
            candles_by_symbol,
            initial_capital=args.capital,
            cost_profile=args.cost_profile,
        )

        metrics = compute_metrics(
            state.closed_trades + state.open_trades,
            state.equity_curve,
            args.capital,
            rejected_count=len(state.rejected_trades),
            ambiguous_bar_count=state.ambiguous_bar_count,
            total_signals=len(all_signals),
        )
        all_results[strategy_id] = metrics
        print(f"\n=== {strategy_id} ===")
        print(f"  Executed trades : {metrics.get('executed_trades', 0)}")
        print(f"  Win rate        : {metrics.get('win_rate_pct', 0):.1f}%")
        print(f"  Net return      : {metrics.get('net_return_pct', 0):.2f}%")
        print(f"  Profit factor   : {metrics.get('profit_factor', 0)}")
        print(f"  Max drawdown    : {metrics.get('max_drawdown_pct', 0):.2f}%")
        print(f"  NOTE: SYNTHETIC DATA – not real strategy evidence")

    import os
    os.makedirs("reports", exist_ok=True)
    with open(args.output, "w") as f:
        json.dump(all_results, f, indent=2, default=str)
    logger.info(f"Results written to {args.output}")


if __name__ == "__main__":
    main()
