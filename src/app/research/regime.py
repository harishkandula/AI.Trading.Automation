"""Market Regime Engine — 6-state classification using NIFTY 500 breadth + price structure.

States (ordered bullish → bearish):
  STRONG_BULL  — index above all EMAs, breadth > 70%, new highs dominant
  BULL         — index above EMA50 and EMA200, breadth 50–70%
  SIDEWAYS     — index between EMA50 and EMA200, mixed breadth
  CORRECTION   — index below EMA50 but above EMA200, breadth deteriorating
  BEAR         — index below EMA200, breadth collapsing
  RECOVERY     — index below EMA200 but breadth thrust forming (distinct from BEAR)

The engine reads pre-computed indicator data from the ParquetStore.
It does NOT call any external API at regime computation time.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from enum import Enum

log = logging.getLogger(__name__)


class MarketRegime(str, Enum):
    STRONG_BULL = "STRONG_BULL"
    BULL = "BULL"
    SIDEWAYS = "SIDEWAYS"
    CORRECTION = "CORRECTION"
    BEAR = "BEAR"
    RECOVERY = "RECOVERY"
    UNKNOWN = "UNKNOWN"


@dataclass
class RegimeSnapshot:
    date: date
    regime: MarketRegime
    pct_above_ema20: float
    pct_above_ema50: float
    pct_above_ema200: float
    new_highs_pct: float
    new_lows_pct: float
    breadth_thrust: bool   # True if pct_above_ema50 rose >15pp in 5 days
    nifty500_above_ema50: bool
    nifty500_above_ema200: bool
    ema200_rising: bool
    notes: str = ""


def compute_regime(
    store: object,
    symbols: list[str],
    as_of: date,
    nifty500_index_symbol: str = "NIFTY500",
) -> RegimeSnapshot:
    """Compute the market regime for *as_of* date using breadth from constituent data.

    Parameters
    ----------
    store:
        ParquetStore instance (typed as object to avoid circular import at module level).
    symbols:
        NIFTY 500 constituent symbols (bare NSE symbols, no .NS suffix).
    as_of:
        The date to compute regime for (uses data up to and including this date).
    nifty500_index_symbol:
        Symbol for the NIFTY 500 index itself (for price-based checks).
    """
    from datetime import timedelta  # noqa: PLC0415

    above_ema20 = above_ema50 = above_ema200 = 0
    new_highs = new_lows = 0
    total = 0

    # For breadth thrust: need pct_above_ema50 from 5 days ago
    above_ema50_5d_ago = 0
    total_5d = 0

    date_5d_ago = as_of - timedelta(days=7)  # approx 5 trading days

    for symbol in symbols:
        try:
            ind = store.read_indicators(symbol)  # type: ignore[attr-defined]
            if ind.empty:
                continue
            # Latest row as of our date
            ind = ind[ind["date"] <= as_of]
            if ind.empty:
                continue
            row = ind.iloc[-1]
            close_today = store.read_ohlcv_range(symbol, as_of, as_of)  # type: ignore[attr-defined]
            if close_today.empty:
                continue
            price = float(close_today.iloc[-1]["close"])

            total += 1
            ema20 = row.get("ema20")
            ema50 = row.get("ema50")
            ema200 = row.get("ema200")

            if ema20 is not None and price > ema20:
                above_ema20 += 1
            if ema50 is not None and price > ema50:
                above_ema50 += 1
            if ema200 is not None and price > ema200:
                above_ema200 += 1

            # 52-week high/low
            hist = store.read_ohlcv_range(  # type: ignore[attr-defined]
                symbol, as_of - timedelta(days=365), as_of
            )
            if len(hist) >= 5:
                high_52w = hist["high"].max()
                low_52w = hist["low"].min()
                if float(close_today.iloc[-1]["high"]) >= high_52w * 0.995:
                    new_highs += 1
                if float(close_today.iloc[-1]["low"]) <= low_52w * 1.005:
                    new_lows += 1

            # Breadth 5 days ago (for thrust detection)
            ind_5d = ind[ind["date"] <= date_5d_ago]
            if not ind_5d.empty:
                row_5d = ind_5d.iloc[-1]
                close_5d = store.read_ohlcv_range(  # type: ignore[attr-defined]
                    symbol, date_5d_ago - timedelta(days=3), date_5d_ago
                )
                if not close_5d.empty:
                    price_5d = float(close_5d.iloc[-1]["close"])
                    ema50_5d = row_5d.get("ema50")
                    if ema50_5d is not None and price_5d > ema50_5d:
                        above_ema50_5d_ago += 1
                    total_5d += 1

        except Exception:
            log.debug("Skipping %s in regime computation", symbol, exc_info=True)

    if total == 0:
        return RegimeSnapshot(
            date=as_of,
            regime=MarketRegime.UNKNOWN,
            pct_above_ema20=0, pct_above_ema50=0, pct_above_ema200=0,
            new_highs_pct=0, new_lows_pct=0,
            breadth_thrust=False,
            nifty500_above_ema50=False, nifty500_above_ema200=False,
            ema200_rising=False,
            notes="Insufficient constituent data",
        )

    pct_ema20 = above_ema20 / total * 100
    pct_ema50 = above_ema50 / total * 100
    pct_ema200 = above_ema200 / total * 100
    new_highs_pct = new_highs / total * 100
    new_lows_pct = new_lows / total * 100

    pct_ema50_5d = (above_ema50_5d_ago / total_5d * 100) if total_5d > 0 else pct_ema50
    breadth_thrust = (pct_ema50 - pct_ema50_5d) >= 15

    # Use NIFTY500 index data for price-based checks
    idx = store.read_ohlcv_range(nifty500_index_symbol, as_of - timedelta(days=300), as_of)  # type: ignore[attr-defined]
    nifty_above_ema50 = False
    nifty_above_ema200 = False
    ema200_rising = False

    if not idx.empty and len(idx) >= 50:
        from app.indicators.ema import ema as compute_ema, ema_slope  # noqa: PLC0415
        closes = idx["close"].tolist()
        try:
            ema50_vals = compute_ema(closes, 50)
            ema200_vals = compute_ema(closes, 200)
            last_close = closes[-1]
            last_ema50 = next((v for v in reversed(ema50_vals) if v is not None), None)
            last_ema200 = next((v for v in reversed(ema200_vals) if v is not None), None)
            if last_ema50:
                nifty_above_ema50 = last_close > last_ema50
            if last_ema200:
                nifty_above_ema200 = last_close > last_ema200
            try:
                slope = ema_slope(closes, 200, lookback=10)
                ema200_rising = slope > 0
            except Exception:
                pass
        except Exception:
            pass
    else:
        # Fall back to breadth as proxy when index data unavailable
        nifty_above_ema50 = pct_ema50 > 50
        nifty_above_ema200 = pct_ema200 > 50
        ema200_rising = pct_ema200 > 55

    regime = _classify(
        pct_ema50, pct_ema200,
        new_highs_pct, new_lows_pct,
        nifty_above_ema50, nifty_above_ema200,
        ema200_rising, breadth_thrust,
    )

    return RegimeSnapshot(
        date=as_of,
        regime=regime,
        pct_above_ema20=round(pct_ema20, 1),
        pct_above_ema50=round(pct_ema50, 1),
        pct_above_ema200=round(pct_ema200, 1),
        new_highs_pct=round(new_highs_pct, 1),
        new_lows_pct=round(new_lows_pct, 1),
        breadth_thrust=breadth_thrust,
        nifty500_above_ema50=nifty_above_ema50,
        nifty500_above_ema200=nifty_above_ema200,
        ema200_rising=ema200_rising,
    )


def _classify(
    pct_ema50: float,
    pct_ema200: float,
    new_highs_pct: float,
    new_lows_pct: float,
    above_ema50: bool,
    above_ema200: bool,
    ema200_rising: bool,
    breadth_thrust: bool,
) -> MarketRegime:
    # RECOVERY: index below EMA200 but breadth thrusting upward — distinct from BEAR
    if not above_ema200 and breadth_thrust and pct_ema50 > 35:
        return MarketRegime.RECOVERY

    if above_ema50 and above_ema200 and ema200_rising and pct_ema50 > 65:
        return MarketRegime.STRONG_BULL

    if above_ema50 and above_ema200 and pct_ema50 >= 50:
        return MarketRegime.BULL

    if not above_ema200 and pct_ema50 < 30 and new_lows_pct > new_highs_pct:
        return MarketRegime.BEAR

    if above_ema200 and not above_ema50 and pct_ema50 < 50:
        return MarketRegime.CORRECTION

    return MarketRegime.SIDEWAYS
