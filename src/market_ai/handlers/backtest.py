"""
Backtest Lambda handler.

Triggered by EventBridge (Sunday 11:00 IST weekly).
Runs all registered daily strategies in walk-forward mode over the NIFTY 500,
saves per-strategy results to S3, and sends a ranked Telegram summary.

Event payload (all keys optional):
    {
        "mode":         "walk_forward" | "simple",
        "start":        "2019-01-01",   # train anchor; default 2019-01-01
        "end":          "2025-12-31",   # test end; default today
        "test_start":   "2023-01-01",   # first OOS window; default start + 4 years
        "universe":     "nifty500",     # "nifty50" | "nifty500"; default "nifty500"
        "capital":      100000.0,       # initial capital per window; default 100 000
        "step_months":  6               # walk-forward window size; default 6
    }
"""
from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import boto3  # type: ignore[import-untyped]

from app.backtesting.leaderboard import write_result as _leaderboard_write
from app.backtesting.runner import run_universe_backtest
from app.backtesting.walk_forward import WalkForwardResult, run_walk_forward
from app.data.nifty500_symbols import all_symbols as _nifty500_symbols
from app.data.providers.hybrid_provider import HybridDataProvider
from app.models.candle import Timeframe
from app.notifications import telegram as _telegram
from app.notifications.notifier import Notifier
from app.strategies.registry import all_ids

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_DEFAULT_TRAIN_ANCHOR = date(2019, 1, 1)
_DEFAULT_TEST_OFFSET_YEARS = 4
_S3_PREFIX = "backtests"
_WARMUP_BUFFER_DAYS = 120


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the weekly backtest run (Sunday 11:00 IST)."""
    today = datetime.now(tz=IST).date()

    try:
        return _run_backtest(event, today)
    except Exception as exc:
        log.exception("Backtest handler fatal error")
        try:
            notifier = Notifier.from_secrets()
            notifier.send_error_alert("backtest_handler", str(exc))
        except Exception:
            log.exception("Failed to send error alert")
        return {
            "statusCode": 500,
            "body": f"Backtest run failed: {exc}",
        }


def _run_backtest(event: Any, today: date) -> dict:
    evt = event if isinstance(event, dict) else {}
    mode: str = evt.get("mode", "walk_forward")

    end = date.fromisoformat(evt["end"]) if "end" in evt else today
    start = (
        date.fromisoformat(evt["start"])
        if "start" in evt
        else _DEFAULT_TRAIN_ANCHOR
    )
    test_start = (
        date.fromisoformat(evt["test_start"])
        if "test_start" in evt
        else start + timedelta(days=365 * _DEFAULT_TEST_OFFSET_YEARS)
    )
    universe_name: str = evt.get("universe", "nifty500")
    capital: float = float(evt.get("capital", 100_000.0))
    step_months: int = int(evt.get("step_months", 6))

    symbols = _resolve_universe(universe_name)
    strategy_ids = [
        sid for sid in all_ids()
        if not _is_intraday(sid)
    ]

    log.info(
        "Backtest %s: universe=%s (%d symbols) train=%s test=%s→%s strategies=%d",
        mode, universe_name, len(symbols), start, test_start, end, len(strategy_ids),
    )

    bucket = _data_bucket()
    run_date = today.isoformat()
    s3_keys: dict[str, str] = {}

    if mode == "walk_forward":
        s3_keys = _run_walk_forward_all(
            strategy_ids, symbols, start, test_start, end,
            capital, step_months, bucket, run_date, today,
        )
        msg = _format_walk_forward_summary(run_date, start, test_start, end, universe_name, s3_keys)
    else:
        results = run_universe_backtest(
            symbols=symbols,
            strategy_ids=strategy_ids,
            start=start,
            end=end,
            initial_capital=capital,
        )
        if bucket:
            s3_keys = _save_simple_to_s3(bucket, run_date, results)
        msg = _format_simple_summary(run_date, start, end, universe_name, results)

    try:
        _send_telegram(msg)
    except Exception:
        log.exception("Failed to send Telegram backtest summary")

    return {
        "statusCode": 200,
        "body": {
            "mode": mode,
            "run_date": run_date,
            "train_start": start.isoformat(),
            "test_start": test_start.isoformat(),
            "end": end.isoformat(),
            "universe": universe_name,
            "strategies_run": strategy_ids,
            "s3_keys": s3_keys,
        },
    }


def _run_walk_forward_all(
    strategy_ids: list[str],
    symbols: list[str],
    train_anchor: date,
    test_start: date,
    test_end: date,
    capital: float,
    step_months: int,
    bucket: str,
    run_date: str,
    today: date,
) -> dict[str, str]:
    """Fetch candles once, run walk-forward per strategy, save results to S3."""
    from datetime import timedelta as _td

    provider = HybridDataProvider()
    fetch_start = train_anchor - _td(days=_WARMUP_BUFFER_DAYS)

    # Fetch candles once for all strategies
    candles_by_symbol: dict[str, Any] = {}
    for sym in symbols:
        bare = sym.removesuffix(".NS")
        try:
            bars = provider.get_historical_candles(bare, Timeframe.D1, fetch_start, test_end)
            if bars:
                candles_by_symbol[bare] = bars
            else:
                log.warning("No candles for %s", bare)
        except Exception:
            log.warning("Candle fetch failed: %s", bare, exc_info=True)

    log.info("Loaded candles for %d / %d symbols", len(candles_by_symbol), len(symbols))

    s3_keys: dict[str, str] = {}
    for strategy_id in strategy_ids:
        try:
            result = run_walk_forward(
                strategy_id=strategy_id,
                candles_by_symbol=candles_by_symbol,
                train_anchor_start=train_anchor,
                initial_test_start=test_start,
                test_end=test_end,
                step_months=step_months,
                initial_capital=capital,
                run_date=today,
            )
            if bucket:
                key = _save_walk_forward_to_s3(bucket, run_date, result)
                if key:
                    s3_keys[strategy_id] = key
            log.info(
                "Walk-forward %s: validated=%s sharpe=%.2f consistency=%.0f%%",
                strategy_id,
                result.aggregated.get("validated"),
                result.aggregated.get("avg_sharpe") or 0,
                (result.aggregated.get("consistency_score") or 0) * 100,
            )
            # Persist to leaderboard DynamoDB table (non-fatal if table not configured)
            try:
                s3_key = s3_keys.get(strategy_id, "")
                _leaderboard_write(
                    strategy_id=strategy_id,
                    run_date=run_date,
                    strategy_version=result.strategy_version,
                    aggregated=result.aggregated,
                    provenance=result.provenance,
                    windows_count=len(result.windows),
                    s3_key=s3_key,
                )
            except Exception:
                log.warning(
                    "Leaderboard write failed for %s (LEADERBOARD_TABLE set?)",
                    strategy_id, exc_info=True,
                )
        except Exception:
            log.warning("Walk-forward failed for %s", strategy_id, exc_info=True)

    return s3_keys


def _is_intraday(strategy_id: str) -> bool:
    from app.strategies.registry import get
    try:
        cls = get(strategy_id)
        from app.models.candle import Timeframe as TF
        return cls.timeframe == TF.M5
    except Exception:
        return False


def _resolve_universe(name: str) -> list[str]:
    if name == "nifty500":
        return _nifty500_symbols()
    if name == "nifty50":
        from app.data.nifty500_symbols import all_symbols
        return all_symbols(segment="LARGE_CAP")[:50]
    from app.data.universe import get_universe
    return get_universe(name)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _data_bucket() -> str:
    return os.environ.get("DATA_BUCKET", "")


def _save_walk_forward_to_s3(
    bucket: str,
    run_date: str,
    result: WalkForwardResult,
) -> str:
    s3 = boto3.client("s3")
    key = f"{_S3_PREFIX}/walk_forward/{run_date}/{result.strategy_id}.json"
    payload: dict[str, Any] = {
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "train_anchor": result.train_anchor.isoformat(),
        "test_end": result.test_end.isoformat(),
        "step_months": result.step_months,
        "aggregated": result.aggregated,
        "provenance": result.provenance,
        "windows": [
            {
                "window_id": w.window_id,
                "train_start": w.train_start.isoformat(),
                "train_end": w.train_end.isoformat(),
                "test_start": w.test_start.isoformat(),
                "test_end": w.test_end.isoformat(),
                "signal_count": w.signal_count,
                "trade_count": w.trade_count,
                "metrics": w.metrics,
            }
            for w in result.windows
        ],
    }
    try:
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(payload, indent=2, default=str),
            ContentType="application/json",
        )
        log.info("Saved walk-forward %s → s3://%s/%s", result.strategy_id, bucket, key)
    except Exception:
        log.warning("Failed to save walk-forward to S3: %s", result.strategy_id, exc_info=True)
        return ""
    return key


def _save_simple_to_s3(bucket: str, run_date: str, results: dict[str, Any]) -> dict[str, str]:
    s3 = boto3.client("s3")
    keys: dict[str, str] = {}
    for strategy_id, result in results.items():
        key = f"{_S3_PREFIX}/simple/{run_date}/{strategy_id}.json"
        try:
            s3.put_object(
                Bucket=bucket, Key=key,
                Body=json.dumps(result, indent=2, default=str),
                ContentType="application/json",
            )
            keys[strategy_id] = key
        except Exception:
            log.warning("Failed to save %s to S3", strategy_id, exc_info=True)
    return keys


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        _telegram.send(bot_token, chat_id, message)
        return
    notifier = Notifier.from_secrets()
    notifier.send_error_alert("backtest_summary", message)


def _format_walk_forward_summary(
    run_date: str,
    train_anchor: date,
    test_start: date,
    test_end: date,
    universe: str,
    s3_keys: dict[str, str],
) -> str:
    lines = [
        "<b>Market AI — Walk-Forward Backtest</b>",
        f"Run: {run_date}  |  Universe: {universe}",
        f"Train anchor: {train_anchor}  |  OOS: {test_start} → {test_end}",
        f"Strategies tested: {len(s3_keys)}",
        "",
        "Results saved to S3. Use the leaderboard Lambda for ranked summary.",
    ]
    return "\n".join(lines)


def _format_simple_summary(
    run_date: str,
    start: date,
    end: date,
    universe: str,
    results: dict[str, Any],
) -> str:
    lines = [
        "<b>Market AI — Backtest Summary</b>",
        f"Run date: {run_date}  |  Period: {start} → {end}",
        f"Universe: {universe}  |  Strategies: {len(results)}",
        "",
    ]

    if not results:
        lines.append("No strategies produced results.")
        return "\n".join(lines)

    def _sharpe_key(item: tuple[str, Any]) -> float:
        m = item[1].get("metrics", {})
        v = m.get("sharpe_ratio")
        return float(v) if v is not None else float("-inf")

    for rank, (strategy_id, result) in enumerate(
        sorted(results.items(), key=_sharpe_key, reverse=True), start=1
    ):
        m = result.get("metrics", {})
        if "error" in m:
            lines.append(f"{rank}. <b>{strategy_id}</b> — {m['error']}")
            continue
        lines.append(
            f"{rank}. <b>{strategy_id}</b>\n"
            f"   Sharpe: {m.get('sharpe_ratio', 'N/A')} | "
            f"Win: {m.get('win_rate_pct', 0):.1f}% | "
            f"Return: {m.get('net_return_pct', 0):.1f}% | "
            f"DD: {m.get('max_drawdown_pct', 0):.1f}%\n"
            f"   Trades: {m.get('executed_trades', 0)} | "
            f"PF: {m.get('profit_factor', 'N/A')}"
        )

    lines += [
        "",
        "Research only — historical results do not guarantee future returns.",
    ]
    return "\n".join(lines)
