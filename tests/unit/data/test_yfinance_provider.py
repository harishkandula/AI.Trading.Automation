"""Unit tests for YFinanceProvider — all yfinance calls are mocked."""
from __future__ import annotations

from datetime import date, datetime
from unittest.mock import MagicMock, patch
from zoneinfo import ZoneInfo

import pandas as pd
import pytest

from app.data.providers.yfinance_provider import YFinanceProvider
from app.data.universe import NIFTY50_SYMBOLS
from app.models.candle import Timeframe

IST = ZoneInfo("Asia/Kolkata")
UTC = ZoneInfo("UTC")

# Patch target: the `yf` name living inside the provider module.
_YF_TARGET = "app.data.providers.yfinance_provider.yf"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv_df(rows: list[dict]) -> pd.DataFrame:
    """Build a minimal yfinance-style DataFrame from a list of row dicts."""
    records = [
        {
            "Open": r["open"],
            "High": r["high"],
            "Low": r["low"],
            "Close": r["close"],
            "Volume": r["volume"],
        }
        for r in rows
    ]
    ts_index = pd.to_datetime([r["ts"] for r in rows], utc=True)
    return pd.DataFrame(records, index=ts_index)


def _make_yf_mock(df: pd.DataFrame) -> MagicMock:
    """Return a MagicMock that behaves like the yfinance module (Ticker.history API)."""
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = df
    mock_yf = MagicMock()
    mock_yf.Ticker.return_value = mock_ticker
    return mock_yf


# ---------------------------------------------------------------------------
# get_instruments()
# ---------------------------------------------------------------------------

class TestGetInstruments:
    def test_returns_fifty_instruments(self):
        provider = YFinanceProvider()
        instruments = provider.get_instruments()
        assert len(instruments) == 50

    def test_all_instruments_are_nse(self):
        provider = YFinanceProvider()
        for inst in provider.get_instruments():
            assert inst.exchange == "NSE"

    def test_instrument_ids_match_nifty50(self):
        provider = YFinanceProvider()
        inst_ids = {i.instrument_id for i in provider.get_instruments()}
        expected_ids = {sym.split(".")[0] for sym in NIFTY50_SYMBOLS}
        assert inst_ids == expected_ids

    def test_trading_symbols_have_ns_suffix(self):
        provider = YFinanceProvider()
        for inst in provider.get_instruments():
            assert inst.trading_symbol.endswith(".NS"), (
                f"{inst.trading_symbol} missing .NS suffix"
            )


# ---------------------------------------------------------------------------
# get_historical_candles()
# ---------------------------------------------------------------------------

_SAMPLE_ROW = {
    "ts": "2024-01-15T00:00:00+00:00",
    "open": 2400.0,
    "high": 2450.0,
    "low": 2380.0,
    "close": 2430.0,
    "volume": 1_200_000.0,
}


class TestGetHistoricalCandles:
    def test_maps_columns_to_candle_fields(self):
        df = _make_ohlcv_df([_SAMPLE_ROW])
        mock_yf = _make_yf_mock(df)

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "RELIANCE.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 16),
            )

        assert len(candles) == 1
        c = candles[0]
        assert c.open == pytest.approx(2400.0)
        assert c.high == pytest.approx(2450.0)
        assert c.low == pytest.approx(2380.0)
        assert c.close == pytest.approx(2430.0)
        assert c.volume == pytest.approx(1_200_000.0)
        assert c.source == "yfinance"
        assert c.timeframe == Timeframe.D1
        assert c.trading_symbol == "RELIANCE.NS"
        assert c.instrument_id == "RELIANCE"
        assert c.exchange == "NSE"

    def test_timestamp_converted_to_ist(self):
        df = _make_ohlcv_df([_SAMPLE_ROW])
        mock_yf = _make_yf_mock(df)

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "RELIANCE.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 16),
            )

        ts = candles[0].timestamp
        assert ts.tzinfo is not None
        # 2024-01-15 UTC 00:00 → 2024-01-15 05:30 IST
        ts_ist = ts.astimezone(IST)
        assert ts_ist.hour == 5
        assert ts_ist.minute == 30
        assert ts_ist.tzname() == "IST"

    def test_returns_empty_list_on_yfinance_error(self):
        mock_yf = MagicMock()
        mock_yf.Ticker.side_effect = RuntimeError("network error")

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "RELIANCE.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 16),
            )
        assert candles == []

    def test_returns_empty_on_empty_df(self):
        empty_df = pd.DataFrame(columns=["Open", "High", "Low", "Close", "Volume"])
        mock_yf = _make_yf_mock(empty_df)

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "RELIANCE.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 16),
            )
        assert candles == []

    def test_skips_rows_with_zero_price(self):
        bad_row = {**_SAMPLE_ROW, "open": 0.0, "close": 0.0, "high": 0.0, "low": 0.0}
        df = _make_ohlcv_df([bad_row])
        mock_yf = _make_yf_mock(df)

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "RELIANCE.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 16),
            )
        assert candles == []

    def test_multiple_rows_all_converted(self):
        rows = [
            {
                "ts": f"2024-01-{day:02d}T00:00:00+00:00",
                "open": 100.0 + day,
                "high": 110.0 + day,
                "low": 90.0 + day,
                "close": 105.0 + day,
                "volume": 500_000.0,
            }
            for day in range(15, 20)
        ]
        df = _make_ohlcv_df(rows)
        mock_yf = _make_yf_mock(df)

        with patch(_YF_TARGET, mock_yf):
            provider = YFinanceProvider()
            candles = provider.get_historical_candles(
                "TCS.NS",
                Timeframe.D1,
                date(2024, 1, 15),
                date(2024, 1, 19),
            )
        assert len(candles) == 5


# ---------------------------------------------------------------------------
# get_market_status()
# ---------------------------------------------------------------------------

class TestGetMarketStatus:
    """Test NSE open/closed logic against mocked datetime.now."""

    def test_open_during_session_on_weekday(self):
        # Monday 10:00 IST
        dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=IST)  # Monday
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["is_open"] is True

    def test_closed_before_session_on_weekday(self):
        # Monday 08:00 IST
        dt = datetime(2024, 1, 15, 8, 0, 0, tzinfo=IST)
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["is_open"] is False

    def test_closed_after_session_on_weekday(self):
        # Monday 16:00 IST
        dt = datetime(2024, 1, 15, 16, 0, 0, tzinfo=IST)
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["is_open"] is False

    def test_closed_on_saturday(self):
        # Saturday 11:00 IST
        dt = datetime(2024, 1, 20, 11, 0, 0, tzinfo=IST)  # Saturday
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["is_open"] is False

    def test_closed_on_sunday(self):
        # Sunday 12:00 IST
        dt = datetime(2024, 1, 21, 12, 0, 0, tzinfo=IST)  # Sunday
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["is_open"] is False

    def test_status_contains_exchange_key(self):
        dt = datetime(2024, 1, 15, 10, 0, 0, tzinfo=IST)
        provider = YFinanceProvider()
        with patch("app.data.providers.yfinance_provider.datetime") as mock_dt:
            mock_dt.now.return_value = dt
            status = provider.get_market_status()
        assert status["exchange"] == "NSE"
        assert "timestamp" in status
