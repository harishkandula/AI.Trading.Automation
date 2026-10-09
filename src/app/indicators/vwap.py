"""VWAP – resets each NSE session."""
from __future__ import annotations

from datetime import datetime

from app.core.time_utils import IST


def session_vwap(
    timestamps: list[datetime],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> list[float | None]:
    """
    Computes intraday VWAP, resetting at each NSE session open (09:15 IST).
    Bars with zero volume contribute 0 to numerator/denominator accumulation.
    Returns None for bars before any volume has accumulated.
    """
    n = len(timestamps)
    result: list[float | None] = [None] * n

    cum_tpv = 0.0  # sum(typical_price * volume)
    cum_vol = 0.0
    prev_date = None

    for i, ts in enumerate(timestamps):
        ts_ist = ts.astimezone(IST)
        bar_date = ts_ist.date()

        if bar_date != prev_date:
            cum_tpv = 0.0
            cum_vol = 0.0
            prev_date = bar_date

        tp = (highs[i] + lows[i] + closes[i]) / 3.0
        vol = volumes[i]
        if vol < 0:
            vol = 0.0

        cum_tpv += tp * vol
        cum_vol += vol

        result[i] = cum_tpv / cum_vol if cum_vol > 0 else None

    return result


def latest_session_vwap(
    timestamps: list[datetime],
    highs: list[float],
    lows: list[float],
    closes: list[float],
    volumes: list[float],
) -> float | None:
    values = session_vwap(timestamps, highs, lows, closes, volumes)
    for v in reversed(values):
        if v is not None:
            return v
    return None
