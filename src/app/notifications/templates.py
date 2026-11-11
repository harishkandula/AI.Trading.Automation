"""Report templates for Telegram and email."""
from __future__ import annotations

from collections import defaultdict
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
    """Rich HTML message for Telegram listing signals ranked by score.

    Signals are expected to be pre-sorted by score (highest first).
    Truncated to Telegram's 4096-char limit.
    """
    header = (
        f"<b>Market AI — Signal Alert</b>\n"
        f"Date: {scan_date}  |  Signals: {len(signals)}\n\n"
    )

    blocks: list[str] = []
    for rank, sig in enumerate(signals, 1):
        score = sig.indicators.get("score")
        score_str = f" [Score: {score}]" if score is not None else ""
        regime = sig.indicators.get("regime", "")
        regime_str = f" | Regime: {regime}" if regime else ""
        rs = sig.indicators.get("rs_percentile")
        rs_str = f" | RS: {rs:.0f}th" if rs is not None else ""
        block = (
            f"<b>{rank}. {sig.symbol.replace('.NS', '')}</b>"
            f"{score_str}{regime_str}{rs_str}\n"
            f"{_format_signal_block(sig)}"
        )
        blocks.append(block)

    body = "\n\n".join(blocks)
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
    data_lag_days: int = 0,
) -> str:
    """Daily scan summary for the Notifier."""
    sig_count = len(signals)
    if sig_count:
        status = "✅ Signals found"
    elif data_lag_days > 1:
        status = f"⚠️ Data stale ({data_lag_days} days behind) — pipeline check needed"
    else:
        status = "— No signals (quiet market)"
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


def format_pipeline_health(
    run_date: date,
    rows: int,
    symbols: int,
    adjusted: int = 0,
) -> str:
    """Daily data pipeline health ping sent after a successful run."""
    adj_line = f"  Split-adjusted: {adjusted} symbols\n" if adjusted else ""
    return (
        f"<b>Market AI — Data Pipeline</b> ✅\n"
        f"Date: {run_date} | Rows: {rows} | Symbols: {symbols}\n"
        f"{adj_line}"
        f"S3 is fresh — morning scan ready."
    )


# ---------------------------------------------------------------------------
# Weekly performance report
# ---------------------------------------------------------------------------

def _r_mult(entry: float, exit_: float, stop: float) -> float | None:
    """R-multiple for a closed trade. Returns None when stop is missing or invalid."""
    if not stop or stop >= entry or entry <= 0:
        return None
    return round((exit_ - entry) / (entry - stop), 2)


def format_weekly_report(
    week_end: date,
    closed_trades: list[dict],
    open_trades: list[dict],
    signals_by_strategy: dict[str, int],
    regime: str = "UNKNOWN",
) -> str:
    """Weekly performance summary for the dedicated weekly Telegram channel."""
    total_signals = sum(signals_by_strategy.values())
    lines: list[str] = [
        "<b>Market AI — Weekly Performance Report</b>",
        f"Week ending {week_end}  |  Regime: {regime}",
        "",
    ]

    # ── Closed trades ──────────────────────────────────────────────────────
    if not closed_trades:
        lines += [
            "Quiet week — no paper trades closed.",
            f"Signals fired this week: {total_signals}",
            "",
        ]
    else:
        agg: dict[str, dict] = defaultdict(
            lambda: {"cls": 0, "wins": 0, "r_sum": 0.0, "r_n": 0}
        )
        best_r: float | None = None
        best_label = ""
        worst_r: float | None = None
        worst_label = ""

        for t in closed_trades:
            sid = t.get("strategy_id") or "unknown"
            a = agg[sid]
            a["cls"] += 1
            entry = float(t.get("entry_price") or 0)
            exit_ = float(t.get("exit_price") or 0)
            stop = float(t.get("stop_price") or 0)
            r = _r_mult(entry, exit_, stop)
            sym = (t.get("symbol") or "?").replace(".NS", "")
            if r is not None:
                if r > 0:
                    a["wins"] += 1
                a["r_sum"] += r
                a["r_n"] += 1
                label = f"{sym} ({sid})"
                if best_r is None or r > best_r:
                    best_r, best_label = r, label
                if worst_r is None or r < worst_r:
                    worst_r, worst_label = r, label
            elif exit_ > entry:
                a["wins"] += 1

        col_w = 24
        sep = "─" * (col_w + 17)
        lines.append("<b>Closed Trades This Week</b>")
        lines.append("<pre>")
        lines.append(f"{'Strategy':<{col_w}} {'Cls':>3} {'Win%':>5} {'AvgR':>6}")
        lines.append(sep)

        all_sids = sorted(set(list(signals_by_strategy) + list(agg)))
        tot_cls = tot_wins = tot_r_n = 0
        tot_r_sum = 0.0
        for sid in all_sids:
            a = agg.get(sid, {})
            n = a.get("cls", 0)
            w = a.get("wins", 0)
            rs = a.get("r_sum", 0.0)
            rn = a.get("r_n", 0)
            wp = f"{w/n*100:.0f}%" if n else "—"
            ar = f"{rs/rn:+.1f}R" if rn else "—"
            lines.append(f"{sid[:col_w]:<{col_w}} {n:>3} {wp:>5} {ar:>6}")
            tot_cls += n
            tot_wins += w
            tot_r_sum += rs
            tot_r_n += rn

        lines.append(sep)
        twp = f"{tot_wins/tot_cls*100:.0f}%" if tot_cls else "—"
        tar = f"{tot_r_sum/tot_r_n:+.1f}R" if tot_r_n else "—"
        lines.append(f"{'Total':<{col_w}} {tot_cls:>3} {twp:>5} {tar:>6}")
        lines.append("</pre>")

        if best_r is not None:
            lines.append(f"Best:  {best_label}  {best_r:+.1f}R")
        if worst_r is not None:
            lines.append(f"Worst: {worst_label}  {worst_r:+.1f}R")
        lines.append(f"Signals fired this week: {total_signals}")
        lines.append("")

    # ── Open positions ────────────────────────────────────────────────
    if open_trades:
        lines.append(f"<b>Open Positions ({len(open_trades)})</b>")
        for t in open_trades:
            sym = (t.get("symbol") or "?").replace(".NS", "")
            sid = t.get("strategy_id") or "?"
            entry = float(t.get("entry_price") or 0)
            cmp = t.get("cmp")
            entry_date = t.get("entry_date", "")
            if cmp and entry:
                pct = (cmp - entry) / entry * 100
                price_info = f"entry {entry:.2f} | last {cmp:.2f} | {pct:+.1f}%"
            else:
                price_info = f"entry {entry:.2f}"
            lines.append(f"• <b>{sym}</b> ({sid}): {price_info} [{entry_date}]")
    else:
        lines.append("<b>Open Positions</b>: None")

    lines += ["", "Research only. Not investment advice."]
    return "\n".join(lines)
