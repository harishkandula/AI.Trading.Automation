"""Wilder ATR – no look-ahead."""
from __future__ import annotations

from app.core.exceptions import InsufficientDataError


def true_range(high: float, low: float, prev_close: float) -> float:
    return max(high - low, abs(high - prev_close), abs(low - prev_close))


def wilder_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> list[float | None]:
    """
    Returns a list the same length as the inputs.
    Index 0 is None (no previous close). Indices 1..period-1 are None (warm-up).
    Index period onward contains ATR values.
    """
    if period < 1:
        raise ValueError(f"ATR period must be >= 1, got {period}")
    n = len(closes)
    if n != len(highs) or n != len(lows):
        raise ValueError("highs, lows, closes must have equal length")
    if n < period + 1:
        raise InsufficientDataError(f"Need at least {period + 1} bars for ATR({period}), got {n}")

    result: list[float | None] = [None] * n

    trs = [
        true_range(highs[i], lows[i], closes[i - 1])
        for i in range(1, n)
    ]

    # Seed: simple mean of first `period` true ranges
    avg = sum(trs[:period]) / period
    result[period] = avg

    for i in range(period, n - 1):
        avg = (avg * (period - 1) + trs[i]) / period
        result[i + 1] = avg

    return result


def latest_atr(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 14,
) -> float:
    values = wilder_atr(highs, lows, closes, period)
    for v in reversed(values):
        if v is not None:
            return v
    raise InsufficientDataError("Could not compute ATR")
