"""Evening monitor — checks open paper trades against today's candle data.

For each open paper trade:
  1. Fetch today's candle from the provider (S3 only — fill_gaps=False).
  2. If today's low  <= stop_price   → close at stop_price,  reason="stop_hit"
  3. If today's high >= target_price → close at target_price, reason="target_hit"
  4. If (today - entry_date).days >= holding_period_sessions
                                   → close at today's close, reason="time_exit"

Multiple conditions may apply in theory (gap-through); stop takes priority over
target (conservatism), then time_exit.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from app.paper_trading.journal import PaperTradeJournal
    from app.data.providers.hybrid_provider import HybridDataProvider

log = logging.getLogger(__name__)

_DEFAULT_HOLDING_SESSIONS = 10  # close after 10 sessions if no exit was set


def monitor_open_trades(
    journal: "PaperTradeJournal",
    provider: "HybridDataProvider",
    today: date,
) -> dict:
    """Check all open paper trades against today's price data.

    Closes any trade that hit its stop, target, or max holding period.

    Parameters
    ----------
    journal:
        Live ``PaperTradeJournal`` instance backed by DynamoDB.
    provider:
        ``HybridDataProvider`` instance (caller should use ``fill_gaps=False``
        so only already-ingested S3 data is read).
    today:
        The IST date to evaluate against (normally ``date.today()`` in IST).

    Returns
    -------
    dict with keys:
        open_count    – open trades remaining after this run
        closed_today  – number of trades closed in this run
        pnl_today     – cumulative realised P&L for all trades closed today
    """
    from app.models.candle import Timeframe

    open_trades = journal.get_open_trades()
    if not open_trades:
        return {"open_count": 0, "closed_today": 0, "pnl_today": 0.0}

    closed_this_run: list[dict] = []

    for trade in open_trades:
        trade_id: str = trade["SK"]
        symbol: str = trade["symbol"]

        try:
            # Fetch today's single candle (start=today, end=today is acceptable
            # for the provider — it will return 0 or 1 candles).
            candles = provider.get_historical_candles(
                symbol, Timeframe.D1, today, today
            )
            if not candles:
                log.debug(
                    "No candle for %s on %s — skipping monitor for trade %s",
                    symbol, today, trade_id,
                )
                continue

            # Use the last (most recent) candle for the requested date
            candle = candles[-1]

            entry_price: float = float(trade["entry_price"])
            stop_price: float | None = trade.get("stop_price")
            target_price: float | None = trade.get("target_price")
            entry_date_iso: str = trade.get("entry_date", today.isoformat())
            holding_max: int = int(
                trade.get("holding_period_sessions") or _DEFAULT_HOLDING_SESSIONS
            )

            # Compute age in calendar days (conservative; sessions would be better
            # but requires a trading-calendar call for each trade).
            from datetime import date as _date
            entry_date = _date.fromisoformat(entry_date_iso)
            age_days = (today - entry_date).days

            # Determine exit condition — stop takes priority over target
            exit_price: float | None = None
            exit_reason: str | None = None

            if stop_price is not None and candle.low <= stop_price:
                exit_price = stop_price
                exit_reason = "stop_hit"
            elif target_price is not None and candle.high >= target_price:
                exit_price = target_price
                exit_reason = "target_hit"
            elif age_days >= holding_max:
                exit_price = candle.close
                exit_reason = "time_exit"

            if exit_price is not None and exit_reason is not None:
                try:
                    closed = journal.close_trade(trade_id, exit_price, exit_reason)
                    closed_this_run.append(closed)
                    log.info(
                        "Paper trade closed: %s trade_id=%s exit=%.2f reason=%s pnl=%.2f",
                        symbol, trade_id, exit_price, exit_reason,
                        float(closed.get("pnl", 0)),
                    )
                except Exception:
                    log.warning(
                        "Could not close paper trade %s", trade_id, exc_info=True
                    )

        except Exception:
            log.warning(
                "Error monitoring paper trade %s (%s)", trade_id, symbol, exc_info=True
            )

    # Refresh open count from journal after closures
    remaining_open = len(journal.get_open_trades())
    pnl_today = journal.get_pnl_today()

    return {
        "open_count": remaining_open,
        "closed_today": len(closed_this_run),
        "pnl_today": round(pnl_today, 2),
    }
