"""Volume indicators – no look-ahead."""
from __future__ import annotations


def volume_sma(volumes: list[float], period: int) -> list[float | None]:
    """Simple moving average of volume. Returns None for warm-up bars."""
    n = len(volumes)
    result: list[float | None] = [None] * n
    for i in range(period - 1, n):
        result[i] = sum(volumes[i - period + 1 : i + 1]) / period
    return result


def latest_volume_sma(volumes: list[float], period: int) -> float:
    for v in reversed(volume_sma(volumes, period)):
        if v is not None:
            return v
    raise ValueError(f"Need at least {period} bars for volume SMA({period})")


def relative_volume(current_volume: float, avg_volume: float) -> float:
    """current / average; returns 0.0 when average is 0."""
    return current_volume / avg_volume if avg_volume > 0 else 0.0
