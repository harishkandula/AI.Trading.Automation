"""Data pipeline Lambda handler.

Two modes triggered by event payload:

1. Daily append (triggered 04:00 IST Mon-Fri by EventBridge):
   event = {"mode": "daily"}
   Fetches previous trading day's bhavcopy for all NIFTY 500 symbols,
   appends to S3 Parquet, pre-computes indicators.

2. Historical backfill (triggered manually or via EventBridge once):
   event = {"mode": "backfill", "start": "2019-01-01", "end": "2024-12-31"}
   Fetches all bhavcopy files for the date range, loads into S3 Parquet.
   Runs in chunks to avoid Lambda timeout (15 min max).
   Use step_days=30 for manageable chunks; invoke repeatedly until done.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta

log = logging.getLogger(__name__)


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    from app.core.time_utils import IST, prev_trading_day  # noqa: PLC0415
    from datetime import datetime  # noqa: PLC0415

    if isinstance(event, dict) and event.get("_prewarm"):
        return {"statusCode": 200, "body": "warm"}

    event_dict: dict = event if isinstance(event, dict) else {}
    mode = event_dict.get("mode", "daily")

    if mode == "backfill":
        return _run_backfill(event_dict)
    else:
        target_date = prev_trading_day(datetime.now(tz=IST).date())
        return _run_daily(target_date)


def _run_daily(target_date: date) -> dict:
    """Fetch yesterday's bhavcopy and append to S3 Parquet for all NIFTY 500 symbols."""
    from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
    from app.data.providers.nse_bhavcopy import fetch_bhavcopy  # noqa: PLC0415
    from app.data.store import ParquetStore  # noqa: PLC0415

    bucket = os.environ["DATA_BUCKET"]
    store = ParquetStore(bucket)
    symbols = set(all_symbols())

    log.info("Daily pipeline: fetching bhavcopy for %s", target_date)
    df = fetch_bhavcopy(target_date)

    if df.empty:
        log.warning("No bhavcopy data for %s (holiday or fetch failed)", target_date)
        return {"statusCode": 200, "body": {"date": str(target_date), "rows": 0, "symbols": 0}}

    # Filter to our universe only
    df = df[df["symbol"].isin(symbols)]
    updated = 0
    for symbol, group in df.groupby("symbol"):
        rows = group.to_dict("records")
        store.append_ohlcv(str(symbol), rows)
        updated += 1

    # Pre-compute indicators after data is fresh
    _compute_indicators(store, list(symbols), target_date)

    log.info("Daily pipeline complete: %d symbols updated for %s", updated, target_date)
    return {
        "statusCode": 200,
        "body": {"date": str(target_date), "rows": len(df), "symbols": updated},
    }


def _run_backfill(event: dict) -> dict:
    """Backfill historical bhavcopy data for a date range."""
    from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
    from app.data.providers.nse_bhavcopy import fetch_bhavcopy  # noqa: PLC0415
    from app.data.store import ParquetStore  # noqa: PLC0415
    from app.core.time_utils import is_trading_day  # noqa: PLC0415

    bucket = os.environ["DATA_BUCKET"]
    store = ParquetStore(bucket)
    symbols = set(all_symbols())

    start_str = event.get("start", "2019-01-01")
    end_str = event.get("end", str(date.today()))
    step_days = int(event.get("step_days", 30))

    start = date.fromisoformat(start_str)
    end = date.fromisoformat(end_str)

    log.info("Backfill: %s → %s in %d-day chunks", start, end, step_days)

    total_rows = 0
    total_days = 0
    chunk_start = start

    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=step_days - 1), end)
        current = chunk_start
        while current <= chunk_end:
            if is_trading_day(current):
                df = fetch_bhavcopy(current)
                if not df.empty:
                    df_filtered = df[df["symbol"].isin(symbols)]
                    for symbol, group in df_filtered.groupby("symbol"):
                        store.append_ohlcv(str(symbol), group.to_dict("records"))
                    total_rows += len(df_filtered)
                    total_days += 1
                    log.info("Backfill: loaded %s (%d rows)", current, len(df_filtered))
            current += timedelta(days=1)
        chunk_start = chunk_end + timedelta(days=1)

    log.info("Backfill complete: %d trading days, %d total rows", total_days, total_rows)
    return {
        "statusCode": 200,
        "body": {
            "start": start_str,
            "end": end_str,
            "trading_days_loaded": total_days,
            "total_rows": total_rows,
        },
    }


def _compute_indicators(store: object, symbols: list[str], as_of: date) -> None:
    """Pre-compute EMA, RSI, ATR, SuperTrend for all symbols and write to S3."""
    import pandas as pd  # noqa: PLC0415
    from app.indicators.ema import ema  # noqa: PLC0415
    from app.indicators.rsi import wilder_rsi  # noqa: PLC0415
    from app.indicators.atr import wilder_atr  # noqa: PLC0415
    from app.indicators.supertrend import supertrend  # noqa: PLC0415
    from app.core.exceptions import InsufficientDataError  # noqa: PLC0415

    # Use a lookback window adequate for 200 EMA
    lookback_start = as_of - timedelta(days=300)
    computed = 0

    for symbol in symbols:
        try:
            df = store.read_ohlcv_range(symbol, lookback_start, as_of)  # type: ignore[attr-defined]
            if df.empty or len(df) < 30:
                continue

            closes = df["close"].tolist()
            highs = df["high"].tolist()
            lows = df["low"].tolist()
            dates = df["date"].tolist()
            n = len(closes)

            ind: dict[str, list] = {"date": dates}

            for period in (20, 50, 200):
                try:
                    ind[f"ema{period}"] = ema(closes, period)
                except InsufficientDataError:
                    ind[f"ema{period}"] = [None] * n

            try:
                ind["rsi14"] = wilder_rsi(closes, 14)
            except InsufficientDataError:
                ind["rsi14"] = [None] * n

            try:
                ind["atr14"] = wilder_atr(highs, lows, closes, 14)
            except InsufficientDataError:
                ind["atr14"] = [None] * n

            try:
                st_results = supertrend(highs, lows, closes, 10, 3.0)
                ind["st_value"] = [r.value for r in st_results]
                ind["st_direction"] = [r.direction for r in st_results]
            except InsufficientDataError:
                ind["st_value"] = [None] * n
                ind["st_direction"] = [None] * n

            ind_df = pd.DataFrame(ind)
            store.write_indicators(symbol, ind_df)  # type: ignore[attr-defined]
            computed += 1
        except Exception:
            log.warning("Failed to compute indicators for %s", symbol, exc_info=True)

    log.info("Indicators computed for %d/%d symbols", computed, len(symbols))
