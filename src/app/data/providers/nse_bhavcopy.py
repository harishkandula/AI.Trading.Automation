"""NSE Bhavcopy provider — fetches official end-of-day CSV files from NSE.

NSE publishes free bhavcopy CSVs for every trading day. Two URL formats exist:

  Historical (pre-2024):
    https://archives.nseindia.com/content/historical/EQUITIES/{YYYY}/{MON}/cm{DD}{MON}{YYYY}bhav.csv.zip

  Recent (2023+, new CDN):
    https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{DDMMYYYY}.csv

We try the new CDN format first (works for recent dates), then fall back to the
old archive format (works for historical dates back to 1994).

NOTE: NSE blocks direct requests from AWS data-center IPs unless you establish
a session first. We do a GET on the NSE homepage to acquire cookies before
fetching any data file.
"""
from __future__ import annotations

import io
import logging
import time
import zipfile
from datetime import date, timedelta

import httpx
import pandas as pd

from app.core.time_utils import is_trading_day

log = logging.getLogger(__name__)


class BhavcopBlockedError(Exception):
    pass


_TIMEOUT = 30
_MONTHS = {
    1: "JAN", 2: "FEB", 3: "MAR", 4: "APR", 5: "MAY", 6: "JUN",
    7: "JUL", 8: "AUG", 9: "SEP", 10: "OCT", 11: "NOV", 12: "DEC",
}

# Headers that mimic a real browser — NSE requires Referer + Accept-Language
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

_SESSION_URL = "https://www.nseindia.com"
_COOKIE_CACHE: dict[str, str] = {}
_COOKIE_CACHE_TIME: list[float] = [0.0]
_COOKIE_TTL = 300  # seconds


def _get_session_client() -> httpx.Client:
    """Return an httpx client with NSE session cookies."""
    now = time.monotonic()
    if now - _COOKIE_CACHE_TIME[0] > _COOKIE_TTL or not _COOKIE_CACHE:
        try:
            with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as c:
                resp = c.get(_SESSION_URL)
                _COOKIE_CACHE.clear()
                _COOKIE_CACHE.update(dict(resp.cookies))
                _COOKIE_CACHE_TIME[0] = now
                log.debug("NSE session cookies acquired: %s", list(_COOKIE_CACHE.keys()))
        except Exception:
            log.warning("Failed to acquire NSE session cookies", exc_info=True)
    return httpx.Client(
        headers=_HEADERS,
        cookies=_COOKIE_CACHE,
        timeout=_TIMEOUT,
        follow_redirects=True,
    )


def _url_archive(d: date) -> str:
    """Old archive URL — works reliably for dates before 2024."""
    mon = _MONTHS[d.month]
    day_str = f"{d.day:02d}"
    year_str = str(d.year)
    filename = f"cm{day_str}{mon}{year_str}bhav.csv.zip"
    return f"https://archives.nseindia.com/content/historical/EQUITIES/{year_str}/{mon}/{filename}"


def _url_cdn(d: date) -> str:
    """New CDN URL — works for recent dates (2023+)."""
    return (
        f"https://nsearchives.nseindia.com/products/content/"
        f"sec_bhavdata_full_{d.strftime('%d%m%Y')}.csv"
    )


def _parse_archive_zip(content: bytes, d: date) -> pd.DataFrame:
    """Parse the old zip+CSV format."""
    with zipfile.ZipFile(io.BytesIO(content)) as zf:
        csv_files = [n for n in zf.namelist() if n.lower().endswith(".csv")]
        if not csv_files:
            raise ValueError(f"No CSV file found in bhavcopy zip: {zf.namelist()}")
        csv_name = csv_files[0]
        with zf.open(csv_name) as f:
            df = pd.read_csv(f)
    df.columns = [c.strip().upper() for c in df.columns]
    return _normalise(df, d)


def _parse_cdn_csv(content: bytes, d: date) -> pd.DataFrame:
    """Parse the new CDN flat CSV format.

    Columns: SYMBOL,SERIES,DATE1,PREV_CLOSE,OPEN_PRICE,HIGH_PRICE,LOW_PRICE,
             LAST_PRICE,CLOSE_PRICE,AVG_PRICE,TTL_TRD_QNTY,...
    """
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [c.strip().upper() for c in df.columns]
    # Rename to match our normaliser expectations
    rename: dict[str, str] = {}
    if "OPEN_PRICE" in df.columns:
        rename["OPEN_PRICE"] = "OPEN"
    if "HIGH_PRICE" in df.columns:
        rename["HIGH_PRICE"] = "HIGH"
    if "LOW_PRICE" in df.columns:
        rename["LOW_PRICE"] = "LOW"
    if "CLOSE_PRICE" in df.columns:
        rename["CLOSE_PRICE"] = "CLOSE"
    if "TTL_TRD_QNTY" in df.columns:
        rename["TTL_TRD_QNTY"] = "TOTTRDQTY"
    if rename:
        df = df.rename(columns=rename)
    return _normalise(df, d)


