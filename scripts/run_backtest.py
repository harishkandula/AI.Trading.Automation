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
import app.strategies.s00_benchmark  # noqa: F401
import app.strategies.s01_rs_momentum  # noqa: F401
import app.strategies.s02_vcp  # noqa: F401
import app.strategies.s03_trend_pullback  # noqa: F401

from app.strategies.registry import all_ids

configure_logging()
logger = get_logger(__name__)
settings = get_settings()


def _make_mock_provider(symbols: list[str], start: date, end: date) -> object:
    """Return a lightweight mock provider that generates synthetic trending candles."""
    from datetime import datetime, timedelta
    from unittest.mock import MagicMock
    from app.models.candle import Candle, Timeframe
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")

    def _candles(symbol: str, tf: object, s: date, e: date) -> list[Candle]:
        days = (e - s).days + 250  # include warmup
        result = []
        for i in range(days):
            d = s - timedelta(days=250) + timedelta(days=i)
            if d.weekday() >= 5:
                continue
            price = 100.0 + i * 0.05
            ts = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
            result.append(Candle(
                instrument_id=symbol, exchange="NSE", trading_symbol=symbol,
                timeframe=Timeframe.D1, timestamp=ts,
                open=price * 0.998, high=price * 1.01, low=price * 0.99,
                close=price, volume=1_000_000 + i * 500, source="mock",
            ))
        return result

    provider = MagicMock()
    provider.get_historical_candles.side_effect = _candles
    return provider


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

    # For the mock universe (CI smoke-test), inject synthetic candles so no
    # S3/Groww/Bhavcopy calls are made (DATA_BUCKET may not be set in CI).
    provider = None
    if args.universe == "mock":
        provider = _make_mock_provider(symbols, start, end)

    results = run_universe_backtest(
        symbols=symbols,
        strategy_ids=strategy_ids,
        start=start,
        end=end,
        initial_capital=args.capital,
        cost_profile=args.cost_profile,
        provider=provider,
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
        if args.universe in ("nifty50", "nifty_next50", "nifty500"):
            print("  NOTE: Real historical data — still not a guarantee of future performance")
        else:
            print("  NOTE: SYNTHETIC DATA – not real strategy evidence")

    os.makedirs(os.path.dirname(args.output) or ".", exist_ok=True)
    with open(args.output, "w") as fh:
        json.dump(results, fh, indent=2, default=str)
    logger.info("Results written to %s", args.output)

    # ------------------------------------------------------------------ #
    # Persist to S3 + DynamoDB leaderboard when running in AWS (ECS/Lambda)
    # ------------------------------------------------------------------ #
    bucket = os.environ.get("DATA_BUCKET", "")
    run_date = date.today().isoformat()
    if bucket:
        _persist_results(results, bucket, run_date, str(end))
    else:
        logger.info("DATA_BUCKET not set — skipping S3/leaderboard persist (local run)")


def _persist_results(
    results: dict,
    bucket: str,
    run_date: str,
    end_date: str,
) -> None:
    import boto3  # noqa: PLC0415
    from app.backtesting.leaderboard import write_result as _leaderboard_write  # noqa: PLC0415

    s3 = boto3.client("s3")

    for strategy_id, result in results.items():
        m = result.get("metrics", {})
        if "error" in m:
            continue

        # Save full result JSON to S3
        key = f"backtests/simple/{run_date}/{strategy_id}.json"
        try:
            s3.put_object(
                Bucket=bucket,
                Key=key,
                Body=json.dumps(result, indent=2, default=str),
                ContentType="application/json",
            )
            logger.info("S3: s3://%s/%s", bucket, key)
        except Exception:
            logger.warning("Failed to save %s to S3", strategy_id, exc_info=True)
            key = ""

        # Write summary to DynamoDB leaderboard
        try:
            aggregated = {
                "avg_sharpe": m.get("sharpe_ratio"),
                "avg_net_return_pct": m.get("net_return_pct"),
                "avg_max_drawdown_pct": m.get("max_drawdown_pct"),
                "avg_win_rate_pct": m.get("win_rate_pct"),
                "avg_profit_factor": m.get("profit_factor"),
                "total_oos_trades": m.get("executed_trades", 0),
                "consistency_score": None,
                "validated": False,
                "windows_count": 1,
            }
            provenance = {
                "run_date": run_date,
                "end_date": end_date,
                "runner": "scripts.run_backtest",
            }
            _leaderboard_write(
                strategy_id=strategy_id,
                run_date=run_date,
                strategy_version="simple",
                aggregated=aggregated,
                provenance=provenance,
                windows_count=1,
                s3_key=key,
            )
            logger.info("Leaderboard: wrote %s", strategy_id)
        except Exception:
            logger.warning(
                "Leaderboard write failed for %s (LEADERBOARD_TABLE set?)",
                strategy_id, exc_info=True,
            )


if __name__ == "__main__":
    main()
