#!/usr/bin/env python3
"""
Run a backtest for one strategy or all strategies.

Usage (PowerShell / bash):
    python -m scripts.run_backtest --strategy all --universe nifty50 \\
        --start 2023-01-01 --end 2025-12-31 --output reports/backtest_results.json

    python -m scripts.run_backtest --strategy rsi_supertrend_volume --universe mock

For real NIFTY 50 data pass --universe nifty50 (requires internet access + yfinance).
For a quick offline smoke-test pass --universe mock.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date

sys.path.insert(0, "src")

from app.core.config import get_settings
from app.core.logging import configure_logging, get_logger
from app.data.universe import get_universe

# Side-effect imports – trigger @register on all strategy classes
import app.strategies.gap_up_volume  # noqa: F401
import app.strategies.intraday_support_supertrend  # noqa: F401
import app.strategies.morning_star  # noqa: F401
import app.strategies.rsi_supertrend_volume  # noqa: F401

from app.strategies.registry import all_ids

configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run backtests over a symbol universe")
    parser.add_argument("--strategy", default="all", help="Strategy ID or 'all'")
    parser.add_argument(
        "--universe",
        default="mock",
        help="Universe name: nifty50 (real data) or mock (offline)",
    )
    parser.add_argument("--start", default="2023-01-01", help="Start date YYYY-MM-DD")
    parser.add_argument("--end", default="2025-12-31", help="End date YYYY-MM-DD")
    parser.add_argument(
        "--capital",
        type=float,
        default=settings.research_capital,
        help="Starting capital per strategy (INR)",
    )
    parser.add_argument("--cost-profile", default="default_delivery_estimate")
    parser.add_argument(
        "--output",
        default="reports/backtest_results.json",
        help="Output file path for JSON results",
    )
    args = parser.parse_args()

    start = date.fromisoformat(args.start)
    end = date.fromisoformat(args.end)

    symbols = get_universe(args.universe)
    strategy_ids = all_ids() if args.strategy == "all" else [args.strategy]

    logger.info(
        "Universe: %s (%d symbols)  strategies: %s  %s → %s",
        args.universe, len(symbols), strategy_ids, start, end,
    )

    # ------------------------------------------------------------------ #
    # Use the high-level runner which handles data fetch + engine + metrics
    # ------------------------------------------------------------------ #
    from app.backtesting.runner import run_universe_backtest  # noqa: PLC0415

    results = run_universe_backtest(
        symbols=symbols,
        strategy_ids=strategy_ids,
        start=start,
        end=end,
        initial_capital=args.capital,
        cost_profile=args.cost_profile,
    )

    # ------------------------------------------------------------------ #
    # Pretty-print summary
    # ------------------------------------------------------------------ #
    for strategy_id, result in results.items():
        m = result.get("metrics", {})
        print(f"\n=== {strategy_id} ===")
        if "error" in m:
            print(f"  No closed trades: {m.get('error', 'unknown')}")
            continue
        print(f"  Signals          : {result.get('signals_count', 0)}")
        print(f"  Symbols scanned  : {result.get('symbols_scanned', 0)}")
        print(f"  Executed trades  : {m.get('executed_trades', 0)}")
        print(f"  Win rate         : {m.get('win_rate_pct', 0):.1f}%")
        print(f"  Net return       : {m.get('net_return_pct', 0):.2f}%")
        print(f"  Sharpe ratio     : {m.get('sharpe_ratio', 'N/A')}")
        print(f"  Profit factor    : {m.get('profit_factor', 0)}")
        print(f"  Max drawdown     : {m.get('max_drawdown_pct', 0):.2f}%")
        if args.universe in ("nifty50", "nifty_next50"):
            print("  NOTE: Real historical data — still not a guarantee of future performance")
        else:
            print("  NOTE: SYNTHETIC DATA – not real strategy evidence")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    logger.info("Results written to %s", args.output)


if __name__ == "__main__":
    main()
