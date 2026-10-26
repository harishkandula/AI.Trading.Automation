"""Tests for S00, S01, S02, S03 strategies."""
from __future__ import annotations

from datetime import datetime, timezone

from app.models.candle import Candle, Timeframe
import app.strategies.s00_benchmark  # noqa: F401 — register
import app.strategies.s01_rs_momentum  # noqa: F401 — register
import app.strategies.s02_vcp  # noqa: F401 — register
import app.strategies.s03_trend_pullback  # noqa: F401 — register
from app.strategies.registry import get


def _make_candle(i: int, close: float, volume: float = 1_000_000.0) -> Candle:
    ts = datetime(2023, 1, 1, 9, 15, tzinfo=timezone.utc)
    from datetime import timedelta
    ts = ts + timedelta(days=i)
    high = close * 1.01
    low = close * 0.99
    return Candle(
        instrument_id="TEST",
        exchange="NSE",
        trading_symbol="TEST",
        timeframe=Timeframe.D1,
        timestamp=ts,
        open=close * 0.995,
        high=high,
        low=low,
        close=close,
        volume=volume,
        source="test",
    )


def _trending_candles(n: int = 250, base: float = 100.0, step: float = 0.3) -> list[Candle]:
    """Steady uptrend — suitable for all trend-following strategies."""
    return [_make_candle(i, base + i * step, 1_000_000 + i * 1000) for i in range(n)]


def _flat_candles(n: int = 250, price: float = 100.0) -> list[Candle]:
    return [_make_candle(i, price, 500_000) for i in range(n)]


# ── S00 Benchmark ────────────────────────────────────────────────────────────

class TestS00Benchmark:
    def test_registers(self):
        cls = get("s00_benchmark")
        assert cls.strategy_id == "s00_benchmark"

    def test_no_signals_on_flat_market(self):
        strategy = get("s00_benchmark")()
        candles = _flat_candles(200)
        signals = strategy.generate_signals("FLAT", candles)
        assert signals == []

    def test_returns_list(self):
        strategy = get("s00_benchmark")()
        candles = _trending_candles(200)
        result = strategy.generate_signals("TREND", candles)
        assert isinstance(result, list)

    def test_signals_have_stop_price(self):
        strategy = get("s00_benchmark")()
        candles = _trending_candles(200)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            assert sig.stop_price is not None
            assert sig.stop_price < sig.entry_reference_price

    def test_insufficient_data_returns_empty(self):
        strategy = get("s00_benchmark")()
        signals = strategy.generate_signals("X", _trending_candles(20))
        assert signals == []

    def test_entry_timestamp_is_next_day(self):
        strategy = get("s00_benchmark")()
        candles = _trending_candles(200)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            assert sig.planned_entry_timestamp is not None
            assert sig.planned_entry_timestamp > sig.signal_timestamp

    def test_no_lookahead_at_as_of_index(self):
        strategy = get("s00_benchmark")()
        candles = _trending_candles(200)
        sigs_full = strategy.generate_signals("T", candles, as_of_index=199)
        sigs_partial = strategy.generate_signals("T", candles, as_of_index=150)
        # Partial run must not see signals that require future bars
        full_ts = {s.signal_timestamp for s in sigs_full}
        partial_ts = {s.signal_timestamp for s in sigs_partial}
        for ts in partial_ts:
            assert ts in full_ts


# ── S01 RS Momentum ──────────────────────────────────────────────────────────

class TestS01RSMomentum:
    def test_registers(self):
        cls = get("s01_rs_momentum")
        assert cls.strategy_id == "s01_rs_momentum"

    def test_no_signals_flat_market(self):
        strategy = get("s01_rs_momentum")()
        signals = strategy.generate_signals("FLAT", _flat_candles(200))
        assert signals == []

    def test_signals_require_trend_structure(self):
        strategy = get("s01_rs_momentum")()
        # Downtrend — price below EMAs
        candles = [_make_candle(i, 200.0 - i * 0.3) for i in range(200)]
        signals = strategy.generate_signals("DOWN", candles)
        assert signals == []

    def test_no_fixed_target(self):
        """S01 uses trailing exit, not fixed target."""
        strategy = get("s01_rs_momentum")()
        candles = _trending_candles(200)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            assert sig.target_prices == []

    def test_stop_below_entry(self):
        strategy = get("s01_rs_momentum")()
        candles = _trending_candles(200)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            if sig.stop_price:
                assert sig.stop_price < sig.entry_reference_price


# ── S02 VCP ───────────────────────────────────────────────────────────────────

class TestS02VCP:
    def test_registers(self):
        cls = get("s02_vcp")
        assert cls.strategy_id == "s02_vcp"

    def test_no_signals_flat(self):
        strategy = get("s02_vcp")()
        signals = strategy.generate_signals("FLAT", _flat_candles(250))
        assert signals == []

    def test_returns_list(self):
        strategy = get("s02_vcp")()
        candles = _trending_candles(250)
        result = strategy.generate_signals("TREND", candles)
        assert isinstance(result, list)

    def test_two_targets_when_signal(self):
        strategy = get("s02_vcp")()
        candles = _trending_candles(250)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            assert len(sig.target_prices) == 2
            assert sig.target_prices[1] > sig.target_prices[0]

    def test_insufficient_data(self):
        strategy = get("s02_vcp")()
        signals = strategy.generate_signals("X", _trending_candles(50))
        assert signals == []


# ── S03 Trend Pullback ───────────────────────────────────────────────────────

class TestS03TrendPullback:
    def test_registers(self):
        cls = get("s03_trend_pullback")
        assert cls.strategy_id == "s03_trend_pullback"

    def test_no_signals_flat(self):
        strategy = get("s03_trend_pullback")()
        signals = strategy.generate_signals("FLAT", _flat_candles(200))
        assert signals == []

    def test_no_signals_downtrend(self):
        strategy = get("s03_trend_pullback")()
        candles = [_make_candle(i, 200.0 - i * 0.4) for i in range(200)]
        signals = strategy.generate_signals("DOWN", candles)
        assert signals == []

    def test_stop_above_ema50_proxy(self):
        strategy = get("s03_trend_pullback")()
        candles = _trending_candles(200)
        signals = strategy.generate_signals("TREND", candles)
        for sig in signals:
            assert sig.stop_price is not None
            assert sig.stop_price < sig.entry_reference_price

    def test_insufficient_data(self):
        strategy = get("s03_trend_pullback")()
        signals = strategy.generate_signals("X", _trending_candles(30))
        assert signals == []
