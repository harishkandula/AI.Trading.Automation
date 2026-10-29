"""
Backtest Lambda handler.

Triggered by EventBridge (Sunday 11:00 IST weekly).
Runs all registered daily strategies in walk-forward mode over the NIFTY 500,
saves per-strategy results to S3, and sends a ranked Telegram summary.

Event payload (all keys optional):
    {
        "mode":         "walk_forward" | "simple",
        "start":        "2019-01-01",   # train anchor; default 2019-01-01
        "end":          "2025-12-31",   # test end; default today
        "test_start":   "2023-01-01",   # first OOS window; default start + 4 years
        "universe":     "nifty500",     # "nifty50" | "nifty500"; default "nifty500"
        "capital":      100000.0,       # initial capital per window; default 100 000
        "step_months":  6,              # walk-forward window size; default 6
        "strategy":     "s01_rs_momentum"  # comma-separated; default all daily strategies
    }
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

# No module-level heavy imports (pandas, boto3, strategies) — Lambda cold start
# was timing out at 10 s loading them all. Everything is imported lazily inside
# the functions that need them.

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")

_DEFAULT_TRAIN_ANCHOR = date(2019, 1, 1)
# Default first OOS window start for walk-forward.
# Gives 2 years of initial training data (2019-01-01 → 2020-12-31) before the
# first out-of-sample test window begins on 2021-01-01.
_DEFAULT_WF_TEST_START = date(2021, 1, 1)
_S3_PREFIX = "backtests"
_WARMUP_BUFFER_DAYS = 120


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the weekly backtest run (Sunday 11:00 IST)."""
    today = datetime.now(tz=IST).date()

    try:
        return _run_backtest(event, today)
    except Exception as exc:
        log.exception("Backtest handler fatal error")
        try:
            from app.notifications.notifier import Notifier  # noqa: PLC0415
            notifier = Notifier.from_secrets()
            notifier.send_error_alert("backtest_handler", str(exc))
        except Exception:
            log.exception("Failed to send error alert")
        return {
            "statusCode": 500,
            "body": f"Backtest run failed: {exc}",
        }


def _run_backtest(event: Any, today: date) -> dict:
    from app.strategies.registry import all_ids  # noqa: PLC0415

    evt = event if isinstance(event, dict) else {}
    mode: str = evt.get("mode", "walk_forward")

    end = date.fromisoformat(evt["end"]) if "end" in evt else today
    start = (
        date.fromisoformat(evt["start"])
        if "start" in evt
        else _DEFAULT_TRAIN_ANCHOR
    )
    # Walk-forward OOS start: default to 2021-01-01 (gives ~2 y initial training),
    # overridable via "test_start" in the event payload.
    wf_test_start = (
        date.fromisoformat(evt["test_start"])
        if "test_start" in evt
        else _DEFAULT_WF_TEST_START
    )
    universe_name: str = evt.get("universe", "nifty500")
    capital: float = float(evt.get("capital", 100_000.0))
    step_months: int = int(evt.get("step_months", 6))
    strategy_filter: str = evt.get("strategy", "")

    symbols = _resolve_universe(universe_name)
    all_daily_ids = [sid for sid in all_ids() if not _is_intraday(sid)]
    strategy_ids = (
        [s.strip() for s in strategy_filter.split(",") if s.strip()]
        if strategy_filter
        else all_daily_ids
    )

    log.info(
        "Backtest %s: universe=%s (%d symbols) full-IS=%s→%s wf-OOS=%s→%s strategies=%d",
        mode, universe_name, len(symbols), start, end, wf_test_start, end, len(strategy_ids),
    )

    bucket = _data_bucket()
    run_date = today.isoformat()
    s3_keys: dict[str, str] = {}

    if mode == "walk_forward":
        # ── 1. Walk-forward (OOS validated) ──────────────────────────────────
        wf_s3_keys, wf_results = _run_walk_forward_all(
            strategy_ids, symbols, start, wf_test_start, end,
            capital, step_months, bucket, run_date, today,
        )
        s3_keys.update(wf_s3_keys)

        # ── 2. Full in-sample backtest 2019-01-01 → today ───────────────────
        from app.backtesting.runner import run_universe_backtest  # noqa: PLC0415
        log.info("Running full in-sample backtest %s → %s", start, end)
        is_results = run_universe_backtest(
            symbols=symbols,
            strategy_ids=strategy_ids,
            start=start,
            end=end,
            initial_capital=capital,
        )
        if bucket:
            is_s3_keys = _save_simple_to_s3(bucket, run_date, is_results)
            s3_keys.update({f"{sid}_insample": k for sid, k in is_s3_keys.items()})

        # ── 3. Persist combined walk_forward_results.json ───────────────────
        if bucket and wf_results:
            combined_key = _save_combined_walk_forward(
                bucket, run_date, wf_results, is_results,
            )
            if combined_key:
                s3_keys["_combined"] = combined_key

        msg = _format_walk_forward_summary(
            run_date, start, wf_test_start, end, universe_name, wf_s3_keys,
        )
    else:
        from app.backtesting.runner import run_universe_backtest  # noqa: PLC0415
        results = run_universe_backtest(
            symbols=symbols,
            strategy_ids=strategy_ids,
            start=start,
            end=end,
            initial_capital=capital,
        )
        if bucket:
            s3_keys = _save_simple_to_s3(bucket, run_date, results)
        msg = _format_simple_summary(run_date, start, end, universe_name, results)

    try:
        _send_telegram(msg)
    except Exception:
        log.exception("Failed to send Telegram backtest summary")

    return {
        "statusCode": 200,
        "body": {
            "mode": mode,
            "run_date": run_date,
            "train_start": start.isoformat(),
            "wf_test_start": wf_test_start.isoformat() if mode == "walk_forward" else None,
            "end": end.isoformat(),
            "universe": universe_name,
            "strategies_run": strategy_ids,
            "s3_keys": s3_keys,
        },
    }


