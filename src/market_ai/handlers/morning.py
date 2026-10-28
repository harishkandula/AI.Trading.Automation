"""Morning Lambda handler — NIFTY 50 swing signal scan."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_WARMUP_DAYS = 100


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the morning scan (07:00 IST Mon-Fri).

    All heavy imports are deferred inside this function so they execute during
    the INVOKE phase, not INIT. Lambda's INIT phase has a hard 10 s limit; pandas
    + numpy + yfinance together take ~9 s to import on a cold container.
    """
    # --- deferred imports (keep INIT phase < 1 s) ---
    from app.data.providers.hybrid_provider import HybridDataProvider  # noqa: PLC0415
    from app.data.universe import get_universe  # noqa: PLC0415
    from app.models.candle import Timeframe  # noqa: PLC0415
    from app.notifications.notifier import Notifier  # noqa: PLC0415
    import app.strategies.gap_up_volume  # noqa: F401, PLC0415
    import app.strategies.intraday_support_supertrend  # noqa: F401, PLC0415
    import app.strategies.morning_star  # noqa: F401, PLC0415
    import app.strategies.rsi_supertrend_volume  # noqa: F401, PLC0415
    import app.strategies.s00_benchmark  # noqa: F401, PLC0415
    import app.strategies.s01_rs_momentum  # noqa: F401, PLC0415
    import app.strategies.s02_vcp  # noqa: F401, PLC0415
    import app.strategies.s03_trend_pullback  # noqa: F401, PLC0415
    from app.strategies.registry import all_strategies  # noqa: PLC0415
    from app.research.scoring import StrategyFamily, score_signal  # noqa: PLC0415
    from app.research.regime import MarketRegime  # noqa: PLC0415
    from app.risk.engine import RiskConfig, RiskGuard, PortfolioSnapshot  # noqa: PLC0415
    from app.data.nifty500_symbols import SYMBOL_TO_SECTOR, SYMBOL_TO_SEGMENT  # noqa: PLC0415

    if isinstance(event, dict) and event.get("_prewarm"):
        log.info("Pre-warm ping received — container ready")
        return {"statusCode": 200, "body": "warm"}

    today = datetime.now(tz=IST).date()
    start = today - timedelta(days=_WARMUP_DAYS)

    import os  # noqa: PLC0415
    # fill_gaps=False: reads S3 only — data pipeline already ran before market open.
    # Avoids 500+ Groww/yfinance calls that cause 120s timeout.
    provider = HybridDataProvider(fill_gaps=False)
    universe_name = os.environ.get("UNIVERSE", "nifty500")
    symbols = get_universe(universe_name)
    strategies = all_strategies()

    all_signals = []
    scanned = 0
    errors = 0

    for symbol in symbols:
        try:
            candles = provider.get_historical_candles(symbol, Timeframe.D1, start, today)
            if not candles:
                log.warning("No candles for %s", symbol)
                continue

            scanned += 1
            for _sid, strategy_cls in strategies.items():
                strategy = strategy_cls()
                if len(candles) < strategy.warmup_bars + 5:
                    log.debug(
                        "Skipping %s on %s — too few bars (%d < %d)",
                        _sid, symbol, len(candles), strategy.warmup_bars + 5,
                    )
                    continue
                try:
                    signals = strategy.generate_signals(symbol, candles)
                    for sig in signals:
                        # planned_entry_timestamp is next-session open (today).
                        # signal_timestamp is the candle that triggered it (yesterday).
                        entry_date = (
                            sig.planned_entry_timestamp.date()
                            if sig.planned_entry_timestamp else sig.signal_timestamp.date()
                        )
                        if entry_date == today:
                            all_signals.append(sig)
                except Exception:
                    log.warning("Strategy %s failed on %s", _sid, symbol, exc_info=True)
                    errors += 1

        except Exception:
            log.warning("Failed to process %s", symbol, exc_info=True)
            errors += 1

    # Score and rank signals (best-effort — missing RS data gets neutral score)
    _FAMILY_MAP = {
        "s00_benchmark": StrategyFamily.MOMENTUM,
        "s01_rs_momentum": StrategyFamily.MOMENTUM,
        "s02_vcp": StrategyFamily.BREAKOUT,
        "s03_trend_pullback": StrategyFamily.PULLBACK,
        "rsi_supertrend_volume": StrategyFamily.MOMENTUM,
        "gap_up_volume": StrategyFamily.BREAKOUT,
        "morning_star": StrategyFamily.PULLBACK,
        "intraday_support_supertrend": StrategyFamily.MOMENTUM,
    }
    # Detect multi-family confluence per symbol
    families_by_symbol: dict[str, list[str]] = {}
    for sig in all_signals:
        fam = _FAMILY_MAP.get(sig.strategy_id, StrategyFamily.MOMENTUM).value
        families_by_symbol.setdefault(sig.symbol, []).append(fam)

    scored_signals = []
    for sig in all_signals:
        try:
            fam = _FAMILY_MAP.get(sig.strategy_id, StrategyFamily.MOMENTUM)
            rel_vol = sig.indicators.get("rel_vol", 1.0)
            score = score_signal(
                symbol=sig.symbol,
                strategy_id=sig.strategy_id,
                family=fam,
                regime=MarketRegime.UNKNOWN,   # regime computed separately; UNKNOWN = neutral
                rs_percentile=None,
                sector_rank=None,
                relative_volume=float(rel_vol),
                families_triggered=families_by_symbol.get(sig.symbol, []),
            )
            sig.indicators["score"] = score.total
            sig.indicators["score_breakdown"] = {
                "regime": score.regime_score,
                "rs": score.rs_score,
                "sector": score.sector_score,
                "setup": score.setup_score,
                "confluence": score.confluence_bonus,
            }
        except Exception:
            log.debug("Scoring failed for %s", sig.symbol, exc_info=True)
        scored_signals.append(sig)

    # Sort by score descending
    scored_signals.sort(key=lambda s: s.indicators.get("score", 0), reverse=True)

    # Apply portfolio risk guards
    portfolio = PortfolioSnapshot(equity=float(os.environ.get("PORTFOLIO_EQUITY", "500000")))
    guard = RiskGuard(RiskConfig(), portfolio)
    approved_signals = []
    rejected_by_risk = 0
    for sig in scored_signals:
        sector = SYMBOL_TO_SECTOR.get(sig.symbol, "UNKNOWN")
        segment = SYMBOL_TO_SEGMENT.get(sig.symbol, "UNKNOWN")
        decision = guard.check(sig, sector=sector, segment=segment)
        if decision.approved:
            approved_signals.append(sig)
        else:
            rejected_by_risk += 1
            log.info("Risk rejected %s: %s", sig.symbol, decision.reason)
    scored_signals = approved_signals

    try:
        notifier = Notifier.from_secrets()
        if scored_signals:
            notifier.send_signal_alert(scored_signals)
        notifier.send_daily_summary(
            scan_date=today,
            universe_size=len(symbols),
            scanned=scanned,
            signals=scored_signals,
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
            "signals": len(scored_signals),
            "errors": errors,
        },
    }
