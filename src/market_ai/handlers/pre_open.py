"""Pre-open Lambda handler — runs at 09:05 IST Mon-Fri.

Reads NSE pre-open indicative equilibrium prices (IEP) for NIFTY 500
constituents, cross-references with morning scan signals already in
DynamoDB, scores each candidate, and sends a ranked Telegram watchlist
10 minutes before the market opens.

This handler is intentionally independent of S3 data freshness.
It fetches live order-book data directly from NSE at run time,
so it works even on days when the data pipeline is stale or broken.

Schedule: cron(35 3 ? * MON-FRI *)  =  09:05 IST Mon-Fri
"""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
IST = ZoneInfo("Asia/Kolkata")


def _fetch_nse_preopen() -> list[dict]:
    """Fetch NSE pre-open IEP data for NIFTY 500 constituents."""
    import urllib.request
    import json

    url = "https://www.nseindia.com/api/market-data-pre-open?key=NIFTY500"
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/120.0.0.0 Safari/537.36"
        ),
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://www.nseindia.com/market-data/pre-open-market-cm-and-emerge-market",
    }

    cookies = ""
    try:
        req0 = urllib.request.Request("https://www.nseindia.com", headers=headers)
        with urllib.request.urlopen(req0, timeout=10) as resp:
            raw_cookies = resp.headers.get("Set-Cookie", "")
            parts = [p.strip() for p in raw_cookies.split(",")]
            cookie_pairs = []
            for part in parts:
                first = part.split(";")[0].strip()
                if "=" in first and not any(
                    first.lower().startswith(x)
                    for x in ("path", "domain", "expires", "max-age", "samesite", "secure")
                ):
                    cookie_pairs.append(first)
            cookies = "; ".join(cookie_pairs)
    except Exception:
        log.debug("Could not pre-fetch NSE session cookie", exc_info=True)

    req = urllib.request.Request(url, headers={**headers, "Cookie": cookies})
    with urllib.request.urlopen(req, timeout=15) as resp:
        data = json.loads(resp.read())

    results = []
    for item in data.get("data", []):
        meta = item.get("metadata", {})
        detail = item.get("detail", {}).get("preOpenMarket", {})
        symbol = meta.get("symbol", "")
        iep = meta.get("iep")
        prev_close = meta.get("previousClose")
        if not symbol or iep is None or prev_close is None:
            continue
        try:
            iep_f = float(str(iep).replace(",", ""))
            pc_f = float(str(prev_close).replace(",", ""))
        except (ValueError, TypeError):
            continue
        if pc_f == 0:
            continue
        gap_pct = (iep_f - pc_f) / pc_f * 100
        results.append({
            "symbol": symbol,
            "iep": iep_f,
            "prev_close": pc_f,
            "gap_pct": round(gap_pct, 2),
            "buy_qty": int(detail.get("totalBuyQuantity") or 0),
            "sell_qty": int(detail.get("totalSellQuantity") or 0),
            "ato_buy": int(detail.get("atoBuyQty") or 0),
            "ato_sell": int(detail.get("atoSellQty") or 0),
        })
    return results


def _fetch_gift_nifty() -> float | None:
    """Fetch GIFT Nifty futures level as a proxy for market direction."""
    import urllib.request
    import json
    try:
        url = "https://www.nseindia.com/api/futureCurve?index=NIFTY"
        headers = {
            "User-Agent": "Mozilla/5.0",
            "Accept": "application/json",
            "Referer": "https://www.nseindia.com",
        }
        req = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        for item in data.get("data", []):
            if "GIFT" in str(item.get("underlying", "")).upper():
                return float(item.get("lastPrice", 0) or 0) or None
        return None
    except Exception:
        log.debug("Could not fetch GIFT Nifty", exc_info=True)
        return None


def _load_morning_signals(today_str: str) -> dict[str, dict]:
    """Return {symbol: signal_item} for signals written by morning handler today."""
    import os
    import boto3
    from boto3.dynamodb.conditions import Key
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name:
        return {}
    try:
        table = boto3.resource("dynamodb").Table(table_name)
        resp = table.query(KeyConditionExpression=Key("pk").eq(f"SIGNAL_{today_str}"))
        return {item["symbol"]: item for item in resp.get("Items", [])}
    except Exception:
        log.debug("Could not load morning signals from DynamoDB", exc_info=True)
        return {}


def _load_regime() -> str:
    import os
    import boto3
    table_name = os.environ.get("STATE_TABLE", "")
    if not table_name:
        return "UNKNOWN"
    try:
        table = boto3.resource("dynamodb").Table(table_name)
        resp = table.get_item(Key={"pk": "REGIME", "sk": "current"})
        return str(resp.get("Item", {}).get("regime", "UNKNOWN"))
    except Exception:
        return "UNKNOWN"


