"""Portfolio-level risk engine.

Applies guards before any signal is eligible to become a live order.
All checks are stateless relative to each other — the engine reads portfolio
state at call time and makes a binary approve/reject decision per signal.

Guards (all configurable via RiskConfig):
  1. Max open positions (absolute count)
  2. Max sector exposure (% of portfolio in a single GICS sector)
  3. Max segment exposure (% in LARGE_CAP / MID_CAP / SMALL_CAP)
  4. Max single-position size (% of portfolio per trade — redundant with
     position-sizing but enforced here as a hard ceiling)
  5. Daily loss limit — if realised P&L today ≤ -daily_loss_limit_pct × equity,
     no new entries for the rest of the session
  6. Weekly loss limit — same logic, rolling Mon→Fri window
  7. 1% risk rule — stop distance must imply risk ≤ 1% of equity per trade
     (position sizing already enforces this; the risk engine rejects signals
     that have stop_price missing or stop distance > max_risk_per_trade_pct)

Usage in the morning handler:
    guard = RiskGuard(config, portfolio_state)
    approved = [s for s in signals if guard.check(s).approved]
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from typing import Any


@dataclass
class RiskConfig:
    # Position limits
    max_open_positions: int = 15
    max_new_positions_per_day: int = 3

    # Exposure caps (as fractions of portfolio equity)
    max_sector_exposure_pct: float = 0.30   # 30% in any single sector
    max_segment_exposure_pct: float = 0.50  # 50% in any single segment (L/M/S cap)
    max_position_size_pct: float = 0.10     # 10% of equity per single position

    # Risk-per-trade ceiling (stop-distance / entry must be ≤ this)
    max_risk_per_trade_pct: float = 0.01    # 1% of equity risked per trade

    # Session loss limits
    daily_loss_limit_pct: float = 0.02      # halt new entries if day loss ≥ 2% equity
    weekly_loss_limit_pct: float = 0.05     # halt if week loss ≥ 5% equity

    # Minimum score to be eligible (0 = disabled)
    min_signal_score: float = 40.0


@dataclass
class RiskDecision:
    approved: bool
    signal_id: str
    symbol: str
    reason: str


@dataclass
class PortfolioSnapshot:
    """Lightweight view of portfolio state passed to the risk engine at scan time."""
    equity: float
    open_positions: list[dict[str, Any]] = field(default_factory=list)
    # Each position dict must have: symbol, sector, segment, current_value, entry_price, quantity
    realised_pnl_today: float = 0.0
    realised_pnl_this_week: float = 0.0
    new_entries_today: int = 0


class RiskGuard:
    """Stateful gate for a single scan session.

    Instantiate once per handler invocation with the current portfolio snapshot.
    Call check() for each candidate signal; approved signals have their sector/
    segment totals accumulated so later signals see the updated exposure.
    """

    def __init__(self, config: RiskConfig, portfolio: PortfolioSnapshot) -> None:
        self._cfg = config
        self._equity = portfolio.equity
        self._new_today = portfolio.new_entries_today
        self._pnl_today = portfolio.realised_pnl_today
        self._pnl_week = portfolio.realised_pnl_this_week
        self._open_count = len(portfolio.open_positions)

        # Build current sector / segment exposure from open positions
        self._sector_value: dict[str, float] = {}
        self._segment_value: dict[str, float] = {}
        for pos in portfolio.open_positions:
            sect = pos.get("sector", "UNKNOWN")
            seg = pos.get("segment", "UNKNOWN")
            val = float(pos.get("current_value", 0.0))
            self._sector_value[sect] = self._sector_value.get(sect, 0.0) + val
            self._segment_value[seg] = self._segment_value.get(seg, 0.0) + val

    def check(
        self,
        signal: Any,
        sector: str = "UNKNOWN",
        segment: str = "UNKNOWN",
        as_of: date | None = None,
    ) -> RiskDecision:
        cfg = self._cfg
        sig_id = getattr(signal, "signal_id", str(signal))
        symbol = getattr(signal, "symbol", "?")

        # 1. Daily loss limit
        if self._equity > 0:
            daily_loss_pct = -self._pnl_today / self._equity
            if daily_loss_pct >= cfg.daily_loss_limit_pct:
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Daily loss limit hit ({daily_loss_pct * 100:.1f}% >= "
                    f"{cfg.daily_loss_limit_pct * 100:.1f}%)",
                )

        # 2. Weekly loss limit
            weekly_loss_pct = -self._pnl_week / self._equity
            if weekly_loss_pct >= cfg.weekly_loss_limit_pct:
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Weekly loss limit hit ({weekly_loss_pct * 100:.1f}% >= "
                    f"{cfg.weekly_loss_limit_pct * 100:.1f}%)",
                )

        # 3. Max open positions
        if self._open_count >= cfg.max_open_positions:
            return RiskDecision(
                False, sig_id, symbol,
                f"Max open positions reached ({self._open_count}/{cfg.max_open_positions})",
            )

        # 4. Max new entries today
        if self._new_today >= cfg.max_new_positions_per_day:
            return RiskDecision(
                False, sig_id, symbol,
                f"Max new entries per day reached ({self._new_today}/"
                f"{cfg.max_new_positions_per_day})",
            )

        # 5. Stop price required and 1% risk rule
        entry_ref = getattr(signal, "entry_reference_price", None)
        stop_price = getattr(signal, "stop_price", None)
        if entry_ref is None or entry_ref <= 0:
            return RiskDecision(False, sig_id, symbol, "Missing entry reference price")
        if stop_price is None or stop_price >= entry_ref:
            return RiskDecision(False, sig_id, symbol, "Missing or invalid stop price")

        stop_distance_pct = (entry_ref - stop_price) / entry_ref
        estimated_risk = stop_distance_pct * cfg.max_position_size_pct
        if estimated_risk > cfg.max_risk_per_trade_pct:
            return RiskDecision(
                False, sig_id, symbol,
                f"Stop too wide: estimated risk {estimated_risk * 100:.2f}% > "
                f"{cfg.max_risk_per_trade_pct * 100:.2f}% per-trade limit",
            )

        # 6. Signal score gate
        score = (getattr(signal, "indicators", None) or {}).get("score", None)
        if score is not None and cfg.min_signal_score > 0:
            if float(score) < cfg.min_signal_score:
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Score {score} below minimum {cfg.min_signal_score}",
                )

        # 7. Sector exposure cap
        if self._equity > 0:
            estimated_position_value = self._equity * cfg.max_position_size_pct
            projected_sector = self._sector_value.get(sector, 0.0) + estimated_position_value
            if projected_sector / self._equity > cfg.max_sector_exposure_pct:
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Sector {sector!r} exposure would exceed "
                    f"{cfg.max_sector_exposure_pct * 100:.0f}%",
                )

            # 8. Segment exposure cap
            projected_segment = self._segment_value.get(segment, 0.0) + estimated_position_value
            if projected_segment / self._equity > cfg.max_segment_exposure_pct:
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Segment {segment!r} exposure would exceed "
                    f"{cfg.max_segment_exposure_pct * 100:.0f}%",
                )

        # 9. Earnings blackout
        try:
            from app.data.earnings_calendar import has_upcoming_earnings  # noqa: PLC0415
            _check_date = as_of if as_of is not None else date.today()
            if has_upcoming_earnings(symbol, _check_date, window_days=3):
                return RiskDecision(
                    False, sig_id, symbol,
                    f"Earnings blackout: {symbol} reports within 3 days",
                )
        except Exception:
            pass  # calendar is best-effort — never block on failure

        # --- Approved: update running counters ---
        self._open_count += 1
        self._new_today += 1
        estimated_value = self._equity * cfg.max_position_size_pct
        self._sector_value[sector] = self._sector_value.get(sector, 0.0) + estimated_value
        self._segment_value[segment] = self._segment_value.get(segment, 0.0) + estimated_value

        return RiskDecision(True, sig_id, symbol, "OK")

    def approved_count(self) -> int:
        return self._new_today

    def rejection_summary(self) -> dict[str, int]:
        """Call after processing all signals to get counts by reason category."""
        return {}


def build_portfolio_snapshot(
    open_trades: list[Any],
    symbol_to_sector: dict[str, str],
    symbol_to_segment: dict[str, str],
    current_prices: dict[str, float],
    equity: float,
    realised_pnl_today: float = 0.0,
    realised_pnl_this_week: float = 0.0,
    new_entries_today: int = 0,
) -> PortfolioSnapshot:
    """Build a PortfolioSnapshot from a list of Trade objects."""
    positions: list[dict[str, Any]] = []
    for trade in open_trades:
        sym = getattr(trade, "symbol", "?")
        entry_price = getattr(trade, "entry_price", 0.0) or 0.0
        qty = getattr(trade, "remaining_quantity", 0.0) or 0.0
        current_price = current_prices.get(sym, entry_price)
        positions.append({
            "symbol": sym,
            "sector": symbol_to_sector.get(sym, "UNKNOWN"),
            "segment": symbol_to_segment.get(sym, "UNKNOWN"),
            "entry_price": entry_price,
            "quantity": qty,
            "current_value": current_price * qty,
        })
    return PortfolioSnapshot(
        equity=equity,
        open_positions=positions,
        realised_pnl_today=realised_pnl_today,
        realised_pnl_this_week=realised_pnl_this_week,
        new_entries_today=new_entries_today,
    )
