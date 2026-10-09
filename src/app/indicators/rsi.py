"""Wilder RSI – no look-ahead."""
from __future__ import annotations

import numpy as np

from app.core.exceptions import InsufficientDataError


def wilder_rsi(closes: list[float], period: int = 14) -> list[float | None]:
    """
    Returns a list the same length as closes.
    First (period) values are None (warm-up).
    Uses Wilder's smoothing (alpha = 1/period).
    """
    if period < 1:
        raise ValueError(f"RSI period must be >= 1, got {period}")
    n = len(closes)
    if n < period + 1:
        raise InsufficientDataError(f"Need at least {period + 1} bars for RSI({period}), got {n}")

    result: list[float | None] = [None] * n
    arr = np.array(closes, dtype=float)
    deltas = np.diff(arr)

    gains = np.where(deltas > 0, deltas, 0.0)
    losses = np.where(deltas < 0, -deltas, 0.0)

    # Seed with simple average of first `period` moves
    avg_gain = float(np.mean(gains[:period]))
    avg_loss = float(np.mean(losses[:period]))

    def _rsi_from(ag: float, al: float) -> float:
        if al == 0.0:
            return 100.0
        rs = ag / al
        return 100.0 - 100.0 / (1.0 + rs)

    result[period] = _rsi_from(avg_gain, avg_loss)

    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gains[i]) / period
        avg_loss = (avg_loss * (period - 1) + losses[i]) / period
        result[i + 1] = _rsi_from(avg_gain, avg_loss)

    return result


def latest_rsi(closes: list[float], period: int = 14) -> float:
    """Returns the most recent non-None RSI value."""
    values = wilder_rsi(closes, period)
    for v in reversed(values):
        if v is not None:
            return v
    raise InsufficientDataError("Could not compute RSI")
