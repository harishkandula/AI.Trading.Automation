"""NSE Bhavcopy provider — fetches official end-of-day CSV files from NSE.

NSE publishes a free bhavcopy CSV for every trading day at:
  https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip

Each row: SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, LAST, PREVCLOSE, TOTTRDQTY, TOTTRDVAL, TIMESTAMP
We only keep SERIES == "EQ" (equity cash segment).

This provider is used for:
  1. One-time historical backfill (2019–present) via the backfill Lambda.
  2. Daily append as fallback when Groww is unavailable.
"""
from __future__ import annotations

import io
import logging
import zipfile
from datetime import date, timedelta

import httpx
import pandas as pd

from app.core.time_utils import is_trading_day

log = logging.getLogger(__name__)

_BASE_URL = "https://archives.nseindia.com/content/historical/EQUITIES"
_TIMEOUT = 30
_MONTHS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept-Encoding": "gzip, deflate",
    "Accept": "*/*",
}


def _bhavcopy_url(d: date) -> str:
    mon = _MONTHS[d.month]
    day_str = f"{d.day:02d}"
    year_str = str(d.year)
    filename = f"cm{day_str}{mon}{year_str}bhav.csv.zip"
    return f"{_BASE_URL}/{year_str}/{mon}/{filename}"


def fetch_bhavcopy(d: date) -> pd.DataFrame:
    """Download and parse NSE bhavcopy for date *d*.

    Returns DataFrame with columns: symbol, date, open, high, low, close, volume.
    Only equity (SERIES == EQ) rows are returned.
    Returns empty DataFrame on any error.
    """
    url = _bhavcopy_url(d)
    try:
        resp = httpx.get(url, headers=_HEADERS, timeout=_TIMEOUT, follow_redirects=True)
        if resp.status_code == 404:
            log.debug("Bhavcopy not found for %s (possible holiday): %s", d, url)
            return pd.DataFrame()
        resp.raise_for_status()
    except Exception:
        log.warning("Failed to download bhavcopy for %s", d, exc_info=True)
        return pd.DataFrame()

    try:
        with zipfile.ZipFile(io.BytesIO(resp.content)) as zf:
            csv_name = zf.namelist()[0]
            with zf.open(csv_name) as f:
                df = pd.read_csv(f)
    except Exception:
        log.warning("Failed to parse bhavcopy zip for %s", d, exc_info=True)
        return pd.DataFrame()

    # Normalise column names (NSE columns have trailing spaces sometimes)
    df.columns = [c.strip().upper() for c in df.columns]

    eq = df[df.get("SERIES", pd.Series()) == "EQ"].copy() if "SERIES" in df.columns else df.copy()
    if eq.empty:
        return pd.DataFrame()

    # Map NSE bhavcopy columns to our schema
    col_map = {
        "SYMBOL": "symbol",
        "OPEN": "open",
        "HIGH": "high",
        "LOW": "low",
        "CLOSE": "close",
        "TOTTRDQTY": "volume",
    }
    missing = [c for c in col_map if c not in eq.columns]
    if missing:
        log.warning("Bhavcopy missing columns %s for %s", missing, d)
        return pd.DataFrame()

    result = eq[list(col_map.keys())].rename(columns=col_map).copy()
    result["date"] = d
    result["source"] = "nse_bhavcopy"
    result["adjusted"] = False
    for col in ("open", "high", "low", "close", "volume"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result = result.dropna(subset=["open", "high", "low", "close"])
    result = result[result["close"] > 0]
    return result.reset_index(drop=True)


def fetch_bhavcopy_range(start: date, end: date) -> pd.DataFrame:
    """Fetch bhavcopy for every trading day in [start, end] and concatenate.

    Skips weekends and known holidays automatically.
    """
    frames: list[pd.DataFrame] = []
    current = start
    while current <= end:
        if is_trading_day(current):
            df = fetch_bhavcopy(current)
            if not df.empty:
                frames.append(df)
        current += timedelta(days=1)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def trading_days_missing(
    symbol: str,
    start: date,
    end: date,
    stored_dates: set[date],
) -> list[date]:
    """Return trading days in [start, end] not present in stored_dates."""
    current = start
    missing: list[date] = []
    while current <= end:
        if is_trading_day(current) and current not in stored_dates:
            missing.append(current)
        current += timedelta(days=1)
    return missing