def _s3_available_symbols(bucket: str) -> set[str]:
    """Return the set of symbols that have a Parquet file in S3 (one ListObjects call)."""
    import boto3  # noqa: PLC0415
    s3 = boto3.client("s3")
    symbols: set[str] = set()
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix="ohlcv/daily/"):
        for obj in page.get("Contents", []):
            key = obj["Key"]  # ohlcv/daily/SYMBOL.parquet
            name = key.split("/")[-1]
            if name.endswith(".parquet"):
                symbols.add(name[:-8])  # strip .parquet
    log.info("S3 inventory: %d symbols have Parquet files", len(symbols))
    return symbols


def _run_walk_forward_all(
    strategy_ids: list[str],
    symbols: list[str],
    train_anchor: date,
    test_start: date,
    test_end: date,
    capital: float,
    step_months: int,
    bucket: str,
    run_date: str,
    today: date,
) -> tuple[dict[str, str], dict[str, Any]]:
    """Fetch candles once, run walk-forward per strategy, save results to S3.

    Returns
    -------
    (s3_keys, wf_results)
        s3_keys:    strategy_id → S3 key for the per-strategy JSON.
        wf_results: strategy_id → WalkForwardResult (for building combined JSON).
    """
    from app.backtesting.leaderboard import write_result as _leaderboard_write  # noqa: PLC0415
    from app.backtesting.walk_forward import run_walk_forward  # noqa: PLC0415
    from app.data.providers.hybrid_provider import HybridDataProvider  # noqa: PLC0415
    from app.models.candle import Timeframe  # noqa: PLC0415

    # One ListObjects call to find which symbols actually have data in S3.
    # Avoids 500 individual GetObject calls for symbols that don't exist yet.
    available = _s3_available_symbols(bucket) if bucket else set()
    symbols_to_fetch = [
        sym.removesuffix(".NS") for sym in symbols
        if sym.removesuffix(".NS") in available
    ] if available else [sym.removesuffix(".NS") for sym in symbols]

    log.info(
        "Symbols in universe: %d, have S3 data: %d, skipping: %d",
        len(symbols), len(symbols_to_fetch), len(symbols) - len(symbols_to_fetch),
    )

    # fill_gaps=False: read only from S3, no Groww/yfinance calls.
    provider = HybridDataProvider(fill_gaps=False)
    # Clamp to 2019-01-01 — earliest date in S3.
    fetch_start = max(train_anchor - timedelta(days=_WARMUP_BUFFER_DAYS), date(2019, 1, 1))

    import time  # noqa: PLC0415
    t0 = time.monotonic()
    candles_by_symbol: dict[str, Any] = {}
    for bare in symbols_to_fetch:
        try:
            bars = provider.get_historical_candles(bare, Timeframe.D1, fetch_start, test_end)
            if bars:
                candles_by_symbol[bare] = bars
        except Exception:
            log.warning("Candle fetch failed: %s", bare, exc_info=True)

    log.info(
        "Loaded candles for %d / %d symbols in %.1fs",
        len(candles_by_symbol), len(symbols_to_fetch), time.monotonic() - t0,
    )

    s3_keys: dict[str, str] = {}
    wf_results: dict[str, Any] = {}
    for strategy_id in strategy_ids:
        try:
            t1 = time.monotonic()
            result = run_walk_forward(
                strategy_id=strategy_id,
                candles_by_symbol=candles_by_symbol,
                train_anchor_start=train_anchor,
                initial_test_start=test_start,
                test_end=test_end,
                step_months=step_months,
                initial_capital=capital,
                run_date=today,
            )
            wf_results[strategy_id] = result
            if bucket:
                key = _save_walk_forward_to_s3(bucket, run_date, result)
                if key:
                    s3_keys[strategy_id] = key
            log.info(
                "Walk-forward %s: validated=%s sharpe=%.2f consistency=%.0f%% in %.1fs",
                strategy_id,
                result.aggregated.get("validated"),
                result.aggregated.get("avg_sharpe") or 0,
                (result.aggregated.get("consistency_score") or 0) * 100,
                time.monotonic() - t1,
            )
            try:
                s3_key = s3_keys.get(strategy_id, "")
                _leaderboard_write(
                    strategy_id=strategy_id,
                    run_date=run_date,
                    strategy_version=result.strategy_version,
                    aggregated=result.aggregated,
                    provenance=result.provenance,
                    windows_count=len(result.windows),
                    s3_key=s3_key,
                )
            except Exception:
                log.warning(
                    "Leaderboard write failed for %s (LEADERBOARD_TABLE set?)",
                    strategy_id, exc_info=True,
                )
        except Exception:
            log.warning("Walk-forward failed for %s", strategy_id, exc_info=True)

    return s3_keys, wf_results


