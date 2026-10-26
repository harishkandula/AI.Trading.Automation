"""Hybrid data provider — reads S3 Parquet first, fills gaps from Groww or NSE Bhavcopy.

Read path (used by strategies and engines at scan time):
  1. Read from S3 Parquet (fast, no external API call)
  2. If data is missing for requested range, fall back to Groww API
  3. If Groww fails, fall back to NSE Bhavcopy
  4. Cache any new data back to S3

This is the provider the morning/evening handlers should use.
"""
from __future__ import annotations

import logging
import os
from datetime import date

import pandas as pd

from app.data.providers.base import MarketDataProvider
from app.data.store import ParquetStore
from app.models.candle import Candle, Timeframe
from app.models.instrument import Instrument

log = logging.getLogger(__name__)


def _store_from_env() -> ParquetStore:
    bucket = os.environ.get("DATA_BUCKET", "")
    if not bucket:
        raise RuntimeError("DATA_BUCKET environment variable is not set")
    return ParquetStore(bucket)


def _df_row_to_candle(row: pd.Series, symbol: str, timeframe: Timeframe) -> Candle | None:
    from datetime import datetime
    from zoneinfo import ZoneInfo

    IST = ZoneInfo("Asia/Kolkata")
    try:
        d = row["date"]
        ts = datetime(d.year, d.month, d.day, 15, 30, tzinfo=IST)
        return Candle(
            instrument_id=symbol,
            exchange="NSE",
            trading_symbol=symbol,
            timeframe=timeframe,
            timestamp=ts,
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=float(row["volume"]),
            source=str(row.get("source", "parquet")),
            adjusted=bool(row.get("adjusted", False)),
        )
    except Exception:
        return None


class HybridDataProvider(MarketDataProvider):
    """Reads from S3 Parquet; falls back to Groww then NSE Bhavcopy for gaps."""

    def __init__(self, store: ParquetStore | None = None) -> None:
        self._store = store or _store_from_env()

    def get_instruments(self) -> list[Instrument]:
        from app.data.nifty500_symbols import NIFTY500
        return [
            Instrument(
                instrument_id=m.symbol,
                trading_symbol=m.symbol,
                exchange="NSE",
                segment=m.segment,
                instrument_type="EQ",
                tradable=True,
            )
            for m in NIFTY500
        ]

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> list[Candle]:
        if timeframe != Timeframe.D1:
            log.warning("HybridDataProvider only supports D1 timeframe currently")
            return []

        bare = symbol.removesuffix(".NS")
        df = self._store.read_ohlcv_range(bare, start, end)

        # Check if we have all the data we need
        stored_dates: set[date] = set(df["date"].tolist()) if not df.empty else set()
        from app.data.providers.nse_bhavcopy import trading_days_missing
        missing = trading_days_missing(bare, start, end, stored_dates)

        if missing:
            log.info("Gap-filling %d days for %s from Groww/Bhavcopy", len(missing), bare)
            df = self._fill_gaps(bare, df, missing, start, end)

        if df.empty:
            return []

        candles: list[Candle] = []
        for _, row in df.iterrows():
            c = _df_row_to_candle(row, bare, timeframe)
            if c is not None:
                candles.append(c)
        return sorted(candles, key=lambda c: c.timestamp)

    def _fill_gaps(
        self,
        symbol: str,
        existing: pd.DataFrame,
        missing_days: list[date],
        start: date,
        end: date,
    ) -> pd.DataFrame:
        gap_start = min(missing_days)
        gap_end = max(missing_days)

        # Try Groww first
        new_rows = self._fetch_from_groww(symbol, gap_start, gap_end)

        # Fall back to NSE Bhavcopy
        if new_rows is None or new_rows.empty:
            new_rows = self._fetch_from_bhavcopy(symbol, gap_start, gap_end)

        if new_rows is not None and not new_rows.empty:
            self._store.append_ohlcv(symbol, new_rows.to_dict("records"))
            # Re-read from store to get clean deduplicated data
            return self._store.read_ohlcv_range(symbol, start, end)

        return existing

    def _fetch_from_groww(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        try:
            from app.data.providers.groww_provider import GrowwDataProvider
            from app.models.candle import Timeframe as TF

            provider = GrowwDataProvider()
            candles = provider.get_historical_candles(f"{symbol}.NS", TF.D1, start, end)
            if not candles:
                return None
            rows = [
                {
                    "date": c.timestamp.date(),
                    "open": c.open,
                    "high": c.high,
                    "low": c.low,
                    "close": c.close,
                    "volume": c.volume,
                    "source": "groww",
                    "adjusted": c.adjusted,
                }
                for c in candles
            ]
            return pd.DataFrame(rows)
        except Exception:
            log.debug("Groww fetch failed for %s, will try bhavcopy", symbol, exc_info=True)
            return None

    def _fetch_from_bhavcopy(self, symbol: str, start: date, end: date) -> pd.DataFrame | None:
        try:
            from app.data.providers.nse_bhavcopy import fetch_bhavcopy_range

            df = fetch_bhavcopy_range(start, end)
            if df.empty:
                return None
            sym_df = df[df["symbol"] == symbol].copy()
            return sym_df if not sym_df.empty else None
        except Exception:
            log.warning("Bhavcopy fallback failed for %s", symbol, exc_info=True)
            return None

    def get_live_quote(self, symbol: str) -> dict:
        try:
            from app.data.providers.groww_provider import GrowwDataProvider
            return GrowwDataProvider().get_live_quote(symbol)
        except Exception:
            return {}

    def get_market_status(self) -> dict:
        try:
            from app.data.providers.groww_provider import GrowwDataProvider
            return GrowwDataProvider().get_market_status()
        except Exception:
            return {}
