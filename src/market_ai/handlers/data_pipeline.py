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


def _alert_pipeline_failure(target_date, reason: str) -> None:
    log.error("Data pipeline failure for %s: %s", target_date, reason)
    import os
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        try:
            from app.notifications import telegram
            telegram.send(bot_token, chat_id,
                f"Market AI: data pipeline produced 0 rows for {target_date}. {reason}. Manual check required.")
        except Exception:
            log.warning("Failed to send pipeline failure alert", exc_info=True)


def _run_daily(target_date: date) -> dict:
    """Fetch yesterday's bhavcopy and append to S3 Parquet for all NIFTY 500 symbols."""
    from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
    from app.data.providers.nse_bhavcopy import fetch_bhavcopy, BhavcopBlockedError  # noqa: PLC0415
    from app.data.store import ParquetStore  # noqa: PLC0415
    from app.core.time_utils import is_trading_day  # noqa: PLC0415

    bucket = os.environ["DATA_BUCKET"]
    store = ParquetStore(bucket)
    symbols = set(all_symbols())

    log.info("Daily pipeline: fetching bhavcopy for %s", target_date)
    try:
        df = fetch_bhavcopy(target_date)
    except BhavcopBlockedError as exc:
        _alert_pipeline_failure(target_date, f"NSE IP block detected: {exc}")
        return {"statusCode": 200, "body": {"date": str(target_date), "rows": 0, "symbols": 0, "error": "blocked"}}

    if df.empty:
        if is_trading_day(target_date):
            _alert_pipeline_failure(target_date, "bhavcopy returned empty on a trading day — possible NSE block or CDN failure")
        else:
            log.info("No bhavcopy for %s (non-trading day)", target_date)
        return {"statusCode": 200, "body": {"date": str(target_date), "rows": 0, "symbols": 0}}

    # Filter to our universe only
    df = df[df["symbol"].isin(symbols)]
    updated = 0
    adjusted = 0
    for symbol, group in df.groupby("symbol"):
        if _is_adjusted(store, str(symbol)):
            log.info("Skipping bhavcopy append for %s — yfinance-adjusted series in place", symbol)
            continue
        rows = group.to_dict("records")
        store.append_ohlcv(str(symbol), rows)
        updated += 1
        if _detect_split(store, str(symbol), target_date):
            log.warning(
                "Corporate action detected for %s on %s — re-fetching adjusted history",
                symbol, target_date,
            )
            if _readjust_from_yfinance(store, str(symbol)):
                adjusted += 1

    # Pre-compute indicators after data is fresh
    _compute_indicators(store, list(symbols), target_date)

    log.info(
        "Daily pipeline complete: %d symbols updated, %d adjusted for %s",
        updated, adjusted, target_date,
    )
    return {
        "statusCode": 200,
        "body": {
            "date": str(target_date),
            "rows": len(df),
            "symbols": updated,
            "adjusted": adjusted,
        },
    }


_BACKFILL_PK = "backfill#progress"
_BACKFILL_SK = "checkpoint"


def _load_checkpoint() -> date | None:
    """Return the last completed chunk_end date from DynamoDB, or None."""
    import boto3  # noqa: PLC0415
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name:
        return None
    try:
        dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        table = dynamo.Table(table_name)
        item = table.get_item(Key={"pk": _BACKFILL_PK, "sk": _BACKFILL_SK}).get("Item")
        if item and item.get("last_chunk_end"):
            return date.fromisoformat(str(item["last_chunk_end"]))
    except Exception:
        log.warning("Could not load backfill checkpoint", exc_info=True)
    return None


def _save_checkpoint(chunk_end: date) -> None:
    """Persist the completed chunk_end so the next invocation can resume."""
    import boto3  # noqa: PLC0415
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name:
        return
    try:
        dynamo = boto3.resource("dynamodb", region_name=os.environ.get("AWS_REGION", "ap-south-1"))
        table = dynamo.Table(table_name)
        table.put_item(Item={
            "pk": _BACKFILL_PK,
            "sk": _BACKFILL_SK,
            "last_chunk_end": chunk_end.isoformat(),
        })
    except Exception:
        log.warning("Could not save backfill checkpoint", exc_info=True)


