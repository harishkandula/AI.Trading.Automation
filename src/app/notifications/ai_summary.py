"""OpenAI-powered weekly market summary generator.

Calls GPT-4o to produce a concise natural-language recap of the week's
activity: market regime, top strategy, signals generated, paper trades closed,
and a forward-looking sentence for next week.

This module is intentionally self-contained — it imports only the ``openai``
package (optional dep) and the Python standard library. On any failure it
returns an empty string so callers can degrade gracefully.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def generate_weekly_summary(
    top_strategy: str,
    ranked_strategies: list[dict],
    signals_this_week: list[dict],
    paper_trades_closed: list[dict],
    regime: str,
    openai_api_key: str,
) -> str:
    """Call OpenAI GPT-4o to produce a natural-language weekly market summary.

    Parameters
    ----------
    top_strategy:
        Strategy ID with best performance this week (e.g. ``"s01_rs_momentum"``).
    ranked_strategies:
        Ordered list of strategy dicts from the leaderboard (pk, avg_sharpe, …).
    signals_this_week:
        List of signal dicts generated during the current week.
    paper_trades_closed:
        List of paper trade dicts closed this week, each expected to have a
        ``realized_pnl`` key (or ``pnl``) for net P&L calculation.
    regime:
        Current market regime label (e.g. ``"BULL"``, ``"BEAR"``, ``"SIDEWAYS"``).
    openai_api_key:
        OpenAI API key. If empty, the function returns ``""`` immediately.

    Returns
    -------
    str
        A 5-7 sentence plain-English summary, or ``""`` on any error.
    """
    if not openai_api_key:
        log.warning("generate_weekly_summary: no OpenAI API key provided — skipping")
        return ""

    try:
        from openai import OpenAI  # noqa: PLC0415
    except ImportError:
        log.warning(
            "generate_weekly_summary: 'openai' package not installed — skipping. "
            "Install with: pip install openai"
        )
        return ""

    try:
        # --- derived values for prompt ----------------------------------------
        unique_symbols: int = len({s.get("symbol", "") for s in signals_this_week})

        net_pnl: float = 0.0
        for t in paper_trades_closed:
            pnl = t.get("realized_pnl") or t.get("pnl") or 0.0
            try:
                net_pnl += float(pnl)
            except (TypeError, ValueError):
                pass

        # Compact strategy ranking for the prompt (top-5 only to save tokens)
        ranking_summary = [
            {
                "strategy": e.get("pk", "?"),
                "avg_sharpe": round(float(e.get("avg_sharpe", 0)), 2),
                "avg_return_pct": round(float(e.get("avg_net_return_pct", 0)), 1),
                "win_rate_pct": round(float(e.get("avg_win_rate_pct", 0)), 1),
            }
            for e in ranked_strategies[:5]
        ]

        prompt = (
            "You are a concise Indian equity market analyst. "
            "Write a brief weekly summary (5-7 sentences, plain English, no markdown) covering:\n"
            f"1. Market regime: {regime}\n"
            f"2. Best performing strategy this week: {top_strategy}\n"
            f"3. Strategy ranking: {ranking_summary}\n"
            f"4. New signals generated: {len(signals_this_week)} signals across "
            f"{unique_symbols} symbols\n"
            f"5. Paper trades closed this week: {len(paper_trades_closed)} trades, "
            f"net P&L ₹{net_pnl:,.0f}\n"
            "Keep it factual, objective, and actionable. "
            "End with one sentence on what to watch next week."
        )

        client = OpenAI(api_key=openai_api_key)
        response = client.chat.completions.create(
            model="gpt-4o",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=500,
            temperature=0.4,
        )
        summary: str = response.choices[0].message.content or ""
        log.info(
            "generate_weekly_summary: generated %d chars from GPT-4o", len(summary)
        )
        return summary.strip()

    except Exception as exc:
        log.warning("generate_weekly_summary failed: %s: %s", type(exc).__name__, exc)
        return ""
