"""Report templates for Telegram and email."""
from __future__ import annotations

from datetime import date
from typing import Any

DISCLAIMER = (
    "\n\n⚠️ RESEARCH SIGNAL, NOT A TRADE INSTRUCTION. "
    "Market investments involve risk including possible capital loss. "
    "Verify all information with original sources."
)


def format_signal(sig: dict[str, Any]) -> str:
    lines = [
        f"<b>Strategy:</b> {sig.get('strategy_id', 'unknown')}",
        f"<b>Symbol:</b> {sig.get('symbol', 'unknown')}",
        f"<b>Signal date:</b> {sig.get('decision_timestamp', '')[:10]}",
        f"<b>Planned execution:</b> Next session open",
        f"<b>Entry ref:</b> {sig.get('entry_reference_price', 'N/A')}",
        f"<b>Initial stop:</b> {sig.get('stop_price', 'N/A')}",
        f"<b>Holding period:</b> {sig.get('holding_period_sessions', 'N/A')} sessions",
        "<b>Reasons:</b>",
    ]
    for r in sig.get("qualification_reasons", []):
        lines.append(f"  - {r}")
    return "\n".join(lines)


def format_daily_report(
    scan_date: date,
    universe_size: int,
    scanned: int,
    rejected_data_quality: int,
    signals: list[dict[str, Any]],
    paper_positions: list[dict[str, Any]] | None = None,
) -> str:
    lines = [
        f"<b>Market AI Daily Report – {scan_date}</b>",
        f"Universe: {universe_size} | Scanned: {scanned} | Rejected (data): {rejected_data_quality}",
        f"Signals found: {len(signals)}",
        "",
    ]
    for sig in signals[:5]:  # top 5
        lines.append(format_signal(sig))
        lines.append("")

    if paper_positions:
        lines.append(f"<b>Open paper positions:</b> {len(paper_positions)}")

    lines.append(DISCLAIMER)
    return "\n".join(lines)
