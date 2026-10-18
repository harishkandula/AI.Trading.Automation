"""Weekly Lambda handler — compounder research + Telegram summary."""
from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from market_ai.models import Candidate
from market_ai.services.reporting import render_report
from market_ai.services.scoring import compounder_candidate

log = logging.getLogger(__name__)

IST = ZoneInfo("Asia/Kolkata")

SYNTHETIC = {
    "DEMOALPHA": {
        "revenue_cagr": 0.22,
        "profit_cagr": 0.27,
        "roce": 0.21,
        "debt_equity": 0.25,
        "cash_conversion": 0.90,
    },
    "DEMOBETA": {
        "revenue_cagr": 0.13,
        "profit_cagr": 0.16,
        "roce": 0.14,
        "debt_equity": 0.80,
        "cash_conversion": 0.65,
    },
    "DEMOGAMMA": {
        "revenue_cagr": 0.28,
        "profit_cagr": 0.31,
        "roce": 0.24,
        "debt_equity": 0.12,
        "cash_conversion": 1.05,
    },
}

_DISCLAIMER = (
    "\n\n⚠️ RESEARCH SIGNAL, NOT A TRADE INSTRUCTION. "
    "Market investments involve risk including possible capital loss."
)


def handler(event: object, context: object) -> dict:  # type: ignore[type-arg]
    """AWS Lambda entry-point for the weekly compounder research scan."""
    today = datetime.now(tz=IST).date().isoformat()
    items = [compounder_candidate(s, f, today) for s, f in SYNTHETIC.items()]
    result = render_report("Weekly compounder research", items)

    _notify(today, items)

    return result


def _notify(today: str, items: list[Candidate]) -> None:
    """Send compounder results via Telegram (non-fatal if it fails)."""
    try:
        from app.notifications import telegram
        from app.notifications.notifier import Notifier

        notifier = Notifier.from_secrets()

        lines = ["<b>Market AI — Weekly Compounder Research</b>", f"Date: {today}", ""]
        for item in items[:5]:
            lines.append(f"  <b>{item.symbol}</b> — score: {item.score:.2f}")
        lines.append(_DISCLAIMER)

        telegram.send(notifier._bot_token, notifier._chat_id, "\n".join(lines))  # noqa: SLF001
    except Exception:
        log.warning("Weekly Telegram notification failed", exc_info=True)
