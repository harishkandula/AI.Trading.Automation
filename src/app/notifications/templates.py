"""Report templates for Telegram and email."""
from __future__ import annotations

from datetime import date
from typing import Any

from app.models.signal import Signal

DISCLAIMER = (
    "\n\n⚠️ RESEARCH SIGNAL, NOT A TRADE INSTRUCTION. "
    "Market investments involve risk including possible capital loss. "
    "Verify all information with original sources."
)

_TELEGRAM_MAX_CHARS = 4096


def format_signal(sig: dict[str, Any]) -> str:
    lines = [
        f"<b>Strategy:</b> {sig.get('strategy_id', 'unknown')}",
        f"<b>Symbol:</b> {sig.get('symbol', 'unknown')}",
        f"<b>Signal date:</b> {sig.get('decision_timestamp', '')[:10]}",
        "<b>Planned execution:</b> Next session open",
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
        (
            f"Universe: {universe_size} | Scanned: {scanned}"
            f" | Rejected (data): {rejected_data_quality}"
        ),
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


# ---------------------------------------------------------------------------
# New Signal-model templates (accept app.models.signal.Signal instances)
# ---------------------------------------------------------------------------

def _format_signal_block(sig: Signal) -> str:
    """Render a single Signal as an HTML block for Telegram."""
    symbol = sig.symbol.replace(".NS", "")
    entry = f"{sig.entry_reference_price:.2f}" if sig.entry_reference_price else "N/A"
    stop = "N/A"
    stop_pct = ""
    if sig.stop_price and sig.entry_reference_price:
        stop = f"{sig.stop_price:.2f}"
        pct = (sig.entry_reference_price - sig.stop_price) / sig.entry_reference_price * 100
        stop_pct = f" ({pct:.1f}% below entry)"

    targets_str = "N/A"
    if sig.target_prices:
        targets_str = ", ".join(f"{t:.2f}" for t in sig.target_prices)

    hold = f"{sig.holding_period_sessions}d" if sig.holding_period_sessions else "N/A"

    key_ind = ""
    if sig.indicators:
        parts = [f"{k}={v}" for k, v in list(sig.indicators.items())[:3]]
        key_ind = f"\n  <i>Indicators:</i> {', '.join(parts)}"

    return (
        f"<b>{symbol}</b> — <code>{sig.strategy_id}</code>\n"
        f"  Entry ref: {entry} | Stop: {stop}{stop_pct}\n"
        f"  Targets: {targets_str} | Hold: {hold}{key_ind}"
    )


def format_signal_alert(signals: list[Signal], scan_date: date) -> str:
    """Rich HTML message for Telegram listing multiple signals.

    Groups by strategy. Truncated to Telegram's 4096-char limit.
    """
    header = (
        f"<b>Market AI — Signal Alert</b>\n"
        f"Date: {scan_date}  |  Signals: {len(signals)}\n"
    )

    # Group by strategy
    by_strategy: dict[str, list[Signal]] = {}
    for sig in signals:
        by_strategy.setdefault(sig.strategy_id, []).append(sig)

    sections: list[str] = []
    for strategy_id, grp in by_strategy.items():
        section_lines = [f"\n<b>Strategy: {strategy_id}</b>"]
        for sig in grp:
            section_lines.append(_format_signal_block(sig))
        sections.append("\n".join(section_lines))

    body = "\n".join(sections)
    full = header + body + DISCLAIMER

    if len(full) > _TELEGRAM_MAX_CHARS:
        cutoff = _TELEGRAM_MAX_CHARS - len(DISCLAIMER) - 50
        full = full[:cutoff] + "\n…(truncated)" + DISCLAIMER

    return full


def format_daily_summary(
    scan_date: date,
    universe_size: int,
    scanned: int,
    signals: list[Signal],
    errors: int = 0,
) -> str:
    """Daily scan summary for the Notifier."""
    sig_count = len(signals)
    status = "✅ Signals found" if sig_count else "— No signals today"
    err_line = f"  Errors: {errors}\n" if errors else ""

    lines = [
        f"<b>Market AI Daily Summary — {scan_date}</b>",
        f"Universe: {universe_size} | Scanned: {scanned}",
        f"Signals: {sig_count}  {status}",
        err_line.rstrip(),
    ]
    if sig_count:
        lines.append("")
        for sig in signals[:5]:
            lines.append(_format_signal_block(sig))
    lines.append(DISCLAIMER)
    return "\n".join(lo for lo in lines if lo is not None)


def format_error_alert(context: str, error: str) -> str:
    """Error notification message."""
    return (
        f"<b>Market AI — Error Alert</b>\n"
        f"<b>Context:</b> {context}\n"
        f"<b>Error:</b> <code>{error[:500]}</code>"
    )
