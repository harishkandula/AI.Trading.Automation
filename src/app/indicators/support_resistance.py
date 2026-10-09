"""Rolling support/resistance – uses only completed prior bars, no look-ahead."""
from __future__ import annotations


def rolling_support(lows: list[float], lookback: int = 20) -> list[float | None]:
    """
    Support at bar i = min of lows[i-lookback : i] (prior bars only, not current).
    Returns None until enough history exists.
    """
    n = len(lows)
    result: list[float | None] = [None] * n
    for i in range(lookback, n):
        result[i] = min(lows[i - lookback : i])
    return result


def rolling_resistance(highs: list[float], lookback: int = 20) -> list[float | None]:
    """
    Resistance at bar i = max of highs[i-lookback : i] (prior bars only).
    Returns None until enough history exists.
    """
    n = len(highs)
    result: list[float | None] = [None] * n
    for i in range(lookback, n):
        result[i] = max(highs[i - lookback : i])
    return result


def is_near_support(
    price: float,
    support: float,
    tolerance_pct: float = 0.01,
) -> bool:
    """True if price is within tolerance_pct of support (above or at)."""
    return price <= support * (1 + tolerance_pct)


def is_above_support(price: float, support: float) -> bool:
    return price > support
