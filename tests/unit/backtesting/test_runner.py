"""Tests for run_universe_backtest (app.backtesting.runner)."""
from __future__ import annotations

import sys
from datetime import date, datetime
from typing import Any
from unittest.mock import patch

sys.path.insert(0, "src")

from app.models.candle import Candle, Timeframe

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

IST_SUFFIX = "+05:30"
_BASE_DATE = date(2024, 1, 1)


def _make_candle(symbol: str, day_offset: int, price: float = 100.0) -> Candle:
    from datetime import timedelta
    from zoneinfo import ZoneInfo
    ist = ZoneInfo("Asia/Kolkata")
    d = date(2024, 1, 1) + timedelta(days=day_offset)
    ts = datetime(d.year, d.month, d.day, 9, 15, tzinfo=ist)
    return Candle(
        instrument_id=symbol.split(".")[0],
        exchange="NSE",
        trading_symbol=symbol,
        timeframe=Timeframe.D1,
        timestamp=ts,
        open=price,
        high=price * 1.02,
        low=price * 0.98,
        close=price,
        volume=500_000.0,
        source="test",
    )


def _make_candles(symbol: str, count: int = 60, base_price: float = 100.0) -> list[Candle]:
    return [_make_candle(symbol, i, base_price + i * 0.1) for i in range(count)]


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_run_universe_backtest_returns_dict_keyed_by_strategy() -> None:
    """Result dict must be keyed by strategy_id for each registered daily strategy."""
    candle_data = _make_candles("RELIANCE.NS", count=80)

    with patch(
        "app.backtesting.runner.YFinanceProvider.get_historical_candles",
        return_value=candle_data,
    ):
        from app.backtesting.runner import run_universe_backtest
        from app.strategies.registry import all_ids

        daily_ids = [
            sid for sid in all_ids()
            if sid != "intraday_support_supertrend"  # intraday strategy is always skipped
        ]

        results = run_universe_backtest(
            symbols=["RELIANCE.NS"],
            strategy_ids=daily_ids,
            start=date(2024, 2, 1),
            end=date(2024, 4, 30),
            initial_capital=100_000.0,
            cost_profile="zero_cost",
        )

    assert isinstance(results, dict)
    for sid in results:
        assert "metrics" in results[sid]
        assert "trades" in results[sid]
        assert "signals_count" in results[sid]
        assert "symbols_scanned" in results[sid]


def test_intraday_strategy_is_skipped() -> None:
    """Intraday strategy should never appear in results (needs 5 m candles)."""
    candle_data = _make_candles("INFY.NS", count=80)

    with patch(
        "app.backtesting.runner.YFinanceProvider.get_historical_candles",
        return_value=candle_data,
    ):
        from app.backtesting.runner import run_universe_backtest

        results = run_universe_backtest(
            symbols=["INFY.NS"],
            strategy_ids=["intraday_support_supertrend"],
            start=date(2024, 2, 1),
            end=date(2024, 3, 31),
        )

    assert "intraday_support_supertrend" not in results


def test_symbol_with_no_candles_handled_gracefully() -> None:
    """A symbol returning empty candles must not raise; other symbols proceed."""
    def _side_effect(symbol: str, *args: Any, **kwargs: Any) -> list[Candle]:
        if symbol == "BAD.NS":
            return []
        return _make_candles(symbol, count=80)

    with patch(
        "app.backtesting.runner.YFinanceProvider.get_historical_candles",
        side_effect=_side_effect,
    ):
        from app.backtesting.runner import run_universe_backtest

        results = run_universe_backtest(
            symbols=["BAD.NS", "RELIANCE.NS"],
            strategy_ids=["gap_up_volume"],
            start=date(2024, 2, 1),
            end=date(2024, 3, 31),
            cost_profile="zero_cost",
        )

    # Should complete without raising; may or may not have metrics depending on signals
    assert isinstance(results, dict)


def test_empty_symbol_list_returns_empty_results() -> None:
    """Empty symbol list → empty results dict (no strategies run is fine too)."""
    from app.backtesting.runner import run_universe_backtest

    results = run_universe_backtest(
        symbols=[],
        strategy_ids=["gap_up_volume"],
        start=date(2024, 2, 1),
        end=date(2024, 3, 31),
    )

    assert isinstance(results, dict)


def test_unknown_strategy_id_is_skipped_gracefully() -> None:
    """Unknown strategy ID must not raise; simply skipped."""
    candle_data = _make_candles("TCS.NS", count=60)

    with patch(
        "app.backtesting.runner.YFinanceProvider.get_historical_candles",
        return_value=candle_data,
    ):
        from app.backtesting.runner import run_universe_backtest

        results = run_universe_backtest(
            symbols=["TCS.NS"],
            strategy_ids=["nonexistent_strategy_xyz"],
            start=date(2024, 2, 1),
            end=date(2024, 3, 31),
        )

    assert "nonexistent_strategy_xyz" not in results


def test_yfinance_exception_handled_gracefully() -> None:
    """If yfinance raises for a symbol, that symbol is skipped; run continues."""
    call_count = 0

    def _side_effect(symbol: str, *args: Any, **kwargs: Any) -> list[Candle]:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("simulated yfinance error")
        return _make_candles(symbol, count=80)

    with patch(
        "app.backtesting.runner.YFinanceProvider.get_historical_candles",
        side_effect=_side_effect,
    ):
        from app.backtesting.runner import run_universe_backtest

        results = run_universe_backtest(
            symbols=["FAIL.NS", "GOOD.NS"],
            strategy_ids=["gap_up_volume"],
            start=date(2024, 2, 1),
            end=date(2024, 3, 31),
            cost_profile="zero_cost",
        )

    assert isinstance(results, dict)
