"""S3 Parquet data store — read/write OHLCV candles as Parquet files.

Layout:
  s3://{bucket}/ohlcv/daily/{SYMBOL}.parquet   — daily candles, one file per symbol
  s3://{bucket}/ohlcv/5min/{SYMBOL}.parquet    — 5-min candles (future)
  s3://{bucket}/indicators/{SYMBOL}.parquet    — pre-computed indicators

Each Parquet file is append-only (we read, union-deduplicate, rewrite).
Schema: date(str), open, high, low, close, volume, source, adjusted(bool)
"""
from __future__ import annotations

import io
import logging
from datetime import date
from typing import TYPE_CHECKING

import boto3
import pandas as pd

if TYPE_CHECKING:
    pass

log = logging.getLogger(__name__)

_DATE_COL = "date"
_REQUIRED_COLS = {_DATE_COL, "open", "high", "low", "close", "volume"}


def _s3_key(symbol: str, timeframe: str = "daily") -> str:
    return f"ohlcv/{timeframe}/{symbol}.parquet"


def _indicator_key(symbol: str) -> str:
    return f"indicators/{symbol}.parquet"


class ParquetStore:
    """Read/write OHLCV and indicator Parquet files on S3."""

    def __init__(self, bucket: str, region: str = "ap-south-1") -> None:
        self._bucket = bucket
        self._s3 = boto3.client("s3", region_name=region)

    # ------------------------------------------------------------------
    # OHLCV
    # ------------------------------------------------------------------

    def read_ohlcv(self, symbol: str, timeframe: str = "daily") -> pd.DataFrame:
        """Return DataFrame with columns: date, open, high, low, close, volume, source, adjusted.
        Returns empty DataFrame if the file does not exist yet.
        """
        key = _s3_key(symbol, timeframe)
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=key)
            buf = io.BytesIO(obj["Body"].read())
            df = pd.read_parquet(buf)
            df[_DATE_COL] = pd.to_datetime(df[_DATE_COL]).dt.date
            return df.sort_values(_DATE_COL).reset_index(drop=True)
        except self._s3.exceptions.NoSuchKey:
            return pd.DataFrame(columns=list(_REQUIRED_COLS) + ["source", "adjusted"])
        except Exception:
            log.warning("Failed to read parquet for %s (%s)", symbol, timeframe, exc_info=True)
            return pd.DataFrame(columns=list(_REQUIRED_COLS) + ["source", "adjusted"])

    def append_ohlcv(self, symbol: str, rows: list[dict], timeframe: str = "daily") -> int:
        """Append rows to the symbol's Parquet file. Deduplicates by date. Returns new row count."""
        if not rows:
            return 0
        existing = self.read_ohlcv(symbol, timeframe)
        new_df = pd.DataFrame(rows)
        new_df[_DATE_COL] = pd.to_datetime(new_df[_DATE_COL]).dt.date
        combined = pd.concat([existing, new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=[_DATE_COL], keep="last")
        combined = combined.sort_values(_DATE_COL).reset_index(drop=True)
        self._write_parquet(_s3_key(symbol, timeframe), combined)
        return len(combined)

    def read_ohlcv_range(
        self,
        symbol: str,
        start: date,
        end: date,
        timeframe: str = "daily",
    ) -> pd.DataFrame:
        """Return rows within [start, end] inclusive."""
        df = self.read_ohlcv(symbol, timeframe)
        if df.empty:
            return df
        mask = (df[_DATE_COL] >= start) & (df[_DATE_COL] <= end)
        return df[mask].reset_index(drop=True)

    def latest_date(self, symbol: str, timeframe: str = "daily") -> date | None:
        """Return the most recent date stored for a symbol, or None if not found."""
        df = self.read_ohlcv(symbol, timeframe)
        if df.empty:
            return None
        return df[_DATE_COL].max()

    # ------------------------------------------------------------------
    # Indicators
    # ------------------------------------------------------------------

    def write_indicators(self, symbol: str, df: pd.DataFrame) -> None:
        """Overwrite the indicator cache for a symbol."""
        self._write_parquet(_indicator_key(symbol), df)

    def read_indicators(self, symbol: str) -> pd.DataFrame:
        key = _indicator_key(symbol)
        try:
            obj = self._s3.get_object(Bucket=self._bucket, Key=key)
            buf = io.BytesIO(obj["Body"].read())
            df = pd.read_parquet(buf)
            df[_DATE_COL] = pd.to_datetime(df[_DATE_COL]).dt.date
            return df.sort_values(_DATE_COL).reset_index(drop=True)
        except self._s3.exceptions.NoSuchKey:
            return pd.DataFrame()
        except Exception:
            log.warning("Failed to read indicators for %s", symbol, exc_info=True)
            return pd.DataFrame()

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _write_parquet(self, key: str, df: pd.DataFrame) -> None:
        buf = io.BytesIO()
        df.to_parquet(buf, index=False, engine="pyarrow", compression="snappy")
        buf.seek(0)
        self._s3.put_object(Bucket=self._bucket, Key=key, Body=buf.getvalue())
        log.debug("Wrote %d rows to s3://%s/%s", len(df), self._bucket, key)