def _is_intraday(strategy_id: str) -> bool:
    from app.strategies.registry import get  # noqa: PLC0415
    try:
        cls = get(strategy_id)
        from app.models.candle import Timeframe as TF  # noqa: PLC0415
        return cls.timeframe == TF.M5
    except Exception:
        return False


def _resolve_universe(name: str) -> list[str]:
    if name == "nifty500":
        from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
        return all_symbols()
    if name == "nifty50":
        from app.data.nifty500_symbols import all_symbols  # noqa: PLC0415
        return all_symbols(segment="LARGE_CAP")[:50]
    from app.data.universe import get_universe  # noqa: PLC0415
    return get_universe(name)


# ---------------------------------------------------------------------------
# S3 helpers
# ---------------------------------------------------------------------------

def _data_bucket() -> str:
    return os.environ.get("DATA_BUCKET", "")


def _save_walk_forward_to_s3(bucket: str, run_date: str, result: Any) -> str:
    import json  # noqa: PLC0415
    import boto3  # noqa: PLC0415
    s3 = boto3.client("s3")
    key = f"{_S3_PREFIX}/walk_forward/{run_date}/{result.strategy_id}.json"
    payload: dict[str, Any] = {
        "strategy_id": result.strategy_id,
        "strategy_version": result.strategy_version,
        "train_anchor": result.train_anchor.isoformat(),
        "test_end": result.test_end.isoformat(),
        "step_months": result.step_months,
        "aggregated": result.aggregated,
        "provenance": result.provenance,
        "windows": [
            {
                "window_id": w.window_id,
                "train_start": w.train_start.isoformat(),
                "train_end": w.train_end.isoformat(),
                "test_start": w.test_start.isoformat(),
                "test_end": w.test_end.isoformat(),
                "signal_count": w.signal_count,
                "trade_count": w.trade_count,
                "metrics": w.metrics,
            }
            for w in result.windows
        ],
    }
    try:
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(payload, indent=2, default=str),
            ContentType="application/json",
        )
        log.info("Saved walk-forward %s → s3://%s/%s", result.strategy_id, bucket, key)
    except Exception:
        log.warning("Failed to save walk-forward to S3: %s", result.strategy_id, exc_info=True)
        return ""
    return key


