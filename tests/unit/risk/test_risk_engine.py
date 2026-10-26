"""Tests for the portfolio risk engine."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Any

sys.path.insert(0, "src")

from app.risk.engine import (
    PortfolioSnapshot,
    RiskConfig,
    RiskGuard,
    build_portfolio_snapshot,
)


# ---------------------------------------------------------------------------
# Minimal signal stub
# ---------------------------------------------------------------------------

@dataclass
class _Sig:
    signal_id: str
    symbol: str
    entry_reference_price: float
    stop_price: float
    indicators: dict[str, Any]


def _sig(
    symbol: str = "TESTCO",
    entry: float = 100.0,
    stop: float = 95.0,
    score: float = 60.0,
) -> _Sig:
    return _Sig(
        signal_id=f"sig_{symbol}",
        symbol=symbol,
        entry_reference_price=entry,
        stop_price=stop,
        indicators={"score": score},
    )


def _clean_guard(
    equity: float = 1_000_000.0,
    config: RiskConfig | None = None,
) -> RiskGuard:
    portfolio = PortfolioSnapshot(equity=equity)
    return RiskGuard(config or RiskConfig(), portfolio)


# ---------------------------------------------------------------------------
# Basic approval
# ---------------------------------------------------------------------------

class TestBasicApproval:
    def test_clean_portfolio_approves(self) -> None:
        guard = _clean_guard()
        decision = guard.check(_sig())
        assert decision.approved

    def test_approved_decision_reason_is_ok(self) -> None:
        guard = _clean_guard()
        assert guard.check(_sig()).reason == "OK"

    def test_approved_increments_new_today_counter(self) -> None:
        guard = _clean_guard()
        guard.check(_sig("A"))
        guard.check(_sig("B"))
        assert guard.approved_count() == 2


# ---------------------------------------------------------------------------
# Daily / weekly loss limits
# ---------------------------------------------------------------------------

class TestLossLimits:
    def test_daily_loss_limit_blocks_entry(self) -> None:
        cfg = RiskConfig(daily_loss_limit_pct=0.02)
        portfolio = PortfolioSnapshot(equity=100_000.0, realised_pnl_today=-2_500.0)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig())
        assert not decision.approved
        assert "Daily loss" in decision.reason

    def test_daily_loss_below_limit_allows_entry(self) -> None:
        cfg = RiskConfig(daily_loss_limit_pct=0.02)
        portfolio = PortfolioSnapshot(equity=100_000.0, realised_pnl_today=-1_500.0)
        guard = RiskGuard(cfg, portfolio)
        assert guard.check(_sig()).approved

    def test_weekly_loss_limit_blocks_entry(self) -> None:
        cfg = RiskConfig(weekly_loss_limit_pct=0.05)
        portfolio = PortfolioSnapshot(equity=100_000.0, realised_pnl_this_week=-6_000.0)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig())
        assert not decision.approved
        assert "Weekly loss" in decision.reason

    def test_daily_limit_checked_before_weekly(self) -> None:
        cfg = RiskConfig(daily_loss_limit_pct=0.02, weekly_loss_limit_pct=0.05)
        portfolio = PortfolioSnapshot(
            equity=100_000.0,
            realised_pnl_today=-2_500.0,
            realised_pnl_this_week=-6_000.0,
        )
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig())
        assert "Daily loss" in decision.reason


# ---------------------------------------------------------------------------
# Position count limits
# ---------------------------------------------------------------------------

class TestPositionLimits:
    def test_max_open_positions_blocks_new_entry(self) -> None:
        cfg = RiskConfig(max_open_positions=2)
        def _pos(sym: str, price: float, qty: int) -> dict:
            return {
                "symbol": sym, "sector": "IT", "segment": "LARGE_CAP",
                "current_value": 50_000, "entry_price": price, "quantity": qty,
            }
        positions = [_pos("A", 100, 500), _pos("B", 200, 250)]
        portfolio = PortfolioSnapshot(equity=500_000.0, open_positions=positions)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig())
        assert not decision.approved
        assert "Max open positions" in decision.reason

    def test_max_new_per_day_blocks_third_entry(self) -> None:
        cfg = RiskConfig(max_new_positions_per_day=2)
        portfolio = PortfolioSnapshot(equity=1_000_000.0, new_entries_today=2)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig())
        assert not decision.approved
        assert "Max new entries" in decision.reason

    def test_sequential_approvals_respect_per_day_limit(self) -> None:
        cfg = RiskConfig(max_new_positions_per_day=2, max_open_positions=10)
        guard = _clean_guard(config=cfg)
        r1 = guard.check(_sig("A"))
        r2 = guard.check(_sig("B"))
        r3 = guard.check(_sig("C"))
        assert r1.approved
        assert r2.approved
        assert not r3.approved


# ---------------------------------------------------------------------------
# Stop / risk-per-trade checks
# ---------------------------------------------------------------------------

class TestStopChecks:
    def test_missing_stop_price_blocked(self) -> None:
        s = _sig()
        s.stop_price = None  # type: ignore[assignment]
        guard = _clean_guard()
        decision = guard.check(s)
        assert not decision.approved
        assert "stop price" in decision.reason.lower()

    def test_stop_above_entry_blocked(self) -> None:
        guard = _clean_guard()
        decision = guard.check(_sig(entry=100.0, stop=105.0))
        assert not decision.approved

    def test_stop_too_wide_blocked(self) -> None:
        # Entry 100, stop 80 → stop distance = 20% of entry.
        # Estimated risk = 20% × 10% position cap = 2% of equity > 1% limit.
        cfg = RiskConfig(max_risk_per_trade_pct=0.01, max_position_size_pct=0.10)
        guard = _clean_guard(config=cfg)
        decision = guard.check(_sig(entry=100.0, stop=80.0))
        assert not decision.approved
        assert "Stop too wide" in decision.reason

    def test_tight_stop_approved(self) -> None:
        # Entry 100, stop 99 → 1% distance → 1% × 10% = 0.1% risk — well within limit.
        cfg = RiskConfig(max_risk_per_trade_pct=0.01, max_position_size_pct=0.10)
        guard = _clean_guard(config=cfg)
        assert guard.check(_sig(entry=100.0, stop=99.0)).approved


# ---------------------------------------------------------------------------
# Score gate
# ---------------------------------------------------------------------------

class TestScoreGate:
    def test_low_score_blocked(self) -> None:
        cfg = RiskConfig(min_signal_score=50.0)
        guard = _clean_guard(config=cfg)
        decision = guard.check(_sig(score=35.0))
        assert not decision.approved
        assert "Score" in decision.reason

    def test_exact_min_score_approved(self) -> None:
        cfg = RiskConfig(min_signal_score=50.0)
        guard = _clean_guard(config=cfg)
        assert guard.check(_sig(score=50.0)).approved

    def test_score_gate_disabled_when_zero(self) -> None:
        cfg = RiskConfig(min_signal_score=0.0)
        guard = _clean_guard(config=cfg)
        assert guard.check(_sig(score=5.0)).approved


# ---------------------------------------------------------------------------
# Sector / segment caps
# ---------------------------------------------------------------------------

class TestExposureCaps:
    def test_sector_cap_blocks_overconcentration(self) -> None:
        cfg = RiskConfig(max_sector_exposure_pct=0.30, max_position_size_pct=0.10)
        # Already 25% of equity in "IT" → adding 10% would take it to 35% > 30%
        positions = [
            {"symbol": "INFY", "sector": "IT", "segment": "LARGE_CAP",
             "current_value": 250_000, "entry_price": 1000, "quantity": 250},
        ]
        portfolio = PortfolioSnapshot(equity=1_000_000.0, open_positions=positions)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig(), sector="IT", segment="LARGE_CAP")
        assert not decision.approved
        assert "IT" in decision.reason

    def test_sector_cap_different_sector_approved(self) -> None:
        cfg = RiskConfig(max_sector_exposure_pct=0.30, max_position_size_pct=0.10)
        positions = [
            {"symbol": "INFY", "sector": "IT", "segment": "LARGE_CAP",
             "current_value": 250_000, "entry_price": 1000, "quantity": 250},
        ]
        portfolio = PortfolioSnapshot(equity=1_000_000.0, open_positions=positions)
        guard = RiskGuard(cfg, portfolio)
        assert guard.check(_sig(), sector="PHARMA", segment="LARGE_CAP").approved

    def test_segment_cap_blocks_overconcentration(self) -> None:
        cfg = RiskConfig(max_segment_exposure_pct=0.50, max_position_size_pct=0.10)
        # 45% in SMALL_CAP across 5 *different* sectors → no sector cap triggered.
        # Adding 10% would take SMALL_CAP to 55% > 50% segment limit.
        sectors = ["PHARMA", "FMCG", "METAL", "AUTO", "REALTY"]
        positions = [
            {"symbol": f"S{i}", "sector": sectors[i], "segment": "SMALL_CAP",
             "current_value": 90_000, "entry_price": 100, "quantity": 900}
            for i in range(5)
        ]
        portfolio = PortfolioSnapshot(equity=1_000_000.0, open_positions=positions)
        guard = RiskGuard(cfg, portfolio)
        decision = guard.check(_sig(), sector="INFRA", segment="SMALL_CAP")
        assert not decision.approved
        assert "SMALL_CAP" in decision.reason

    def test_sequential_approvals_update_sector_tally(self) -> None:
        cfg = RiskConfig(
            max_sector_exposure_pct=0.25,
            max_position_size_pct=0.10,
            max_open_positions=10,
            max_new_positions_per_day=10,
        )
        guard = _clean_guard(equity=1_000_000.0, config=cfg)
        r1 = guard.check(_sig("A"), sector="IT", segment="LARGE_CAP")
        r2 = guard.check(_sig("B"), sector="IT", segment="LARGE_CAP")
        r3 = guard.check(_sig("C"), sector="IT", segment="LARGE_CAP")
        assert r1.approved
        assert r2.approved
        # Third IT position would push to 30% > 25% limit
        assert not r3.approved


# ---------------------------------------------------------------------------
# build_portfolio_snapshot helper
# ---------------------------------------------------------------------------

class TestBuildPortfolioSnapshot:
    def test_builds_from_trade_list(self) -> None:
        @dataclass
        class _Trade:
            symbol: str
            entry_price: float
            remaining_quantity: float

        trades = [_Trade("INFY", 1500.0, 100), _Trade("RELIANCE", 2800.0, 50)]
        symbol_to_sector = {"INFY": "IT", "RELIANCE": "ENERGY"}
        symbol_to_segment = {"INFY": "LARGE_CAP", "RELIANCE": "LARGE_CAP"}
        current_prices = {"INFY": 1600.0, "RELIANCE": 2900.0}

        snap = build_portfolio_snapshot(
            open_trades=trades,
            symbol_to_sector=symbol_to_sector,
            symbol_to_segment=symbol_to_segment,
            current_prices=current_prices,
            equity=500_000.0,
        )

        assert snap.equity == 500_000.0
        assert len(snap.open_positions) == 2
        infy_pos = next(p for p in snap.open_positions if p["symbol"] == "INFY")
        assert infy_pos["sector"] == "IT"
        assert infy_pos["current_value"] == 1600.0 * 100

    def test_unknown_symbol_defaults_gracefully(self) -> None:
        @dataclass
        class _Trade:
            symbol: str
            entry_price: float
            remaining_quantity: float

        trades = [_Trade("UNKNOWN", 100.0, 10)]
        snap = build_portfolio_snapshot(
            open_trades=trades,
            symbol_to_sector={},
            symbol_to_segment={},
            current_prices={},
            equity=100_000.0,
        )
        assert snap.open_positions[0]["sector"] == "UNKNOWN"
        assert snap.open_positions[0]["segment"] == "UNKNOWN"
