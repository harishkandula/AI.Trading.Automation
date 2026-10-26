"""Strategy leaderboard Lambda handler.

Triggered by EventBridge (Sunday 12:30 IST weekly — after backtest completes).
Reads the latest walk-forward result per strategy from DynamoDB, ranks by
Sharpe ratio, and sends a formatted Telegram summary with validated badges.

Event payload: {} (no parameters needed — always uses latest from DynamoDB)
"""
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from app.backtesting.leaderboard import rank_leaderboard, read_latest_per_strategy
from app.notifications import telegram as _telegram
from app.notifications.notifier import Notifier

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the weekly leaderboard summary."""
    today = datetime.now(tz=IST).date().isoformat()

    try:
        return _run(today)
    except Exception as exc:
        log.exception("Leaderboard handler fatal error")
        try:
            notifier = Notifier.from_secrets()
            notifier.send_error_alert("leaderboard_handler", str(exc))
        except Exception:
            log.exception("Failed to send error alert")
        return {"statusCode": 500, "body": f"Leaderboard failed: {exc}"}


def _run(today: str) -> dict:
    entries = read_latest_per_strategy()

    if not entries:
        log.warning("No leaderboard entries found in DynamoDB")
        _send_telegram(f"<b>Market AI Leaderboard — {today}</b>\n\nNo results yet.")
        return {"statusCode": 200, "body": {"run_date": today, "strategies": 0}}

    ranked = rank_leaderboard(entries)
    msg = _format_leaderboard(today, ranked)
    _send_telegram(msg)

    log.info("Leaderboard sent: %d strategies ranked", len(ranked))
    return {
        "statusCode": 200,
        "body": {
            "run_date": today,
            "strategies": len(ranked),
            "top_strategy": ranked[0].get("pk") if ranked else None,
        },
    }


def _format_leaderboard(run_date: str, ranked: list[dict[str, Any]]) -> str:
    lines = [
        "<b>Market AI — Strategy Leaderboard</b>",
        f"Updated: {run_date}",
        f"Strategies tracked: {len(ranked)}",
        "",
    ]

    for rank, entry in enumerate(ranked, start=1):
        sid = entry.get("pk", "?")
        ver = entry.get("strategy_version", "")
        validated = entry.get("validated")
        badge = "✅" if validated else "❌"

        sharpe = entry.get("avg_sharpe")
        sharpe_str = f"{sharpe:.2f}" if isinstance(sharpe, float) else "N/A"

        consistency = entry.get("consistency_score")
        consistency_str = (
            f"{consistency * 100:.0f}%" if isinstance(consistency, float) else "N/A"
        )

        ret = entry.get("avg_net_return_pct")
        ret_str = f"{ret:.1f}%" if isinstance(ret, float) else "N/A"

        dd = entry.get("avg_max_drawdown_pct")
        dd_str = f"{dd:.1f}%" if isinstance(dd, float) else "N/A"

        win = entry.get("avg_win_rate_pct")
        win_str = f"{win:.1f}%" if isinstance(win, float) else "N/A"

        windows = entry.get("windows_count", "?")
        trades = entry.get("total_oos_trades", "?")
        run_on = entry.get("sk", "?")

        lines.append(
            f"{rank}. {badge} <b>{sid}</b> {ver}\n"
            f"   Sharpe: {sharpe_str} | Consistency: {consistency_str} | "
            f"Avg return: {ret_str}\n"
            f"   Avg DD: {dd_str} | Win rate: {win_str} | "
            f"OOS trades: {trades} ({windows} windows)\n"
            f"   Last run: {run_on}"
        )

    lines += [
        "",
        "✅ = validated (consistency ≥ 60%, avg Sharpe > 0.5)",
        "Research only — not financial advice.",
    ]
    return "\n".join(lines)


def _send_telegram(message: str) -> None:
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        _telegram.send(bot_token, chat_id, message)
        return
    notifier = Notifier.from_secrets()
    notifier.send_error_alert("leaderboard_summary", message)