def _save_combined_walk_forward(
    bucket: str,
    run_date: str,
    wf_results: dict[str, Any],
    is_results: dict[str, Any],
) -> str:
    """Save a single walk_forward_results.json combining per-window OOS metrics
    and top-level in-sample metrics for every strategy that ran this session.

    S3 key: backtests/{run_date}/walk_forward_results.json

    Schema
    ------
    {
      "run_date": "...",
      "windows": [
        {
          "train_start": "2021-01-01", "train_end": "...",
          "test_start": "...", "test_end": "...",
          "strategies": {
            "s02_vcp": {"sharpe": 1.2, "win_rate": 0.52, "avg_return": 0.031},
            ...
          }
        }
      ],
      "summary": {
        "s02_vcp": {
          "avg_oos_sharpe": 1.1, "consistency_pct": 0.72,
          "validated": true,
          "insample_sharpe": 1.4, "insample_return_pct": 38.2
        },
        ...
      }
    }
    """
    import json  # noqa: PLC0415
    import boto3  # noqa: PLC0415

    key = f"{_S3_PREFIX}/{run_date}/walk_forward_results.json"

    # ── windows section: one entry per OOS test window ──────────────────────
    # Collect all unique window slots across strategies (keyed by test_start).
    window_map: dict[str, dict[str, Any]] = {}
    for strategy_id, result in wf_results.items():
        for w in result.windows:
            slot_key = w.test_start.isoformat()
            if slot_key not in window_map:
                window_map[slot_key] = {
                    "train_start": w.train_start.isoformat(),
                    "train_end":   w.train_end.isoformat(),
                    "test_start":  w.test_start.isoformat(),
                    "test_end":    w.test_end.isoformat(),
                    "strategies":  {},
                }
            m = w.metrics
            if "error" not in m:
                window_map[slot_key]["strategies"][strategy_id] = {
                    "sharpe":      m.get("sharpe_ratio"),
                    "win_rate":    round((m.get("win_rate_pct") or 0.0) / 100, 4),
                    "avg_return":  round((m.get("net_return_pct") or 0.0) / 100, 4),
                    "max_drawdown": round((m.get("max_drawdown_pct") or 0.0) / 100, 4),
                    "trade_count": w.trade_count,
                }

    windows_list = [window_map[k] for k in sorted(window_map)]

    # ── summary section: per-strategy aggregated OOS + in-sample headline ──
    summary: dict[str, Any] = {}
    for strategy_id, result in wf_results.items():
        agg = result.aggregated
        is_m = (is_results.get(strategy_id) or {}).get("metrics", {})
        summary[strategy_id] = {
            "avg_oos_sharpe":      agg.get("avg_sharpe"),
            "consistency_pct":     agg.get("consistency_score"),
            "avg_oos_return_pct":  agg.get("avg_net_return_pct"),
            "avg_oos_drawdown_pct": agg.get("avg_max_drawdown_pct"),
            "total_oos_trades":    agg.get("total_oos_trades"),
            "validated":           agg.get("validated"),
            # In-sample headline figures for comparison
            "insample_sharpe":        is_m.get("sharpe_ratio"),
            "insample_return_pct":    is_m.get("net_return_pct"),
            "insample_max_drawdown_pct": is_m.get("max_drawdown_pct"),
        }

    payload: dict[str, Any] = {
        "run_date": run_date,
        "windows": windows_list,
        "summary": summary,
    }

    try:
        s3 = boto3.client("s3")
        s3.put_object(
            Bucket=bucket, Key=key,
            Body=json.dumps(payload, indent=2, default=str),
            ContentType="application/json",
        )
        log.info("Saved combined walk_forward_results.json → s3://%s/%s", bucket, key)
    except Exception:
        log.warning("Failed to save combined walk_forward_results.json", exc_info=True)
        return ""
    return key


