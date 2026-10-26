"""Tests for the walk-forward validation module."""
from __future__ import annotations

import sys
from datetime import date, datetime, timezone
from typing import Any

sys.path.insert(0, "src")

import app.strategies.s00_benchmark  # noqa: F401 — register
import app.strategies.s01_rs_momentum  # noqa: F401 — register
import app.strategies.s02_vcp  # noqa: F401 — register
import app.strategies.s03_trend_pullback  # noqa: F401 — register

from app.backtesting.walk_forward import (
    WalkForwardResult,
    WalkForwardWindow,
    _add_months,
    _aggregate,
    _build_windows,
    run_walk_forward,
)
from app.models.candle import Candle, Timeframe


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_candle(symbol: str, i: int, price: float = 100.0) -> Candle:
    from datetime import timedelta
    d = date(2019, 1, 2) + timedelta(days=i)
    ts = datetime(d.year, d.month, d.day, 9, 15, tzinfo=timezone.utc)
    return Candle(
        instrument_id=symbol,
        exchange="NSE",
        trading_symbol=symbol,
        timeframe=Timeframe.D1,
        timestamp=ts,
        open=price * 0.995,
        high=price * 1.01,
        low=price * 0.99,
        close=price,
        volume=1_000_000.0 + i * 500,
        source="test",
    )


def _trending_candles(symbol: str, n: int = 1500, base: float = 100.0) -> list[Candle]:
    """Steady uptrend long enough for multi-year walk-forward (≈6 calendar years)."""
    return [_make_candle(symbol, i, base + i * 0.05) for i in range(n)]


# ---------------------------------------------------------------------------
# Unit tests for helpers
# ---------------------------------------------------------------------------

class TestAddMonths:
    def test_basic(self) -> None:
        assert _add_months(date(2023, 1, 15), 6) == date(2023, 7, 15)

    def test_year_rollover(self) -> None:
        assert _add_months(date(2023, 11, 1), 3) == date(2024, 2, 1)

    def test_month_end_clamped(self) -> None:
        # Jan 31 + 1 month = Feb 28 (non-leap) or Feb 29 (leap)
        result = _add_months(date(2023, 1, 31), 1)
        assert result == date(2023, 2, 28)


class TestBuildWindows:
    def test_single_window(self) -> None:
        windows = _build_windows(date(2023, 1, 1), date(2023, 6, 30), step_months=6)
        assert len(windows) == 1
        assert windows[0][0] == date(2023, 1, 1)
        assert windows[0][1] == date(2023, 6, 30)

    def test_two_windows(self) -> None:
        windows = _build_windows(date(2023, 1, 1), date(2023, 12, 31), step_months=6)
        assert len(windows) == 2
        assert windows[0][0] == date(2023, 1, 1)
        assert windows[1][0] == date(2023, 7, 1)

    def test_no_windows_if_start_gte_end(self) -> None:
        windows = _build_windows(date(2023, 7, 1), date(2023, 6, 30), step_months=6)
        assert windows == []

    def test_last_window_capped_at_test_end(self) -> None:
        windows = _build_windows(date(2023, 1, 1), date(2023, 8, 15), step_months=6)
        assert windows[-1][1] == date(2023, 8, 15)


