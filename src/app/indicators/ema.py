"""Exponential Moving Average — no look-ahead."""
from __future__ import annotations

from app.core.exceptions import InsufficientDataError


def ema(closes: list[float], period: int) -> list[float | None]:
    """Returns EMA series, same length as closes. First (period-1) values are None."""
    if period < 1:
        raise ValueError(f"EMA period must be >= 1, got {period}")
    n = len(closes)
    if n < period:
        raise InsufficientDataError(f"Need at least {period} bars for EMA({period}), got {n}")

    result: list[float | None] = [None] * n
    k = 2.0 / (period + 1)
    result[period - 1] = sum(closes[:period]) / period
    for i in range(period, n):
        result[i] = closes[i] * k + result[i - 1] * (1 - k)  # type: ignore[operator]
    return result


def latest_ema(closes: list[float], period: int) -> float:
    values = ema(closes, period)
    for v in reversed(values):
        if v is not None:
            return v
    raise InsufficientDataError(f"Could not compute EMA({period})")


def ema_slope(closes: list[float], period: int, lookback: int = 5) -> float:
    """Return the average daily change of EMA over the last `lookback` bars (positive = rising)."""
    values = ema(closes, period)
    valid = [v for v in values if v is not None]
    if len(valid) < lookback + 1:
        raise InsufficientDataError(f"Need {lookback + 1} EMA values for slope")
    tail = valid[-(lookback + 1):]
    return (tail[-1] - tail[0]) / lookback
