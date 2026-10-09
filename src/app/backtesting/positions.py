"""Position sizing with risk-based quantity calculation."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class SizingResult:
    quantity: int
    reason: str
    rejected: bool = False


def risk_based_quantity(
    entry_price: float,
    stop_price: float,
    current_equity: float,
    risk_per_trade_pct: float,
    max_position_value_pct: float = 0.10,
) -> SizingResult:
    """
    Compute position size as described in the spec:
      risk_budget = equity * risk_per_trade_pct
      risk_per_share = entry - stop
      qty_by_risk = floor(risk_budget / risk_per_share)
      qty_by_cap = floor(equity * max_position_value_pct / entry)
      final = min(qty_by_risk, qty_by_cap)
    """
    risk_per_share = entry_price - stop_price
    if risk_per_share <= 0:
        return SizingResult(0, f"Stop {stop_price} >= entry {entry_price}", rejected=True)

    risk_budget = current_equity * risk_per_trade_pct
    qty_by_risk = math.floor(risk_budget / risk_per_share)
    qty_by_cap = math.floor((current_equity * max_position_value_pct) / entry_price)
    qty_by_cash = math.floor(current_equity / entry_price)

    qty = min(qty_by_risk, qty_by_cap, qty_by_cash)
    if qty <= 0:
        return SizingResult(0, f"Zero quantity: risk_qty={qty_by_risk} cap_qty={qty_by_cap} cash_qty={qty_by_cash}", rejected=True)

    return SizingResult(
        quantity=qty,
        reason=f"qty={qty} (risk_qty={qty_by_risk}, cap_qty={qty_by_cap}, cash_qty={qty_by_cash})",
    )
