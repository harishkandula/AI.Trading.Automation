"""Cross-symbol Relative Strength computation for NIFTY 500 universe.

Computes 90-day price return for each symbol and converts it to a percentile
rank (0–100) across the full universe.  Also ranks symbols within each sector.

Usage (called once per scan, not per-signal):
    rs_percentiles = compute_rs_percentiles(symbols, today, provider)
    sector_ranks   = compute_sector_ranks(symbols, rs_percentiles, SYMBOL_TO_SECTOR)
"""
from __future__ import annotations

import logging
from datetime import date, timedelta

log = logging.getLogger(__name__)


def compute_rs_percentiles(
    symbols: list[str],
    as_of: date,
    provider: object,
    lookback_days: int = 90,
) -> dict[str, float]:
    """Compute the 90-day price return percentile for each symbol.

    Parameters
    ----------
    symbols:
        List of bare NSE symbols (no .NS suffix).
    as_of:
        The reference date (today).  Returns for each symbol are computed as
        ``(close_as_of - close_lookback) / close_lookback``.
    provider:
        HybridDataProvider instance (typed as object to avoid circular import).
    lookback_days:
        Number of calendar days to look back for the base price (default 90).

    Returns
    -------
    dict[str, float]
        Symbol → percentile (0.0–100.0), where 100 means the strongest RS.
        Symbols with unavailable data are omitted.
    """
    from app.models.candle import Timeframe  # noqa: PLC0415

    # extra 10-day buffer for weekends/holidays
    lookback_start = as_of - timedelta(days=lookback_days + 10)

    returns: dict[str, float] = {}
    skipped = 0

    for symbol in symbols:
        try:
            candles = provider.get_historical_candles(  # type: ignore[attr-defined]
                symbol, Timeframe.D1, lookback_start, as_of
            )
            if not candles or len(candles) < 5:
                skipped += 1
                continue

            close_today = candles[-1].close

            # Find the candle closest to lookback_days ago
            target_date = as_of - timedelta(days=lookback_days)
            # candles are sorted by timestamp ascending — find the earliest candle
            # on or after target_date to use as the base
            base_candle = None
            for c in candles:
                if c.timestamp.date() >= target_date:
                    base_candle = c
                    break

            if base_candle is None or base_candle.close == 0:
                skipped += 1
                continue

            # Avoid using today's candle as both base and current
            if base_candle is candles[-1]:
                skipped += 1
                continue

            ret = (close_today - base_candle.close) / base_candle.close
            returns[symbol] = ret

        except Exception:
            log.debug("RS compute skipped %s", symbol, exc_info=True)
            skipped += 1

    if skipped:
        log.debug("RS percentile: skipped %d / %d symbols (no data)", skipped, len(symbols))

    if not returns:
        log.warning("RS percentile: no return data available for any symbol")
        return {}

    # Rank by return ascending and convert to percentile
    sorted_symbols = sorted(returns, key=lambda s: returns[s])
    n = len(sorted_symbols)
    percentiles: dict[str, float] = {}
    for rank_0based, sym in enumerate(sorted_symbols):
        # 0-based rank → 0.0–100.0 percentile; highest return = 100.0
        percentiles[sym] = round(rank_0based / (n - 1) * 100.0, 1) if n > 1 else 100.0

    log.info(
        "RS percentiles computed for %d symbols (skipped %d)",
        len(percentiles), skipped,
    )
    return percentiles


def compute_sector_ranks(
    symbols: list[str],
    rs_percentiles: dict[str, float],
    symbol_to_sector: dict[str, str],
) -> dict[str, int]:
    """Rank each symbol within its own sector by RS percentile.

    Parameters
    ----------
    symbols:
        Full universe list (used to group by sector).
    rs_percentiles:
        Output of ``compute_rs_percentiles`` — symbol → percentile (0–100).
    symbol_to_sector:
        Mapping of symbol → sector name (from SYMBOL_TO_SECTOR).

    Returns
    -------
    dict[str, int]
        Symbol → within-sector rank (1 = strongest in sector).
        Symbols with no RS data are omitted.
    """
    # Group symbols by sector, keeping only those with RS data
    sector_buckets: dict[str, list[tuple[str, float]]] = {}
    for sym in symbols:
        if sym not in rs_percentiles:
            continue
        sector = symbol_to_sector.get(sym, "UNKNOWN")
        sector_buckets.setdefault(sector, []).append((sym, rs_percentiles[sym]))

    sector_ranks: dict[str, int] = {}
    for sector, members in sector_buckets.items():
        # Sort descending by RS percentile — rank 1 = highest percentile
        members_sorted = sorted(members, key=lambda x: x[1], reverse=True)
        for rank_1based, (sym, _) in enumerate(members_sorted, start=1):
            sector_ranks[sym] = rank_1based

    return sector_ranks