def _score_candidate(item: dict, morning_signal: dict | None) -> int:
    """
    Score a pre-open candidate 0-100.

    Components:
      Gap quality   0-30  (size + direction; extreme gaps penalised)
      Order book    0-25  (buy/sell imbalance in pre-open)
      Morning sig   0-30  (presence + morning score)
      ATO interest  0-15  (at-open orders signal conviction)
    """
    score = 0
    gap = item["gap_pct"]

    if 0.5 <= gap < 1.0:
        score += 15
    elif 1.0 <= gap < 2.0:
        score += 25
    elif 2.0 <= gap < 3.0:
        score += 30
    elif 3.0 <= gap < 4.5:
        score += 20
    elif gap >= 4.5:
        score += 8
    elif -1.0 < gap < 0.5:
        score += 5

    buy_qty = item["buy_qty"]
    sell_qty = item["sell_qty"]
    total_qty = buy_qty + sell_qty
    if total_qty > 0:
        buy_ratio = buy_qty / total_qty
        if buy_ratio >= 0.75:
            score += 25
        elif buy_ratio >= 0.65:
            score += 18
        elif buy_ratio >= 0.55:
            score += 10
        elif buy_ratio >= 0.45:
            score += 5

    if morning_signal:
        morning_score = float(morning_signal.get("score") or 0)
        score += min(30, int(morning_score * 0.30))

    ato_total = item["ato_buy"] + item["ato_sell"]
    if ato_total > 0:
        ato_buy_ratio = item["ato_buy"] / ato_total
        if ato_buy_ratio >= 0.70 and ato_total > 10000:
            score += 15
        elif ato_buy_ratio >= 0.60:
            score += 8
        elif ato_buy_ratio >= 0.50:
            score += 4

    return min(score, 100)


