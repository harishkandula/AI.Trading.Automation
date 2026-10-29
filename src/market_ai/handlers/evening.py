"""Evening Lambda handler — post-market signal scan + portfolio review."""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")
_WARMUP_DAYS = 400


def _check_data_freshness(today) -> str | None:
    import os
    from app.data.store import ParquetStore
    bucket = os.environ.get("DATA_BUCKET", "")
    if not bucket:
        return None
    store = ParquetStore(bucket)
    probe_symbols = ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]
    latest_dates = []
    for sym in probe_symbols:
        d = store.latest_date(sym)
        if d:
            latest_dates.append(d)
    if not latest_dates:
        return "WARNING: No S3 data found for probe symbols — data pipeline may have never run."
    most_recent = max(latest_dates)
    from app.core.time_utils import prev_trading_day
    expected = prev_trading_day(today)
    lag_days = (expected - most_recent).days
    if lag_days > 1:
        return (
            f"WARNING: S3 data is stale — latest {most_recent}, expected {expected} "
            f"({lag_days} trading days behind). Signals may be based on old prices."
        )
    return None


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the evening scan (19:00 IST Mon-Fri).

    All heavy imports are deferred inside this function so they execute during
    the INVOKE phase, not INIT. Lambda's INIT phase has a hard 10 s limit; pandas
    + numpy + yfinance together take ~9 s to import on a cold container.
    """
    # --- deferred imports (keep INIT phase < 1 s) ---
    from app.brokers.groww_broker import GrowwBroker  # noqa: PLC0415
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
    from app.research.regime import MarketRegime, compute_regime  # noqa: PLC0415
    from app.research.rs_compute import compute_rs_percentiles, compute_sector_ranks  # noqa: PLC0415
    from app.data.nifty500_symbols import SYMBOL_TO_SECTOR, SYMBOL_TO_SEGMENT  # noqa: PLC0415

    if isinstance(event, dict) and event.get("_prewarm"):
        log.info("Pre-warm ping received — container ready")
        return {"statusCode": 200, "body": "warm"}

    today = datetime.now(tz=IST).date()

    freshness_warning = _check_data_freshness(today)
    if freshness_warning:
        log.warning(freshness_warning)
        try:
            notifier = Notifier.from_secrets()
            notifier.send_error_alert("data_freshness", freshness_warning)
        except Exception:
            log.debug("Could not send freshness warning via notifier", exc_info=True)

    start = today - timedelta(days=_WARMUP_DAYS)

    import os  # noqa: PLC0415
    # fill_gaps=False: reads S3 only — data pipeline already ran before market close.
    provider = HybridDataProvider(fill_gaps=False)
    universe_name = os.environ.get("UNIVERSE", "nifty500")
    symbols = get_universe(universe_name)
    strategies = all_strategies()

    # ── Compute market regime (best-effort) ──────────────────────────────────
    regime = MarketRegime.UNKNOWN
    try:
        regime_snapshot = compute_regime(
            store=provider._store,  # type: ignore[attr-defined]
            symbols=symbols,
            as_of=today,
        )
        regime = regime_snapshot.regime
        log.info(
            "Market regime: %s (ema50=%.1f%%, ema200=%.1f%%, new_highs=%.1f%%)",
            regime.value,
            regime_snapshot.pct_above_ema50,
            regime_snapshot.pct_above_ema200,
            regime_snapshot.new_highs_pct,
        )
    except Exception:
        log.warning("Regime computation failed — falling back to UNKNOWN", exc_info=True)

    # ── Compute RS percentiles across the full universe (best-effort) ────────
    rs_percentiles: dict[str, float] = {}
    sector_ranks: dict[str, int] = {}
    try:
        rs_percentiles = compute_rs_percentiles(symbols, today, provider)
        sector_ranks = compute_sector_ranks(symbols, rs_percentiles, SYMBOL_TO_SECTOR)
        log.info(
            "RS percentiles computed for %d symbols; sector ranks for %d symbols",
            len(rs_percentiles), len(sector_ranks),
        )
    except Exception:
        log.warning(
            "RS percentile computation failed — rs_percentile/sector_rank will be None",
            exc_info=True,
        )

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
                if strategy.timeframe == Timeframe.M5:
                    log.debug("Skipping intraday strategy %s in daily scan", _sid)
                    continue
                if len(candles) < strategy.warmup_bars + 5:
                    log.debug(
                        "Skipping %s on %s — too few bars (%d < %d)",
                        _sid, symbol, len(candles), strategy.warmup_bars + 5,
                    )
                    continue
                try:
                    signals = strategy.generate_signals(symbol, candles)
                    for sig in signals:
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
                regime=regime,
                rs_percentile=rs_percentiles.get(sig.symbol),
                sector_rank=sector_ranks.get(sig.symbol),
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
    all_signals = scored_signals

    # Attempt to read Groww holdings (non-fatal)
    open_positions = 0
    try:
        broker = GrowwBroker()
        holdings = broker.get_holdings()
        open_positions = len([h for h in holdings if h.get("quantity", 0) > 0])
        log.info("Groww holdings fetched: %d open positions", open_positions)
    except Exception:
        log.warning("Could not fetch Groww holdings (Groww may not be configured)")

    # --- Paper trade monitor: close any stops/targets/time-exits ---
    from app.paper_trading.journal import PaperTradeJournal  # noqa: PLC0415
    from app.paper_trading.monitor import monitor_open_trades  # noqa: PLC0415

    monitor_result: dict = {}
    try:
        journal = PaperTradeJournal()
        monitor_result = monitor_open_trades(journal, provider, today)
        log.info("Paper trade monitor: %s", monitor_result)
    except Exception:
        log.warning("Paper trade monitor failed (non-fatal)", exc_info=True)

    try:
        notifier = Notifier.from_secrets()
        if all_signals:
            notifier.send_signal_alert(all_signals)

        # Build paper trade summary line for Telegram (only when non-zero activity)
        paper_summary = ""
        if monitor_result:
            closed_today = monitor_result.get("closed_today", 0)
            pnl_today = monitor_result.get("pnl_today", 0.0)
            open_count = monitor_result.get("open_count", 0)
            if closed_today or open_count:
                sign = "+" if pnl_today >= 0 else ""
                paper_summary = (
                    f"\n\n<b>Paper Trades</b>\n"
                    f"  Open: {open_count}  |  Closed today: {closed_today}"
                    f"  |  P&amp;L today: {sign}{pnl_today:,.2f}"
                )

        notifier.send_daily_summary(
            scan_date=today,
            universe_size=len(symbols),
            scanned=scanned,
            signals=all_signals,
            errors=errors,
        )
        if paper_summary:
            try:
                from app.notifications import telegram  # noqa: PLC0415
                telegram.send(notifier._bot_token, notifier._chat_id, paper_summary)
            except Exception:
                log.debug("Could not send paper trade Telegram summary", exc_info=True)
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
            "paper_open": monitor_result.get("open_count", 0),
            "paper_closed_today": monitor_result.get("closed_today", 0),
            "paper_pnl_today": monitor_result.get("pnl_today", 0.0),
            "errors": errors,
        },
    }
