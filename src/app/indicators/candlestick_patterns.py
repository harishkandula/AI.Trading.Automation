"""Deterministic candlestick pattern detection – no look-ahead."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CandleAttrs:
    open: float
    high: float
    low: float
    close: float

    @property
    def real_body(self) -> float:
        return abs(self.close - self.open)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body_ratio(self) -> float:
        return self.real_body / self.range if self.range > 1e-9 else 0.0

    @property
    def midpoint(self) -> float:
        return (self.open + self.close) / 2.0

    @property
    def is_bearish(self) -> bool:
        return self.close < self.open

    @property
    def is_bullish(self) -> bool:
        return self.close > self.open


def is_morning_star(
    c1: CandleAttrs,
    c2: CandleAttrs,
    c3: CandleAttrs,
    c1_body_min_ratio: float = 0.60,
    c2_body_max_ratio: float = 0.30,
    c3_must_close_above_c1_mid: bool = True,
) -> tuple[bool, list[str]]:
    """
    Returns (qualifies, reasons).
    Candle 1: bearish, real-body >= c1_body_min_ratio of range.
    Candle 2: indecisive, real-body <= c2_body_max_ratio of range.
    Candle 3: bullish, closes above midpoint of C1's real body.
    """
    reasons: list[str] = []
    fails: list[str] = []

    if c1.is_bearish:
        reasons.append("C1 bearish")
    else:
        fails.append("C1 not bearish")

    if c1.body_ratio >= c1_body_min_ratio:
        reasons.append(f"C1 body ratio {c1.body_ratio:.2f} >= {c1_body_min_ratio}")
    else:
        fails.append(f"C1 body ratio {c1.body_ratio:.2f} < {c1_body_min_ratio}")

    if c2.body_ratio <= c2_body_max_ratio:
        reasons.append(f"C2 indecisive body ratio {c2.body_ratio:.2f}")
    else:
        fails.append(f"C2 body ratio {c2.body_ratio:.2f} > {c2_body_max_ratio}")

    if c3.is_bullish:
        reasons.append("C3 bullish")
    else:
        fails.append("C3 not bullish")

    if c3_must_close_above_c1_mid:
        c1_mid = c1.midpoint
        if c3.close > c1_mid:
            reasons.append(f"C3 close {c3.close:.2f} above C1 midpoint {c1_mid:.2f}")
        else:
            fails.append(f"C3 close {c3.close:.2f} <= C1 midpoint {c1_mid:.2f}")

    qualifies = len(fails) == 0
    return qualifies, reasons if qualifies else fails
