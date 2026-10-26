"""Relative Strength Engine.

Computes RS metrics for every symbol vs the NIFTY 500 index and vs its sector.
All values are cached; strategies read from cache, never recompute.

RS definition: (stock return over N days) / (index return over N days)
  > 1.0 means stock outperformed the index over that period
  > 0.0 means stock had positive return (even if index was flat/negative)

Percentile rank: where the stock sits vs all 500 peers (0=worst, 100=best).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

log = logging.getLogger(__name__)


@dataclass
class RSSnapshot:
    symbol: str
    date: date
    rs_20d: float | None       # stock/index 20-day return ratio
    rs_60d: float | None
    rs_120d: float | None
    rs_252d: float | None
    rs_vs_sector_20d: float | None
    rs_vs_sector_60d: float | None
    percentile_vs_universe: float | None   # 0–100
    percentile_vs_sector: float | None     # 0–100


def _return_over(closes: list[float], n: int) -> float | None:
    """Return (close[-1] / close[-n-1]) - 1, or None if not enough data."""
    if len(closes) < n + 1:
        return None
    base = closes[-(n + 1)]
    if base <= 0:
        return None
    return (closes[-1] / base) - 1.0


def compute_rs_for_symbol(
    symbol_closes: list[float],
    index_closes: list[float],
    sector_closes: list[float] | None = None,
) -> dict[str, float | None]:
    """Compute raw RS values (not percentile ranked)."""
    results: dict[str, float | None] = {}
    for n, key in ((20, "rs_20d"), (60, "rs_60d"), (120, "rs_120d"), (252, "rs_252d")):
        s_ret = _return_over(symbol_closes, n)
        i_ret = _return_over(index_closes, n)
        if s_ret is None or i_ret is None:
            results[key] = None
        elif i_ret == -1.0:
            results[key] = None
        else:
            results[key] = (1 + s_ret) / (1 + i_ret) - 1.0

    if sector_closes:
        for n, key in ((20, "rs_vs_sector_20d"), (60, "rs_vs_sector_60d")):
            s_ret = _return_over(symbol_closes, n)
            sec_ret = _return_over(sector_closes, n)
            if s_ret is None or sec_ret is None:
                results[key] = None
            elif sec_ret == -1.0:
                results[key] = None
            else:
                results[key] = (1 + s_ret) / (1 + sec_ret) - 1.0
    else:
        results["rs_vs_sector_20d"] = None
        results["rs_vs_sector_60d"] = None

    return results


def compute_universe_rs(
    store: object,
    symbols: list[str],
    symbol_to_sector: dict[str, str],
    as_of: date,
    index_symbol: str = "NIFTY500",
) -> dict[str, RSSnapshot]:
    """Compute RS snapshots for all symbols, including universe percentile ranks.

    Parameters
    ----------
    store : ParquetStore
    symbols : list of bare NSE symbols
    symbol_to_sector : mapping symbol → sector name
    as_of : date to compute for
    index_symbol : the NIFTY500 index symbol stored in parquet
    """
    lookback = as_of - timedelta(days=300)

    # Load index closes
    idx_df = store.read_ohlcv_range(index_symbol, lookback, as_of)  # type: ignore[attr-defined]
    index_closes = idx_df["close"].tolist() if not idx_df.empty else []

    # Build sector close series
    sector_closes_map: dict[str, list[float]] = {}
    sectors_seen: set[str] = set(symbol_to_sector.values())
    for sector in sectors_seen:
        sector_syms = [s for s, sec in symbol_to_sector.items() if sec == sector]
        if not sector_syms:
            continue
        frames = []
        for sym in sector_syms[:20]:  # cap at 20 per sector for speed
            df = store.read_ohlcv_range(sym, lookback, as_of)  # type: ignore[attr-defined]
            if not df.empty:
                frames.append(df.set_index("date")["close"])
        if frames:
            import pandas as pd  # noqa: PLC0415
            sector_avg = pd.concat(frames, axis=1).mean(axis=1).dropna()
            sector_closes_map[sector] = sector_avg.tolist()

    # Compute raw RS for every symbol
    raw: dict[str, dict[str, float | None]] = {}
    for symbol in symbols:
        try:
            df = store.read_ohlcv_range(symbol, lookback, as_of)  # type: ignore[attr-defined]
            if df.empty:
                continue
            sym_closes = df["close"].tolist()
            sector = symbol_to_sector.get(symbol)
            sec_closes = sector_closes_map.get(sector) if sector else None
            raw[symbol] = compute_rs_for_symbol(sym_closes, index_closes, sec_closes)
        except Exception:
            log.debug("RS computation failed for %s", symbol, exc_info=True)

    # Compute percentile ranks
    def _percentile(val: float | None, all_vals: list[float]) -> float | None:
        if val is None or not all_vals:
            return None
        rank = sum(1 for v in all_vals if v <= val)
        return round(rank / len(all_vals) * 100, 1)

    rs20_vals = [v["rs_20d"] for v in raw.values() if v.get("rs_20d") is not None]

    snapshots: dict[str, RSSnapshot] = {}
    for symbol, metrics in raw.items():
        sector = symbol_to_sector.get(symbol, "")
        sector_syms_rs20 = [
            raw[s]["rs_vs_sector_20d"]
            for s in raw
            if symbol_to_sector.get(s) == sector and raw[s].get("rs_vs_sector_20d") is not None
        ]
        snapshots[symbol] = RSSnapshot(
            symbol=symbol,
            date=as_of,
            rs_20d=metrics.get("rs_20d"),
            rs_60d=metrics.get("rs_60d"),
            rs_120d=metrics.get("rs_120d"),
            rs_252d=metrics.get("rs_252d"),
            rs_vs_sector_20d=metrics.get("rs_vs_sector_20d"),
            rs_vs_sector_60d=metrics.get("rs_vs_sector_60d"),
            percentile_vs_universe=_percentile(metrics.get("rs_20d"), rs20_vals),
            percentile_vs_sector=_percentile(
                metrics.get("rs_vs_sector_20d"), sector_syms_rs20
            ),
        )

    return snapshots
