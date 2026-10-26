"""Market Breadth Engine — computed entirely from constituent OHLCV data, no external API.

Daily breadth metrics:
  pct_above_ema20    — % of NIFTY 500 stocks trading above their 20-day EMA
  pct_above_ema50    — % above 50-day EMA
  pct_above_ema200   — % above 200-day EMA
  new_highs_pct      — % making 52-week highs today
  new_lows_pct       — % making 52-week lows today
  advance_decline    — advancing / declining ratio

Breadth thrust: pct_above_ema50 rising > 15pp in 5 days = early bull signal.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

log = logging.getLogger(__name__)


@dataclass
class BreadthSnapshot:
    date: date
    total_stocks: int
    pct_above_ema20: float
    pct_above_ema50: float
    pct_above_ema200: float
    new_highs_pct: float
    new_lows_pct: float
    advancing: int
    declining: int
    unchanged: int
    advance_decline_ratio: float   # advancing / max(declining, 1)
    breadth_thrust_5d: bool        # pct_above_ema50 up > 15pp vs 5 trading days ago


def compute_breadth(
    store: object,
    symbols: list[str],
    as_of: date,
    prev_breadth: BreadthSnapshot | None = None,
) -> BreadthSnapshot:
    """Compute breadth for *as_of* date from constituent indicator data.

    Parameters
    ----------
    store : ParquetStore
    symbols : NIFTY 500 symbols (bare NSE, no .NS)
    as_of : computation date
    prev_breadth : previous session's snapshot (for A/D ratio and thrust comparison)
    """
    above_ema20 = above_ema50 = above_ema200 = 0
    new_highs = new_lows = 0
    advancing = declining = unchanged = 0
    total = 0

    for symbol in symbols:
        try:
            ind = store.read_indicators(symbol)  # type: ignore[attr-defined]
            if ind.empty:
                continue
            ind = ind[ind["date"] <= as_of]
            if ind.empty:
                continue
            row = ind.iloc[-1]

            ohlcv = store.read_ohlcv_range(symbol, as_of, as_of)  # type: ignore[attr-defined]
            if ohlcv.empty:
                continue

            price = float(ohlcv.iloc[-1]["close"])
            today_high = float(ohlcv.iloc[-1]["high"])
            today_low = float(ohlcv.iloc[-1]["low"])

            total += 1

            ema20 = row.get("ema20")
            ema50 = row.get("ema50")
            ema200 = row.get("ema200")

            if ema20 is not None and price > float(ema20):
                above_ema20 += 1
            if ema50 is not None and price > float(ema50):
                above_ema50 += 1
            if ema200 is not None and price > float(ema200):
                above_ema200 += 1

            # 52-week high/low
            hist = store.read_ohlcv_range(  # type: ignore[attr-defined]
                symbol, as_of - timedelta(days=365), as_of - timedelta(days=1)
            )
            if not hist.empty:
                hi52 = float(hist["high"].max())
                lo52 = float(hist["low"].min())
                if today_high >= hi52 * 0.995:
                    new_highs += 1
                if today_low <= lo52 * 1.005:
                    new_lows += 1

            # Advance/Decline — compare today's close vs yesterday's close
            prev_ohlcv = store.read_ohlcv_range(  # type: ignore[attr-defined]
                symbol, as_of - timedelta(days=5), as_of - timedelta(days=1)
            )
            if not prev_ohlcv.empty:
                prev_close = float(prev_ohlcv.iloc[-1]["close"])
                if price > prev_close * 1.001:
                    advancing += 1
                elif price < prev_close * 0.999:
                    declining += 1
                else:
                    unchanged += 1

        except Exception:
            log.debug("Breadth skipping %s", symbol, exc_info=True)

    if total == 0:
        return BreadthSnapshot(
            date=as_of, total_stocks=0,
            pct_above_ema20=0, pct_above_ema50=0, pct_above_ema200=0,
            new_highs_pct=0, new_lows_pct=0,
            advancing=0, declining=0, unchanged=0,
            advance_decline_ratio=1.0,
            breadth_thrust_5d=False,
        )

    pct_50 = above_ema50 / total * 100
    thrust = False
    if prev_breadth is not None:
        thrust = (pct_50 - prev_breadth.pct_above_ema50) >= 15

    return BreadthSnapshot(
        date=as_of,
        total_stocks=total,
        pct_above_ema20=round(above_ema20 / total * 100, 1),
        pct_above_ema50=round(pct_50, 1),
        pct_above_ema200=round(above_ema200 / total * 100, 1),
        new_highs_pct=round(new_highs / total * 100, 1),
        new_lows_pct=round(new_lows / total * 100, 1),
        advancing=advancing,
        declining=declining,
        unchanged=unchanged,
        advance_decline_ratio=round(advancing / max(declining, 1), 2),
        breadth_thrust_5d=thrust,
    )