def _run_backfill(event: dict) -> dict:
    """Backfill historical bhavcopy data for a date range.

    The Lambda timeout is 900 s. Strategy: fetch all trading days in the chunk
    first (HTTP only), accumulate rows in memory keyed by symbol, then flush to
    S3 once per symbol. This reduces S3 ops from (symbols × days) to (symbols),
    cutting a 90-day chunk from ~30,000 S3 calls to ~1,000.

    Auto-resume: if 'start' is omitted, the last checkpoint is loaded from
    DynamoDB so the same event payload can be re-invoked repeatedly until done.

    Recommended: step_days=180 (≈ one half-year per call, ~12 invocations for
    the full 2019→2024 backfill).
    """
    from collections import defaultdict  # noqa: PLC0415

    from app.core.time_utils import is_trading_day  # noqa: PLC0415
    from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
    from app.data.providers.nse_bhavcopy import (  # noqa: PLC0415
        _fetch_with_client,
        _get_session_client,
    )
    from app.data.store import ParquetStore  # noqa: PLC0415

    bucket = os.environ["DATA_BUCKET"]
    store = ParquetStore(bucket)
    symbols = set(all_symbols())

    end_str = event.get("end") or "2024-12-31"
    step_days = int(event.get("step_days", 180))

    # Resolve start: explicit in event > DynamoDB checkpoint + 1 day > default
    explicit_start = event.get("start") or ""
    if explicit_start:
        start = date.fromisoformat(explicit_start)
    else:
        checkpoint = _load_checkpoint()
        if checkpoint:
            start = checkpoint + timedelta(days=1)
            log.info(
                "Resuming backfill from checkpoint: last completed %s, starting %s",
                checkpoint, start,
            )
        else:
            start = date(2019, 1, 1)
            log.info("No checkpoint found, starting from %s", start)

    end = date.fromisoformat(end_str)
    chunk_end = min(start + timedelta(days=step_days - 1), end)

    log.info("Backfill: %s → %s (this chunk ends %s)", start, end, chunk_end)

    # Phase 1 — HTTP only: fetch all trading days, accumulate rows per symbol in memory.
    # Avoids interleaving S3 reads/writes with network calls.
    rows_by_symbol: dict[str, list[dict]] = defaultdict(list)
    total_days = 0
    last_loaded: date | None = None

    client = _get_session_client()
    try:
        current = start
        while current <= chunk_end:
            if is_trading_day(current):
                df = _fetch_with_client(client, current)
                if not df.empty:
                    df_filtered = df[df["symbol"].isin(symbols)]
                    if not df_filtered.empty:
                        for symbol, group in df_filtered.groupby("symbol"):
                            rows_by_symbol[str(symbol)].extend(group.to_dict("records"))
                        total_days += 1
                        last_loaded = current
                        log.info(
                            "Backfill: fetched %s (%d rows, %d symbols)",
                            current, len(df_filtered), df_filtered["symbol"].nunique(),
                        )
                    else:
                        log.debug("No universe symbols in bhavcopy for %s", current)
                else:
                    log.debug("No data for %s (holiday or NSE fetch failed)", current)
            current += timedelta(days=1)
    finally:
        client.close()

    # Phase 2 — S3 only: one read + one write per symbol for the whole chunk.
    total_rows = 0
    for symbol, rows in rows_by_symbol.items():
        try:
            store.append_ohlcv(symbol, rows)
            total_rows += len(rows)
        except Exception:
            log.warning("S3 write failed for %s — skipping", symbol, exc_info=True)

    # Save checkpoint so next invocation auto-resumes from here.
    if total_rows > 0 or total_days > 0:
        _save_checkpoint(chunk_end)
    else:
        log.warning("Backfill chunk %s->%s produced zero rows — NOT advancing checkpoint", start, chunk_end)

    next_start = chunk_end + timedelta(days=1)
    done = next_start > end

    log.info(
        "Backfill chunk done: %d trading days, %d rows, %d symbols. next_start=%s done=%s",
        total_days, total_rows, len(rows_by_symbol), next_start, done,
    )
    return {
        "statusCode": 200,
        "body": {
            "chunk_start": start.isoformat(),
            "chunk_end": chunk_end.isoformat(),
            "full_end": end_str,
            "trading_days_loaded": total_days,
            "total_rows": total_rows,
            "symbols_updated": len(rows_by_symbol),
            "last_loaded_date": str(last_loaded) if last_loaded else None,
            "next_start": next_start.isoformat(),
            "done": done,
        },
    }


