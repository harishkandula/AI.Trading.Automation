"""
Backtest Lambda handler.

Triggered by EventBridge (Sunday 11:00 IST weekly).
Runs all registered daily strategies over the NIFTY 50 universe, saves per-strategy
results to S3, and sends a ranked Telegram summary.

Event payload (all keys optional):
    {
        "start":    "2023-01-01",   # ISO date; default 2 years ago
        "end":      "2025-12-31",   # ISO date; default today
        "universe": "nifty50",      # get_universe() key; default "nifty50"
        "capital":  100000.0        # initial capital per strategy; default 100 000
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

from app.backtesting.runner import run_universe_backtest
from app.data.universe import get_universe
from app.notifications import telegram as _telegram
from app.notifications.notifier import Notifier
from app.strategies.registry import all_ids

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_DEFAULT_LOOKBACK_YEARS = 2
_S3_PREFIX = "backtests"


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

    # --- Resolve parameters from event with defaults ---
    end = date.fromisoformat(evt["end"]) if "end" in evt else today
    start = (
        date.fromisoformat(evt["start"])
        if "start" in evt
        else end - timedelta(days=365 * _DEFAULT_LOOKBACK_YEARS)
    )
    universe_name: str = evt.get("universe", "nifty50")
    capital: float = float(evt.get("capital", 100_000.0))

    symbols = get_universe(universe_name)
    strategy_ids = all_ids()

    log.info(
        "Backtest: universe=%s (%d symbols) start=%s end=%s capital=%.0f",
        universe_name, len(symbols), start, end, capital,
    )

    results = run_universe_backtest(
        symbols=symbols,
        strategy_ids=strategy_ids,
        start=start,
        end=end,
        initial_capital=capital,
    )

    # --- Save results to S3 ---
    bucket = _data_bucket()
    run_date = today.isoformat()
    s3_keys: dict[str, str] = {}
    if bucket:
        s3_keys = _save_to_s3(bucket, run_date, results)
    else:
        log.warning("DATA_BUCKET not set — skipping S3 upload")

    # --- Send Telegram summary ---
    try:
        msg = _format_summary(run_date, start, end, universe_name, results)
        _send_telegram(msg)
    except Exception:
        log.exception("Failed to send Telegram backtest summary")

    return {
        "statusCode": 200,
        "body": {
            "run_date": run_date,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "universe": universe_name,
            "strategies_run": list(results.keys()),
            "s3_keys": s3_keys,
        },
    }


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _data_bucket() -> str:
    return os.environ.get("DATA_BUCKET", "")


def _save_to_s3(bucket: str, run_date: str, results: dict[str, Any]) -> dict[str, str]:
    """Persist each strategy result as a separate JSON file in S3."""
    s3 = boto3.client("s3")
    keys: dict[str, str] = {}
    for strategy_id, result in results.items():
        key = f"{_S3_PREFIX}/{run_date}/{strategy_id}.json"
        body = json.dumps(result, indent=2, default=str)
        try:
            s3.put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
            keys[strategy_id] = key
            log.info("Saved %s → s3://%s/%s", strategy_id, bucket, key)
        except Exception:
            log.warning("Failed to save %s to S3", strategy_id, exc_info=True)
    return keys


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------


def _send_telegram(message: str) -> None:
    """Send *message* via Telegram, preferring env-var credentials over Secrets Manager."""
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        _telegram.send(bot_token, chat_id, message)
        return
    # Fall back to Secrets Manager
    notifier = Notifier.from_secrets()
    notifier.send_error_alert("backtest_summary", message)


# ---------------------------------------------------------------------------
# Telegram summary formatting
# ---------------------------------------------------------------------------

def _format_summary(
    run_date: str,
    start: date,
    end: date,
    universe: str,
    results: dict[str, Any],
) -> str:
    """Build a ranked Telegram HTML summary of backtest results."""
    lines = [
        "<b>Market AI — Weekly Backtest Summary</b>",
        f"Run date: {run_date}  |  Period: {start} → {end}",
        f"Universe: {universe}  |  Strategies: {len(results)}",
        "",
    ]

    if not results:
        lines.append("No strategies produced results.")
        return "\n".join(lines)

    # Rank by Sharpe ratio (None treated as -inf)
    def _sharpe_key(item: tuple[str, Any]) -> float:
        m = item[1].get("metrics", {})
        v = m.get("sharpe_ratio")
        return float(v) if v is not None else float("-inf")

    ranked = sorted(results.items(), key=_sharpe_key, reverse=True)

    for rank, (strategy_id, result) in enumerate(ranked, start=1):
        m = result.get("metrics", {})
        if "error" in m:
            lines.append(f"{rank}. <b>{strategy_id}</b> — {m['error']}")
            continue
        sharpe = m.get("sharpe_ratio", "N/A")
        win_rate = m.get("win_rate_pct", 0)
        net_ret = m.get("net_return_pct", 0)
        max_dd = m.get("max_drawdown_pct", 0)
        executed = m.get("executed_trades", 0)
        pf = m.get("profit_factor", "N/A")
        lines.append(
            f"{rank}. <b>{strategy_id}</b>\n"
            f"   Sharpe: {sharpe} | Win: {win_rate:.1f}% | "
            f"Return: {net_ret:.1f}% | DD: {max_dd:.1f}%\n"
            f"   Trades: {executed} | PF: {pf}"
        )

    lines += [
        "",
        "⚠️ RESEARCH ONLY — backtests use historical data and do not guarantee future returns.",
    ]
    return "\n".join(lines)
