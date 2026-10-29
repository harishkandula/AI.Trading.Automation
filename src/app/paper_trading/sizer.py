"""Position sizing helper for paper trades.

Uses a risk-per-trade model:
  * Risk no more than ``risk_pct`` (default 1%) of portfolio equity on a single trade.
  * Never allocate more than ``max_position_pct`` (default 10%) of equity to one position.
"""
from __future__ import annotations

import math


def compute_qty(
    entry_price: float,
    stop_price: float,
    portfolio_equity: float,
    risk_pct: float = 0.01,         # 1% risk per trade
    max_position_pct: float = 0.10,  # max 10% of equity in one position
) -> int:
    """Return share quantity using 1% risk rule, capped at 10% of equity.

    Parameters
    ----------
    entry_price:
        The planned entry price per share (must be > 0).
    stop_price:
        The initial stop-loss price per share (must be < entry_price and > 0).
    portfolio_equity:
        Total portfolio value in INR.
    risk_pct:
        Fraction of equity to risk per trade (default 0.01 = 1%).
    max_position_pct:
        Hard cap: maximum fraction of equity that can go into this position
        (default 0.10 = 10%).

    Returns
    -------
    int
        Number of shares; always >= 1.

    Raises
    ------
    ValueError
        If ``entry_price`` <= 0 or ``stop_price`` >= ``entry_price``.
    """
    if entry_price <= 0:
        raise ValueError(f"entry_price must be > 0, got {entry_price}")
    if stop_price >= entry_price:
        raise ValueError(
            f"stop_price must be < entry_price, got stop={stop_price} entry={entry_price}"
        )
    if stop_price <= 0:
        raise ValueError(f"stop_price must be > 0, got {stop_price}")

    stop_distance = entry_price - stop_price

    # Quantity implied by the risk limit
    risk_amount = portfolio_equity * risk_pct
    qty_by_risk = math.floor(risk_amount / stop_distance)

    # Quantity implied by the position-size cap
    qty_by_cap = math.floor(portfolio_equity * max_position_pct / entry_price)

    qty = min(qty_by_risk, qty_by_cap)
    return max(qty, 1)