class TestAggregate:
    def _window(self, metrics: dict[str, Any], wid: int = 1) -> WalkForwardWindow:
        return WalkForwardWindow(
            window_id=wid,
            train_start=date(2019, 1, 1),
            train_end=date(2022, 12, 31),
            test_start=date(2023, 1, 1),
            test_end=date(2023, 6, 30),
            metrics=metrics,
            trade_count=5,
            signal_count=10,
        )

    def test_empty_windows_returns_error(self) -> None:
        result = _aggregate([])
        assert "error" in result

    def test_all_profitable(self) -> None:
        windows = [
            self._window({"sharpe_ratio": 1.5, "win_rate_pct": 60.0, "net_return_pct": 12.0,
                          "max_drawdown_pct": 8.0, "profit_factor": 2.0}, wid=i)
            for i in range(1, 5)
        ]
        agg = _aggregate(windows)
        assert agg["consistency_score"] == 1.0
        assert agg["windows_profitable"] == 4
        assert agg["validated"] is True

    def test_error_windows_counted(self) -> None:
        windows = [
            self._window({"error": "No signals in test window"}, wid=1),
            self._window({"sharpe_ratio": 1.0, "win_rate_pct": 55.0, "net_return_pct": 5.0,
                          "max_drawdown_pct": 10.0, "profit_factor": 1.5}, wid=2),
        ]
        agg = _aggregate(windows)
        assert agg["windows_count"] == 2
        assert agg["windows_with_data"] == 1

    def test_negative_sharpe_not_validated(self) -> None:
        windows = [
            self._window({"sharpe_ratio": -0.5, "win_rate_pct": 30.0, "net_return_pct": -5.0,
                          "max_drawdown_pct": 25.0, "profit_factor": 0.8}, wid=i)
            for i in range(1, 4)
        ]
        agg = _aggregate(windows)
        assert agg["validated"] is False


# ---------------------------------------------------------------------------
# Integration test for run_walk_forward
# ---------------------------------------------------------------------------

class TestRunWalkForward:
    def test_returns_walk_forward_result(self) -> None:
        candles = _trending_candles("TESTCO", n=1500)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"TESTCO": candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            initial_capital=100_000.0,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        assert isinstance(result, WalkForwardResult)
        assert result.strategy_id == "s00_benchmark"
        assert len(result.windows) >= 1

    def test_all_window_fields_present(self) -> None:
        candles = _trending_candles("TESTCO", n=1500)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"TESTCO": candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        for w in result.windows:
            assert w.train_start == date(2019, 1, 1)
            assert w.test_start < w.test_end
            assert isinstance(w.metrics, dict)
            assert w.signal_count >= 0
            assert w.trade_count >= 0

    def test_test_windows_dont_overlap(self) -> None:
        candles = _trending_candles("TESTCO", n=1500)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"TESTCO": candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        for i in range(len(result.windows) - 1):
            assert result.windows[i].test_end < result.windows[i + 1].test_start

    def test_provenance_fields_populated(self) -> None:
        candles = _trending_candles("TESTCO", n=1500)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"TESTCO": candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        assert result.provenance["run_date"] == "2025-01-05"
        assert result.provenance["cost_model"] == "zero_cost"
        assert "parameter_hash" in result.provenance
        assert result.provenance["universe_size"] == 1

    def test_aggregated_has_required_keys(self) -> None:
        candles = _trending_candles("TESTCO", n=1500)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"TESTCO": candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        required = {
            "windows_count", "windows_profitable", "consistency_score",
            "avg_sharpe", "avg_net_return_pct", "avg_max_drawdown_pct",
            "total_oos_trades", "total_oos_signals", "validated",
        }
        for key in required:
            assert key in result.aggregated, f"Missing key: {key}"

    def test_invalid_test_start_raises(self) -> None:
        import pytest
        candles = _trending_candles("TESTCO", n=1500)
        with pytest.raises(ValueError, match="initial_test_start"):
            run_walk_forward(
                strategy_id="s00_benchmark",
                candles_by_symbol={"TESTCO": candles},
                train_anchor_start=date(2023, 6, 1),
                initial_test_start=date(2023, 1, 1),
                test_end=date(2024, 12, 31),
                step_months=6,
                cost_profile="zero_cost",
                run_date=date(2025, 1, 5),
            )

    def test_insufficient_candles_symbol_skipped(self) -> None:
        short_candles = _trending_candles("SHORT", n=10)
        result = run_walk_forward(
            strategy_id="s00_benchmark",
            candles_by_symbol={"SHORT": short_candles},
            train_anchor_start=date(2019, 1, 1),
            initial_test_start=date(2023, 1, 1),
            test_end=date(2024, 12, 31),
            step_months=6,
            cost_profile="zero_cost",
            run_date=date(2025, 1, 5),
        )
        # All windows will have no signals (symbol was skipped)
        assert isinstance(result, WalkForwardResult)
        for w in result.windows:
            assert w.signal_count == 0