def _save_simple_to_s3(bucket: str, run_date: str, results: dict[str, Any]) -> dict[str, str]:
    import json  # noqa: PLC0415
    import boto3  # noqa: PLC0415
    s3 = boto3.client("s3")
    keys: dict[str, str] = {}
    for strategy_id, result in results.items():
        key = f"{_S3_PREFIX}/simple/{run_date}/{strategy_id}.json"
        try:
            s3.put_object(
                Bucket=bucket, Key=key,
                Body=json.dumps(result, indent=2, default=str),
                ContentType="application/json",
            )
            keys[strategy_id] = key
        except Exception:
            log.warning("Failed to save %s to S3", strategy_id, exc_info=True)
    return keys


# ---------------------------------------------------------------------------
# Telegram formatting
# ---------------------------------------------------------------------------

def _send_telegram(message: str) -> None:
    from app.notifications import telegram as _telegram  # noqa: PLC0415
    from app.notifications.notifier import Notifier  # noqa: PLC0415
    bot_token = os.environ.get("TELEGRAM_BOT_TOKEN", "")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID", "")
    if bot_token and chat_id:
        _telegram.send(bot_token, chat_id, message)
        return
    notifier = Notifier.from_secrets()
    notifier.send_error_alert("backtest_summary", message)


def _format_walk_forward_summary(
    run_date: str,
    train_anchor: date,
    test_start: date,
    test_end: date,
    universe: str,
    s3_keys: dict[str, str],
) -> str:
    lines = [
        "<b>Market AI — Walk-Forward Backtest</b>",
        f"Run: {run_date}  |  Universe: {universe}",
        f"Train anchor: {train_anchor}  |  OOS: {test_start} → {test_end}",
        f"Strategies tested: {len(s3_keys)}",
        "",
        "Results saved to S3. Use the leaderboard Lambda for ranked summary.",
    ]
    return "\n".join(lines)


def _format_simple_summary(
    run_date: str,
    start: date,
    end: date,
    universe: str,
    results: dict[str, Any],
) -> str:
    lines = [
        "<b>Market AI — Backtest Summary</b>",
        f"Run date: {run_date}  |  Period: {start} → {end}",
        f"Universe: {universe}  |  Strategies: {len(results)}",
        "",
    ]

    if not results:
        lines.append("No strategies produced results.")
        return "\n".join(lines)

    def _sharpe_key(item: tuple[str, Any]) -> float:
        m = item[1].get("metrics", {})
        v = m.get("sharpe_ratio")
        return float(v) if v is not None else float("-inf")

    for rank, (strategy_id, result) in enumerate(
        sorted(results.items(), key=_sharpe_key, reverse=True), start=1
    ):
        m = result.get("metrics", {})
        if "error" in m:
            lines.append(f"{rank}. <b>{strategy_id}</b> — {m['error']}")
            continue
        lines.append(
            f"{rank}. <b>{strategy_id}</b>\n"
            f"   Sharpe: {m.get('sharpe_ratio', 'N/A')} | "
            f"Win: {m.get('win_rate_pct', 0):.1f}% | "
            f"Return: {m.get('net_return_pct', 0):.1f}% | "
            f"DD: {m.get('max_drawdown_pct', 0):.1f}%\n"
            f"   Trades: {m.get('executed_trades', 0)} | "
            f"PF: {m.get('profit_factor', 'N/A')}"
        )

    lines += [
        "",
        "Research only — historical results do not guarantee future returns.",
    ]
    return "\n".join(lines)
