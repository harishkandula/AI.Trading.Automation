"""Weekly Lambda handler — price-based compounder research scan.

Scores NIFTY 500 stocks using price/volume data already in S3 as a proxy
for compounder quality until a proper fundamentals API is integrated.

Scoring factors (all derived from S3 Parquet candles):
  - 52-week momentum     : close vs 52-week high (higher = stronger trend)
  - Long-term trend      : close vs 200-day EMA (above = uptrend)
  - Trend consistency    : % of last 52 weeks that closed above 200d EMA
  - Volume confirmation  : 20-day avg volume vs 52-week avg volume
  - Drawdown control     : max drawdown over past 52 weeks (lower = better)
  - Volatility-adj return: 52-week return / annualised volatility (Sharpe-like)

TODO: Replace with Tijori Finance / Tickertape fundamentals API when
      integrated. These metrics are price proxies, not real fundamentals.
"""
from __future__ import annotations

import logging
import os
from datetime import date, timedelta
from typing import Any

log = logging.getLogger(__name__)

_LOOKBACK_DAYS = 380   # ~52 weeks + warmup
_TOP_N = 20            # candidates to surface
_MIN_TRADING_DAYS = 200


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    from datetime import datetime  # noqa: PLC0415
    from zoneinfo import ZoneInfo  # noqa: PLC0415

    IST = ZoneInfo("Asia/Kolkata")
    today = datetime.now(tz=IST).date()

    try:
        candidates = _scan(today)
        _notify(today, candidates)
        return {
            "statusCode": 200,
            "body": {
                "title": "Weekly compounder research",
                "generated_at": datetime.now(tz=IST).isoformat(),
                "scan_date": today.isoformat(),
                "universe_scanned": len(candidates) + 0,
                "top_candidates": len([c for c in candidates if c["score"] >= 60]),
                "candidates": candidates[:_TOP_N],
                "disclaimer": (
                    "Price-based proxy scores only. "
                    "Not investment advice. Market investments involve risk."
                ),
            },
        }
    except Exception as exc:
        log.exception("Weekly handler failed")
        return {"statusCode": 500, "body": str(exc)}


def _scan(today: date) -> list[dict[str, Any]]:
    from app.data.nifty500_symbols import NIFTY500  # noqa: PLC0415
    from app.data.store import ParquetStore  # noqa: PLC0415

    bucket = os.environ.get("DATA_BUCKET", "")
    if not bucket:
        raise RuntimeError("DATA_BUCKET not set")

    store = ParquetStore(bucket)
    start = today - timedelta(days=_LOOKBACK_DAYS)
    results: list[dict[str, Any]] = []

    for meta in NIFTY500:
        try:
            df = store.read_ohlcv_range(meta.symbol, start, today)
            if df.empty or len(df) < _MIN_TRADING_DAYS:
                continue
            score, breakdown = _score(df)
            results.append({
                "symbol": meta.symbol,
                "segment": meta.segment,
                "sector": meta.sector,
                "score": round(score, 1),
                "breakdown": breakdown,
            })
        except Exception:
            log.debug("Skipping %s", meta.symbol, exc_info=True)

    results.sort(key=lambda x: x["score"], reverse=True)
    log.info("Weekly scan complete: %d symbols scored", len(results))
    return results


def _score(df: Any) -> tuple[float, dict[str, Any]]:
    """Return (composite_score_0_to_100, breakdown_dict)."""
    import numpy as np  # noqa: PLC0415

    closes = df["close"].astype(float).values
    volumes = df["volume"].astype(float).values
    n = len(closes)

    # 1. 52-week momentum: close vs 52-week high (weight 25)
    high_52w = closes.max()
    momentum = closes[-1] / high_52w  # 1.0 = at 52w high
    momentum_score = min(momentum * 100, 100)

    # 2. Long-term trend: close vs 200-day EMA (weight 20)
    ema200 = _ema(closes, 200)
    above_ema = closes[-1] > ema200[-1] if ema200 is not None else False
    trend_score = 100.0 if above_ema else 30.0

    # 3. Trend consistency: % of last 200 days above 200d EMA (weight 20)
    if ema200 is not None and n >= 200:
        recent_closes = closes[-200:]
        recent_ema = ema200[-200:]
        consistency = (recent_closes > recent_ema).mean() * 100
    else:
        consistency = 0.0

    # 4. Volume confirmation: recent 20d avg vs full period avg (weight 15)
    vol_recent = volumes[-20:].mean()
    vol_avg = volumes.mean()
    vol_ratio = vol_recent / vol_avg if vol_avg > 0 else 1.0
    volume_score = min(vol_ratio * 70, 100)  # cap at 100

    # 5. Drawdown control over full period (weight 10)
    peak = np.maximum.accumulate(closes)
    drawdowns = (closes - peak) / peak * 100
    max_dd = abs(drawdowns.min())
    drawdown_score = max(0, 100 - max_dd * 3)  # 33% DD → 0 score

    # 6. Volatility-adjusted return (weight 10)
    if n >= 52:
        ret_52w = (closes[-1] / closes[-min(252, n)] - 1) * 100
        daily_returns = np.diff(closes) / closes[:-1]
        vol = daily_returns.std() * (252 ** 0.5) * 100
        sharpe_proxy = (ret_52w / vol) * 20 if vol > 0 else 0
        va_score = min(max(sharpe_proxy, 0), 100)
    else:
        va_score = 0.0

    composite = (
        momentum_score   * 0.25
        + trend_score    * 0.20
        + consistency    * 0.20
        + volume_score   * 0.15
        + drawdown_score * 0.10
        + va_score       * 0.10
    )

    breakdown = {
        "momentum_pct_of_52w_high": round(momentum * 100, 1),
        "above_ema200": above_ema,
        "trend_consistency_pct": round(consistency, 1),
        "volume_ratio_20d_vs_avg": round(vol_ratio, 2),
        "max_drawdown_pct": round(max_dd, 1),
        "vol_adj_return_score": round(va_score, 1),
    }
    return composite, breakdown


def _ema(closes: Any, period: int) -> Any:
    """Simple EMA via pandas for accuracy."""
    try:
        import pandas as pd  # noqa: PLC0415
        return pd.Series(closes).ewm(span=period, adjust=False).mean().values
    except Exception:
        return None


def _notify(today: date, candidates: list[dict[str, Any]]) -> None:
    try:
        from app.notifications import telegram  # noqa: PLC0415
        from app.notifications.notifier import Notifier  # noqa: PLC0415

        notifier = Notifier.from_secrets()
        top = [c for c in candidates[:10] if c["score"] >= 50]

        lines = [
            "<b>Market AI - Weekly Compounder Research</b>",
            f"Date: {today}  |  Top candidates by price momentum + trend quality",
            "",
        ]
        for i, c in enumerate(top, 1):
            b = c["breakdown"]
            lines.append(
                f"{i}. <b>{c['symbol']}</b> [{c['segment']}] - Score: {c['score']}\n"
                f"   Momentum: {b['momentum_pct_of_52w_high']}% of 52w high | "
                f"Above EMA200: {'yes' if b['above_ema200'] else 'no'} | "
                f"Consistency: {b['trend_consistency_pct']}%"
            )

        if not top:
            lines.append("No symbols scored above 50 this week.")

        lines += [
            "",
            "Price-based proxy only. Not investment advice.",
            "Market investments involve risk and possible capital loss.",
        ]
        telegram.send(notifier._bot_token, notifier._chat_id, "\n".join(lines))  # noqa: SLF001
    except Exception:
        log.warning("Weekly Telegram notification failed", exc_info=True)