def _ai_commentary(
    top_candidates: list[dict],
    regime: str,
    gift_nifty: float | None,
) -> str:
    import json as _json
    import os
    openai_key = ""
    try:
        import boto3
        secret_name = f"{os.environ.get('APP_NAME', 'market-ai-dev')}/openai"
        client = boto3.client("secretsmanager", region_name="ap-south-1")
        resp = client.get_secret_value(SecretId=secret_name)
        secret = _json.loads(resp["SecretString"])
        openai_key = secret.get("api_key", "")
    except Exception:
        log.debug("Could not load OpenAI key", exc_info=True)
        return ""
    if not openai_key:
        return ""
    try:
        import urllib.request
        lines = []
        for c in top_candidates[:5]:
            sig_note = " [morning signal]" if c.get("has_morning_signal") else ""
            lines.append(
                f"- {c['symbol']}: gap {c['gap_pct']:+.1f}%, "
                f"score {c['total_score']}, "
                f"sector {c.get('sector', 'unknown')}{sig_note}"
            )
        gift_text = f"{gift_nifty:.0f}" if gift_nifty else "unavailable"
        prompt = (
            f"Indian equity market pre-open analysis "
            f"(NSE, {datetime.now(tz=IST).strftime('%d %b %Y')}).\n"
            f"Market regime: {regime}\nGIFT Nifty: {gift_text}\n"
            f"Top pre-open candidates by score:\n" + "\n".join(lines) + "\n\n"
            "In exactly 2-3 sentences, comment on what this data suggests for the "
            "first 30 minutes of trade. Focus on: which setups look strongest, "
            "any sector theme, and one risk to watch. Be specific and concise. "
            "Do not use bullet points."
        )
        payload = _json.dumps({
            "model": "gpt-4o",
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 150,
            "temperature": 0.4,
        }).encode()
        req = urllib.request.Request(
            "https://api.openai.com/v1/chat/completions",
            data=payload,
            headers={
                "Authorization": f"Bearer {openai_key}",
                "Content-Type": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=20) as resp:
            result = _json.loads(resp.read())
        return result["choices"][0]["message"]["content"].strip()
    except Exception:
        log.debug("OpenAI commentary failed", exc_info=True)
        return ""


def _format_message(
    candidates: list[dict],
    regime: str,
    gift_nifty: float | None,
    ai_text: str,
    today_str: str,
    morning_signal_count: int,
) -> str:
    gift_line = (
        f"GIFT Nifty: <b>{gift_nifty:,.0f}</b>" if gift_nifty else "GIFT Nifty: unavailable"
    )
    regime_emoji = {
        "STRONG_BULL": "\U0001f7e2\U0001f7e2",
        "BULL": "\U0001f7e2",
        "SIDEWAYS": "\U0001f7e1",
        "CORRECTION": "\U0001f7e0",
        "BEAR": "\U0001f534",
        "RECOVERY": "\U0001f535",
        "UNKNOWN": "⚪",
    }.get(regime, "⚪")
    lines = [
        f"<b>\U0001f514 Pre-Open Watchlist — {today_str}</b>",
        f"{gift_line}  |  Regime: {regime_emoji} {regime}",
        f"Morning signals today: {morning_signal_count}",
        "",
    ]
    if not candidates:
        lines.append("No strong pre-open candidates found.")
    else:
        for i, c in enumerate(candidates[:8], 1):
            gap = c["gap_pct"]
            gap_str = f"+{gap:.1f}%" if gap >= 0 else f"{gap:.1f}%"
            signal_tag = " ★" if c.get("has_morning_signal") else ""
            ob_ratio = c.get("buy_ratio", 0)
            ob_str = f"OB {ob_ratio:.0%} buy" if ob_ratio else ""
            lines.append(
                f"{i}. <b>{c['symbol']}</b>{signal_tag} "
                f"↗ {gap_str} | Score {c['total_score']} "
                f"| {c.get('sector', '')} | {ob_str}"
            )
    if ai_text:
        lines += ["", "\U0001f916 <i>" + ai_text + "</i>"]
    lines += ["", "<i>Pre-open IEP data. Not a buy recommendation.</i>"]
    return "\n".join(lines)


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """Lambda entry-point: 09:05 IST Mon-Fri pre-open scan."""
    import json as _json
    import os
    import boto3
    from app.notifications import telegram
    from app.data.nifty500_symbols import SYMBOL_TO_SECTOR

    if isinstance(event, dict) and event.get("_prewarm"):
        return {"statusCode": 200, "body": "warm"}

    today = datetime.now(tz=IST).date()
    today_str = today.isoformat()

    morning_signals = _load_morning_signals(today_str)
    regime = _load_regime()
    log.info("Regime: %s | Morning signals: %d", regime, len(morning_signals))

    gift_nifty: float | None = None
    try:
        gift_nifty = _fetch_gift_nifty()
    except Exception:
        log.debug("GIFT Nifty fetch failed", exc_info=True)

    iep_data: list[dict] = []
    try:
        iep_data = _fetch_nse_preopen()
        log.info("NSE pre-open: %d symbols fetched", len(iep_data))
    except Exception:
        log.warning("NSE pre-open fetch failed", exc_info=True)

    secret_name = os.environ.get("TELEGRAM_SECRET", "market-ai-dev/telegram")

    def _get_telegram_creds() -> tuple[str, str]:
        c = boto3.client("secretsmanager", region_name="ap-south-1")
        sec = _json.loads(c.get_secret_value(SecretId=secret_name)["SecretString"])
        return sec["bot_token"], sec["chat_id"]

    if not iep_data:
        try:
            bot, chat = _get_telegram_creds()
            telegram.send(
                bot, chat,
                f"⚠️ Pre-open ({today_str}): NSE IEP data unavailable. "
                f"Morning signals: {len(morning_signals)}. Regime: {regime}.",
            )
        except Exception:
            log.warning("Could not send fallback Telegram alert", exc_info=True)
        return {"statusCode": 200, "body": {"status": "no_iep_data", "date": today_str}}

    candidates = []
    for item in iep_data:
        symbol = item["symbol"]
        morning_sig = morning_signals.get(symbol)
        total_score = _score_candidate(item, morning_sig)
        total_qty = item["buy_qty"] + item["sell_qty"]
        buy_ratio = item["buy_qty"] / total_qty if total_qty > 0 else 0
        candidates.append({
            **item,
            "has_morning_signal": morning_sig is not None,
            "morning_score": float(morning_sig.get("score") or 0) if morning_sig else 0,
            "morning_strategy": morning_sig.get("strategy_id", "") if morning_sig else "",
            "sector": SYMBOL_TO_SECTOR.get(symbol, "Unknown"),
            "buy_ratio": round(buy_ratio, 3),
            "total_score": total_score,
        })

    candidates.sort(
        key=lambda c: (c["total_score"] + (10 if c["has_morning_signal"] else 0)),
        reverse=True,
    )
    top = [c for c in candidates if c["gap_pct"] > 0][:8]

    ai_text = ""
    try:
        ai_text = _ai_commentary(top, regime, gift_nifty)
    except Exception:
        log.debug("AI commentary skipped", exc_info=True)

    msg = _format_message(
        candidates=top,
        regime=regime,
        gift_nifty=gift_nifty,
        ai_text=ai_text,
        today_str=today_str,
        morning_signal_count=len(morning_signals),
    )

    sent = False
    try:
        bot, chat = _get_telegram_creds()
        sent = telegram.send(bot, chat, msg)
    except Exception:
        log.exception("Failed to send pre-open Telegram message")

    try:
        from decimal import Decimal
        table_name = os.environ.get("STATE_TABLE", "")
        if table_name:
            table = boto3.resource("dynamodb").Table(table_name)
            with table.batch_writer() as batch:
                for c in candidates[:20]:
                    batch.put_item(Item={
                        "pk": f"PREOPEN_{today_str}",
                        "sk": c["symbol"],
                        "symbol": c["symbol"],
                        "gap_pct": Decimal(str(c["gap_pct"])),
                        "iep": Decimal(str(c["iep"])),
                        "total_score": c["total_score"],
                        "has_morning_signal": c["has_morning_signal"],
                        "sector": c["sector"],
                        "buy_ratio": Decimal(str(c["buy_ratio"])),
                        "date": today_str,
                    })
    except Exception:
        log.debug("Could not persist pre-open candidates to DynamoDB", exc_info=True)

    return {
        "statusCode": 200,
        "body": {
            "date": today_str,
            "iep_symbols": len(iep_data),
            "top_candidates": len(top),
            "morning_signals_matched": sum(1 for c in top if c["has_morning_signal"]),
            "telegram_sent": sent,
            "regime": regime,
        },
    }
