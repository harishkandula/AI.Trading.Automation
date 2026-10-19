"""Evening Lambda handler — post-market signal scan + portfolio review."""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from app.brokers.groww_broker import GrowwBroker
from app.data.providers.yfinance_provider import YFinanceProvider
from app.data.universe import get_universe
from app.models.candle import Timeframe
from app.notifications.notifier import Notifier

# Side-effect imports to trigger @register decorators on all strategy classes
import app.strategies.gap_up_volume  # noqa: F401
import app.strategies.intraday_support_supertrend  # noqa: F401
import app.strategies.morning_star  # noqa: F401
import app.strategies.rsi_supertrend_volume  # noqa: F401

from app.strategies.registry import all_strategies

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_WARMUP_DAYS = 100


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the evening scan (19:00 IST Mon-Fri)."""
    if isinstance(event, dict) and event.get("_prewarm"):
        log.info("Pre-warm ping received — container ready")
        return {"statusCode": 200, "body": "warm"}

    today = datetime.now(tz=IST).date()

    try:
        return _run_scan(today)
    except Exception as exc:
        log.exception("Evening handler fatal error")
        try:
            notifier = Notifier.from_secrets()
            notifier.send_error_alert("evening_handler", str(exc))
        except Exception:
            log.exception("Failed to send error alert")
        return {
            "statusCode": 500,
            "body": f"Evening scan failed: {exc}",
        }


def _run_scan(today: date) -> dict:
    provider = YFinanceProvider()
    symbols = get_universe("nifty50")
    strategies = all_strategies()

    start = today - timedelta(days=_WARMUP_DAYS)
    end = today

    all_signals = []
    scanned = 0
    errors = 0

    for symbol in symbols:
        try:
            candles = provider.get_historical_candles(symbol, Timeframe.D1, start, end)
            if not candles:
                log.warning("No candles for %s", symbol)
                continue

            scanned += 1
            for _sid, strategy_cls in strategies.items():
                strategy = strategy_cls()
                try:
                    signals = strategy.generate_signals(symbol, candles)
                    for sig in signals:
                        if sig.signal_timestamp.date() == today:
                            all_signals.append(sig)
                except Exception:
                    log.warning(
                        "Strategy %s failed on %s", _sid, symbol, exc_info=True
                    )
                    errors += 1

        except Exception:
            log.warning("Failed to process %s", symbol, exc_info=True)
            errors += 1

    # Attempt to read Groww holdings (non-fatal)
    open_positions = 0
    try:
        broker = GrowwBroker()
        holdings = broker.get_holdings()
        open_positions = len([h for h in holdings if h.get("quantity", 0) > 0])
        log.info("Groww holdings fetched: %d open positions", open_positions)
    except Exception:
        log.warning("Could not fetch Groww holdings (Groww may not be configured)")

    try:
        notifier = Notifier.from_secrets()
        if all_signals:
            notifier.send_signal_alert(all_signals)
        notifier.send_daily_summary(
            scan_date=today,
            universe_size=len(symbols),
            scanned=scanned,
            signals=all_signals,
            errors=errors,
        )
    except Exception:
        log.exception("Failed to send Telegram notifications")

    return {
        "statusCode": 200,
        "body": {
            "scan_date": today.isoformat(),
            "universe_size": len(symbols),
            "scanned": scanned,
            "signals": len(all_signals),
            "open_positions": open_positions,
            "errors": errors,
        },
    }