def _normalise(df: pd.DataFrame, d: date) -> pd.DataFrame:
    """Extract EQ rows and standardise to our schema."""
    if "SERIES" in df.columns:
        df = df[df["SERIES"].astype(str).str.strip() == "EQ"].copy()
    if df.empty:
        return pd.DataFrame()
    col_map = {
        "SYMBOL": "symbol",
        "OPEN": "open",
        "HIGH": "high",
        "LOW": "low",
        "CLOSE": "close",
        "TOTTRDQTY": "volume",
    }
    missing = [c for c in col_map if c not in df.columns]
    if missing:
        log.warning(
            "Bhavcopy missing columns %s for %s; available: %s", missing, d, list(df.columns)
        )
        return pd.DataFrame()
    result = df[list(col_map.keys())].rename(columns=col_map).copy()
    result["symbol"] = result["symbol"].astype(str).str.strip()
    result["date"] = d
    result["source"] = "nse_bhavcopy"
    result["adjusted"] = False
    for col in ("open", "high", "low", "close", "volume"):
        result[col] = pd.to_numeric(result[col], errors="coerce")
    result = result.dropna(subset=["open", "high", "low", "close"])
    result = result[result["close"] > 0]
    result = result[result["volume"] > 0]
    return result.reset_index(drop=True)


def fetch_bhavcopy(d: date) -> pd.DataFrame:
    """Download and parse NSE bhavcopy for date *d*.

    Tries new CDN format first, then falls back to archive zip format.
    Returns empty DataFrame on holiday, weekend, or any error.
    """
    client = _get_session_client()
    try:
        return _fetch_with_client(client, d)
    finally:
        client.close()


def _fetch_with_client(client: httpx.Client, d: date) -> pd.DataFrame:
    cdn_status = 0
    archive_status = 0

    # Try CDN (recent format) first
    cdn_url = _url_cdn(d)
    try:
        resp = client.get(cdn_url)
        cdn_status = resp.status_code
        if resp.status_code == 200 and len(resp.content) > 5000:
            df = _parse_cdn_csv(resp.content, d)
            if not df.empty and len(df) >= 100:
                log.debug("Bhavcopy CDN OK for %s (%d rows)", d, len(df))
                return df
            if not df.empty and len(df) < 100:
                log.debug("CDN response suspiciously small (%d rows) for %s, trying archive", len(df), d)
            else:
                log.debug("CDN response parsed empty for %s, trying archive", d)
        elif resp.status_code != 404:
            log.debug("CDN %s → HTTP %d, trying archive", d, resp.status_code)
    except Exception:
        log.debug("CDN fetch failed for %s, trying archive", d, exc_info=True)

    # Fall back to archive zip
    archive_url = _url_archive(d)
    try:
        resp = client.get(archive_url)
        archive_status = resp.status_code
        if resp.status_code == 404:
            log.debug("Bhavcopy not found for %s (holiday?): %s", d, archive_url)
            if cdn_status in (403, 429) or archive_status in (403, 429):
                raise BhavcopBlockedError(f"NSE blocked requests for {d}")
            return pd.DataFrame()
        resp.raise_for_status()
        df = _parse_archive_zip(resp.content, d)
        if not df.empty:
            log.debug("Bhavcopy archive OK for %s (%d rows)", d, len(df))
        return df
    except BhavcopBlockedError:
        raise
    except Exception:
        log.warning("Failed to fetch bhavcopy for %s from both CDN and archive", d, exc_info=True)

    if cdn_status in (403, 429) or archive_status in (403, 429):
        raise BhavcopBlockedError(f"NSE blocked requests for {d}")
    return pd.DataFrame()


def fetch_bhavcopy_range(start: date, end: date) -> pd.DataFrame:
    """Fetch bhavcopy for every trading day in [start, end] and concatenate."""
    frames: list[pd.DataFrame] = []
    client = _get_session_client()
    try:
        current = start
        while current <= end:
            if is_trading_day(current):
                df = _fetch_with_client(client, current)
                if not df.empty:
                    frames.append(df)
            current += timedelta(days=1)
    finally:
        client.close()
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
