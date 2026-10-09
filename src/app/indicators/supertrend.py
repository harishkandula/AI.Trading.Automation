"""Supertrend indicator – no look-ahead."""
from __future__ import annotations

from dataclasses import dataclass

from app.indicators.atr import wilder_atr
from app.core.exceptions import InsufficientDataError


@dataclass
class SupertrendResult:
    value: float | None
    direction: int | None   # +1 = bullish (price above band), -1 = bearish


def supertrend(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> list[SupertrendResult]:
    """
    Returns one SupertrendResult per bar.
    direction = +1 means trend is up (Supertrend line is below price).
    direction = -1 means trend is down (Supertrend line is above price).
    Entries before enough warm-up have value=None, direction=None.
    """
    n = len(closes)
    atrs = wilder_atr(highs, lows, closes, period)
    results: list[SupertrendResult] = [SupertrendResult(None, None)] * n

    upper_band: float | None = None
    lower_band: float | None = None
    prev_direction: int = 1

    for i in range(1, n):
        atr_val = atrs[i]
        if atr_val is None:
            continue

        hl2 = (highs[i] + lows[i]) / 2.0
        basic_upper = hl2 + multiplier * atr_val
        basic_lower = hl2 - multiplier * atr_val

        if upper_band is None:
            upper_band = basic_upper
            lower_band = basic_lower
            direction = 1
        else:
            # Lower band only moves up
            new_lower = basic_lower
            if new_lower > lower_band or closes[i - 1] < lower_band:
                lower_band = new_lower
            else:
                lower_band = lower_band  # stays

            # Upper band only moves down
            new_upper = basic_upper
            if new_upper < upper_band or closes[i - 1] > upper_band:
                upper_band = new_upper
            else:
                upper_band = upper_band  # stays

            if prev_direction == 1 and closes[i] < lower_band:
                direction = -1
            elif prev_direction == -1 and closes[i] > upper_band:
                direction = 1
            else:
                direction = prev_direction

        st_value = lower_band if direction == 1 else upper_band
        results[i] = SupertrendResult(value=st_value, direction=direction)
        prev_direction = direction

    return results


def latest_supertrend(
    highs: list[float],
    lows: list[float],
    closes: list[float],
    period: int = 10,
    multiplier: float = 3.0,
) -> SupertrendResult:
    results = supertrend(highs, lows, closes, period, multiplier)
    for r in reversed(results):
        if r.direction is not None:
            return r
    raise InsufficientDataError("Could not compute Supertrend")
