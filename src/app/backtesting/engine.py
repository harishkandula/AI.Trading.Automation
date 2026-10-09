"""
Event-driven backtesting engine.

Processes daily candles bar-by-bar in chronological order.
No look-ahead: signal generated at bar i enters at bar i+1 open.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from app.backtesting.costs import CostProfile, get_profile
from app.backtesting.positions import risk_based_quantity
from app.core.identifiers import new_uuid
from app.core.time_utils import nth_trading_day_after
from app.models.candle import Candle
from app.models.signal import Signal
from app.models.trade import Trade, TradeEvent, TradeStatus

logger = logging.getLogger(__name__)


@dataclass
class PortfolioState:
    cash: float
    equity: float
    open_trades: list[Trade] = field(default_factory=list)
    closed_trades: list[Trade] = field(default_factory=list)
    equity_curve: list[tuple[date, float]] = field(default_factory=list)
    rejected_trades: list[dict[str, Any]] = field(default_factory=list)
    ambiguous_bar_count: int = 0


def _execution_price(ref_price: float, slippage_pct: float, direction: int = 1) -> float:
    """direction=1 buy (adverse = higher), direction=-1 sell (adverse = lower)."""
    return ref_price * (1 + direction * slippage_pct)


def _open_value(
    open_trades: list[Trade],
    candle_map: dict[str, dict[date, Candle]],
    today: date,
) -> float:
    total = 0.0
    for t in open_trades:
        c = candle_map.get(t.symbol, {}).get(today)
        price = c.close if c else (t.entry_price or 0.0)
        total += price * t.remaining_quantity
    return total


def run_backtest(
    signals: list[Signal],
    candles_by_symbol: dict[str, list[Candle]],
    initial_capital: float = 5_000_000.0,
    risk_per_trade_pct: float = 0.005,
    max_position_value_pct: float = 0.10,
    max_open_positions: int = 10,
    max_new_positions_per_session: int = 3,
    cost_profile: str = "default_delivery_estimate",
    same_bar_policy: str = "CONSERVATIVE",   # CONSERVATIVE = stop before target
) -> PortfolioState:
    """
    Run a signal-level event-driven backtest.

    Assumptions:
    - Daily candles only (per bar-by-bar logic).
    - Entry at next session open.
    - Gap-through stop: executes at open, not stop price.
    - Same-bar policy CONSERVATIVE: stop counted first if both T+S in same bar.
    """
    profile: CostProfile = get_profile(cost_profile)
    state = PortfolioState(cash=initial_capital, equity=initial_capital)

    # Index signals by intended entry date
    pending: dict[date, list[Signal]] = {}
    for sig in signals:
        if sig.planned_entry_timestamp:
            entry_date = sig.planned_entry_timestamp.date()
        else:
            continue
        pending.setdefault(entry_date, []).append(sig)

    # Collect all trading dates across all symbols
    all_dates: list[date] = sorted(
        {c.timestamp.date() for bars in candles_by_symbol.values() for c in bars}
    )

    # Quick lookup: symbol -> date -> candle
    candle_map: dict[str, dict[date, Candle]] = {}
    for sym, bars in candles_by_symbol.items():
        candle_map[sym] = {c.timestamp.date(): c for c in bars}

    for today in all_dates:
        new_this_session = 0

        # --- Evaluate open trades ---
        still_open: list[Trade] = []
        for trade in state.open_trades:
            sym = trade.symbol
            candle = candle_map.get(sym, {}).get(today)
            if candle is None:
                still_open.append(trade)
                continue

            exit_date = trade.exit_deadline
            assert trade.entry_price is not None
            assert trade.stop_price is not None

            stop = trade.stop_price
            targets = trade.target_prices
            open_price = candle.open
            close_price = candle.close

            # Gap-through stop check
            if open_price <= stop:
                exec_price = _execution_price(open_price, profile.slippage_pct, direction=-1)
                _close_trade(
                    trade, today, exec_price, candle, TradeStatus.CLOSED_STOP, state, profile,
                    "Gap through stop",
                )
                continue

            hit_target = targets and close_price >= targets[0]
            hit_stop = candle.low <= stop

            if hit_target and hit_stop:
                state.ambiguous_bar_count += 1
                if same_bar_policy == "CONSERVATIVE":
                    hit_target = False  # stop counted first (worst for long)

            if hit_target:
                # Partial exit at Target 1
                t1_price = _execution_price(targets[0], profile.slippage_pct, direction=-1)
                partial_qty = int(trade.remaining_quantity * 0.5)
                if partial_qty > 0:
                    pnl = (t1_price - trade.entry_price) * partial_qty
                    cost_dict = profile.compute(
                        trade.entry_price * partial_qty, t1_price * partial_qty
                    )
                    pnl -= cost_dict["total"]
                    trade.remaining_quantity -= partial_qty
                    trade.realized_pnl += pnl
                    state.cash += t1_price * partial_qty - cost_dict["total"]
                    trade.events.append(TradeEvent(
                        event_type="PARTIAL_EXIT_TARGET1",
                        timestamp=candle.timestamp,
                        quantity=partial_qty,
                        price=t1_price,
                        cost=cost_dict["total"],
                    ))
                    # Adjust stop: add stop_adjustment to original stop
                    adj_pct = trade.parameter_snapshot.get("stop_adjustment_pct", 0.02)
                    trade.stop_price = round(
                        trade.entry_price * (1 - 0.02 + adj_pct), 4
                    )
                    trade.status = TradeStatus.PARTIALLY_CLOSED
                    trade.target_prices = targets[1:] if len(targets) > 1 else []

                if trade.remaining_quantity <= 0:
                    trade.status = TradeStatus.CLOSED_TARGET
                    state.closed_trades.append(trade)
                    continue

            if hit_stop and not hit_target:
                exec_price = _execution_price(stop, profile.slippage_pct, direction=-1)
                _close_trade(
                    trade, today, exec_price, candle, TradeStatus.CLOSED_STOP, state, profile,
                    "Stop hit",
                )
                continue

            # Time-based exit
            if exit_date is not None and today >= exit_date:
                exec_price = _execution_price(close_price, profile.slippage_pct, direction=-1)
                _close_trade(
                    trade, today, exec_price, candle, TradeStatus.CLOSED_TIME, state, profile,
                    "Time exit",
                )
                continue

            still_open.append(trade)

        state.open_trades = still_open

        # --- Enter new signals ---
        for sig in pending.get(today, []):
            if len(state.open_trades) >= max_open_positions:
                state.rejected_trades.append(
                    {"signal_id": sig.signal_id, "reason": "Max positions reached"}
                )
                continue
            if new_this_session >= max_new_positions_per_session:
                state.rejected_trades.append(
                    {"signal_id": sig.signal_id, "reason": "Max new per session"}
                )
                continue

            sym = sig.symbol
            candle = candle_map.get(sym, {}).get(today)
            if candle is None:
                state.rejected_trades.append(
                    {"signal_id": sig.signal_id, "reason": "No candle on entry date"}
                )
                continue

            entry_price = _execution_price(candle.open, profile.slippage_pct, direction=1)
            stop_price = sig.stop_price
            if stop_price is None:
                stop_price = entry_price * 0.97

            sizing = risk_based_quantity(
                entry_price, stop_price, state.equity, risk_per_trade_pct, max_position_value_pct
            )
            if sizing.rejected:
                state.rejected_trades.append({"signal_id": sig.signal_id, "reason": sizing.reason})
                continue

            qty = sizing.quantity
            # entry side only
            cost_dict = profile.compute(entry_price * qty, entry_price * qty)
            total_entry_cost = entry_price * qty + cost_dict["total"] / 2

            if total_entry_cost > state.cash:
                state.rejected_trades.append(
                    {"signal_id": sig.signal_id, "reason": "Insufficient cash"}
                )
                continue

            state.cash -= total_entry_cost
            hold = sig.holding_period_sessions or 5
            exit_deadline = nth_trading_day_after(today, hold)

            trade = Trade(
                trade_id=new_uuid(),
                signal_id=sig.signal_id,
                strategy_id=sig.strategy_id,
                strategy_version=sig.strategy_version,
                symbol=sym,
                exchange=sig.exchange,
                entry_timestamp=candle.timestamp,
                entry_price=entry_price,
                entry_quantity=float(qty),
                entry_cost=cost_dict["total"] / 2,
                stop_price=stop_price,
                target_prices=list(sig.target_prices),
                holding_period_sessions=hold,
                exit_deadline=exit_deadline,
                remaining_quantity=float(qty),
                status=TradeStatus.OPEN,
                parameter_snapshot=sig.parameter_snapshot,
            )
            state.open_trades.append(trade)
            new_this_session += 1
            logger.debug(f"Entry {sym} qty={qty} @ {entry_price:.2f}")

        # Update equity
        open_value = _open_value(state.open_trades, candle_map, today)
        state.equity = state.cash + open_value
        state.equity_curve.append((today, state.equity))

    return state


def _close_trade(
    trade: Trade,
    today: date,
    exec_price: float,
    candle: Candle,
    status: TradeStatus,
    state: PortfolioState,
    profile: CostProfile,
    reason: str,
) -> None:
    qty = trade.remaining_quantity
    assert trade.entry_price is not None
    cost_dict = profile.compute(trade.entry_price * qty, exec_price * qty)
    pnl = (exec_price - trade.entry_price) * qty - cost_dict["total"]
    trade.realized_pnl += pnl
    trade.remaining_quantity = 0
    trade.status = status
    state.cash += exec_price * qty - cost_dict["total"]
    trade.events.append(TradeEvent(
        event_type=f"EXIT_{status.value}",
        timestamp=candle.timestamp,
        quantity=qty,
        price=exec_price,
        cost=cost_dict["total"],
        notes=reason,
    ))
    state.closed_trades.append(trade)
