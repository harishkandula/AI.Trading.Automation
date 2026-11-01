"""Morning Lambda handler — NIFTY 50 swing signal scan."""
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


def _persist_regime(regime_snapshot, today) -> None:
    """Write regime to STATE_TABLE so dashboard and weekly handler can read it."""
    import os
    import boto3
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name:
        return
    try:
        table = boto3.resource("dynamodb").Table(table_name)
        table.put_item(Item={
            "pk": "REGIME",
            "sk": "current",
            "regime": regime_snapshot.regime.value,
            "as_of": today.isoformat(),
            "pct_above_ema50": str(round(regime_snapshot.pct_above_ema50, 1)),
            "pct_above_ema200": str(round(regime_snapshot.pct_above_ema200, 1)),
            "new_highs_pct": str(round(regime_snapshot.new_highs_pct, 1)),
        })
        log.info("Regime persisted to DynamoDB: %s", regime_snapshot.regime.value)
    except Exception:
        log.warning("Could not persist regime to DynamoDB", exc_info=True)


def _persist_signals(signals, today) -> None:
    """Write approved signals to STATE_TABLE so dashboard Live Signals page has data."""
    import os
    import boto3
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name or not signals:
        return
    try:
        table = boto3.resource("dynamodb").Table(table_name)
        from decimal import Decimal
        date_str = today.isoformat()
        with table.batch_writer() as batch:
            for sig in signals:
                entry_ref = sig.entry_reference_price
                item = {
                    "pk": f"SIGNAL_{date_str}",
                    "sk": f"{sig.symbol}_{sig.strategy_id}",
                    "symbol": sig.symbol,
                    "strategy_id": sig.strategy_id,
                    "signal_date": date_str,
                    "entry_price": (
                        Decimal(str(round(entry_ref, 2))) if entry_ref else None
                    ),
                    "stop_price": (
                        Decimal(str(round(sig.stop_price, 2))) if sig.stop_price else None
                    ),
                    "score": Decimal(str(sig.indicators.get("score", 0))),
                    "status": (
                        sig.status.value if hasattr(sig.status, "value") else str(sig.status)
                    ),
                }
                # strip None values — DynamoDB rejects them
                batch.put_item(Item={k: v for k, v in item.items() if v is not None})
        log.info("Persisted %d signals to DynamoDB for %s", len(signals), date_str)
    except Exception:
        log.warning("Could not persist signals to DynamoDB", exc_info=True)


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """
    AWS Lambda entry-point for the morning scan (07:00 IST Mon-Fri).

    All heavy imports are deferred inside this function so they execute during
    the INVOKE phase, not INIT. Lambda's INIT phase has a hard 10 s limit;
    pandas + numpy + yfinance together take ~9 s to import on a cold container.
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
    from app.research.regime import MarketRegime, compute_regime  # noqa: PLC0415
    from app.research.rs_compute import (  # noqa: PLC0415
        compute_rs_percentiles,
        compute_sector_ranks,
    )
    from app.risk.engine import (  # noqa: PLC0415
        RiskConfig,
        RiskGuard,
        build_portfolio_snapshot,
    )
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
    provider = HybridDataProvider(fill_gaps=False)
    universe_name = os.environ.get("UNIVERSE", "nifty500")
    symbols = get_universe(universe_name)
    strategies = all_strategies()

    # ── Compute market regime (best-effort) ──────────────────────────────────
    regime = MarketRegime.UNKNOWN
    regime_snapshot = None
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
        _persist_regime(regime_snapshot, today)
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

    # Score and rank signals
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

    scored_signals.sort(key=lambda s: s.indicators.get("score", 0), reverse=True)

    # --- Paper trading: read real P&L and open positions before risk guard ---
    from app.paper_trading.journal import PaperTradeJournal  # noqa: PLC0415
    from app.paper_trading.sizer import compute_qty  # noqa: PLC0415

    journal = PaperTradeJournal()
    pnl_today = 0.0
    pnl_week = 0.0
    open_trades_list = []
    try:
        pnl_today = journal.get_pnl_today()
        pnl_week = journal.get_pnl_week()
        open_trades_list = journal.get_open_trades()
        log.info(
            "Paper P&L — today: %.2f  week: %.2f  open: %d",
            pnl_today, pnl_week, len(open_trades_list),
        )
    except Exception:
        log.warning(
            "Could not read paper P&L/positions — using 0 (RiskGuard limits inactive)",
            exc_info=True,
        )

    from app.core.ssm_config import get_portfolio_equity, get_risk_config_from_ssm  # noqa: PLC0415
    portfolio_equity = get_portfolio_equity()

    # Build snapshot with real open positions so sector/segment caps apply
    portfolio = build_portfolio_snapshot(
        open_trades=open_trades_list,
        symbol_to_sector=SYMBOL_TO_SECTOR,
        symbol_to_segment=SYMBOL_TO_SEGMENT,
        current_prices={},
        equity=portfolio_equity,
        realised_pnl_today=pnl_today,
        realised_pnl_this_week=pnl_week,
    )
    ssm_overrides = get_risk_config_from_ssm()
    risk_cfg = RiskConfig(**ssm_overrides) if ssm_overrides else RiskConfig()
    guard = RiskGuard(risk_cfg, portfolio)
    approved_signals = []
    rejected_by_risk = 0
    for sig in scored_signals:
        sector = SYMBOL_TO_SECTOR.get(sig.symbol, "UNKNOWN")
        segment = SYMBOL_TO_SEGMENT.get(sig.symbol, "UNKNOWN")
        decision = guard.check(sig, sector=sector, segment=segment, as_of=today)
        if decision.approved:
            approved_signals.append(sig)
        else:
            rejected_by_risk += 1
            log.info("Risk rejected %s: %s", sig.symbol, decision.reason)
    scored_signals = approved_signals

    # Persist approved signals to DynamoDB for the dashboard
    _persist_signals(scored_signals, today)

    # --- Open paper trades for every approved signal ---
    for sig in scored_signals:
        try:
            if sig.entry_reference_price and sig.stop_price:
                qty = compute_qty(
                    entry_price=sig.entry_reference_price,
                    stop_price=sig.stop_price,
                    portfolio_equity=portfolio_equity,
                )
                trade_id = journal.open_trade(
                    sig,
                    entry_price=sig.entry_reference_price,
                    qty=qty,
                )
                log.info(
                    "Paper trade opened: %s qty=%d entry=%.2f stop=%.2f target=%s trade_id=%s",
                    sig.symbol,
                    qty,
                    sig.entry_reference_price,
                    sig.stop_price,
                    f"{sig.target_prices[0]:.2f}" if sig.target_prices else "N/A",
                    trade_id,
                )
            else:
                log.debug(
                    "Skipping paper trade for %s — missing entry/stop price", sig.symbol
                )
        except Exception:
            log.warning("Could not open paper trade for %s", sig.symbol, exc_info=True)

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