def _detect_split(store: object, symbol: str, today: date) -> bool:
    """Return True if today's close vs yesterday's close implies a corporate action.

    A ratio outside [0.75, 1.35] on a single day is almost never a real price move —
    it indicates a stock split, reverse split, or bonus issue.
    """
    from app.data.store import ParquetStore  # noqa: PLC0415

    if not isinstance(store, ParquetStore):
        return False
    try:
        from datetime import timedelta  # noqa: PLC0415
        df = store.read_ohlcv_range(symbol, today - timedelta(days=5), today)
        if len(df) < 2:
            return False
        df = df.sort_values("date")
        prev_close = float(df.iloc[-2]["close"])
        today_close = float(df.iloc[-1]["close"])
        if prev_close <= 0:
            return False
        if abs(today_close - prev_close) / prev_close < 0.01:
            return False
        ratio = today_close / prev_close
        # NSE circuit limits cap single-day moves at 20%; beyond 35% is a corporate action
        if ratio < 0.75 or ratio > 1.35:
            log.warning(
                "Split signal for %s: prev_close=%.2f today_close=%.2f ratio=%.3f",
                symbol, prev_close, today_close, ratio,
            )
            return True
    except Exception:
        log.debug("Split detection failed for %s", symbol, exc_info=True)
    return False


def _readjust_from_yfinance(store: object, symbol: str) -> bool:
    """Overwrite S3 Parquet for symbol with yfinance split-adjusted history.

    yfinance returns auto-adjusted prices by default, so the full historical
    series is restated to reflect the split. We re-fetch from 2019-01-01 and
    overwrite the existing file entirely.
    """
    from datetime import date as _date  # noqa: PLC0415
    from app.data.store import ParquetStore  # noqa: PLC0415

    if not isinstance(store, ParquetStore):
        return False
    try:
        import yfinance as yf  # noqa: PLC0415
        import pandas as pd  # noqa: PLC0415
        from zoneinfo import ZoneInfo  # noqa: PLC0415

        IST = ZoneInfo("Asia/Kolkata")
        ticker = yf.Ticker(f"{symbol}.NS")
        df = ticker.history(
            start="2019-01-01",
            end=_date.today().isoformat(),
            interval="1d",
            auto_adjust=True,
        )
        if df is None or df.empty:
            log.warning("yfinance returned no data for %s — skipping re-adjust", symbol)
            return False

        if hasattr(df.columns, "levels"):
            df.columns = df.columns.get_level_values(0)

        rows = []
        for ts_raw, row in df.iterrows():
            try:
                if hasattr(ts_raw, "tzinfo") and ts_raw.tzinfo is not None:
                    d = ts_raw.astimezone(IST).date()
                else:
                    d = ts_raw.date()
                o, h, l, c, v = (
                    float(row["Open"]), float(row["High"]),
                    float(row["Low"]), float(row["Close"]), float(row["Volume"]),
                )
                if c > 0:
                    rows.append({
                        "date": d, "open": o, "high": h, "low": l,
                        "close": c, "volume": v,
                        "source": "yfinance_adjusted", "adjusted": True,
                    })
            except Exception:
                continue

        if not rows:
            log.warning("No valid rows from yfinance for %s", symbol)
            return False

        # Overwrite S3 entirely with adjusted series
        new_df = pd.DataFrame(rows)
        new_df["date"] = pd.to_datetime(new_df["date"]).dt.date
        new_df = new_df.drop_duplicates(subset=["date"], keep="last")
        new_df = new_df.sort_values("date").reset_index(drop=True)
        store._write_parquet(f"ohlcv/daily/{symbol}.parquet", new_df)  # noqa: SLF001
        _set_adjusted_flag(store, symbol, _date.today())
        log.info(
            "Re-adjusted %s with %d rows from yfinance (split-adjusted)",
            symbol, len(new_df),
        )
        return True
    except ImportError:
        log.warning("yfinance not installed — cannot re-adjust %s", symbol)
    except Exception:
        log.warning("Re-adjust failed for %s", symbol, exc_info=True)
    return False


def _adjusted_flag_key(symbol: str) -> str:
    return f"ohlcv/daily/{symbol}.adjusted_flag"


def _is_adjusted(store, symbol: str) -> bool:
    try:
        store._s3.head_object(Bucket=store._bucket, Key=_adjusted_flag_key(symbol))
        return True
    except Exception:
        return False


def _set_adjusted_flag(store, symbol: str, today) -> None:
    import json
    body = json.dumps({"adjusted": True, "adjusted_date": str(today), "source": "yfinance"})
    store._s3.put_object(Bucket=store._bucket, Key=_adjusted_flag_key(symbol), Body=body.encode())


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
