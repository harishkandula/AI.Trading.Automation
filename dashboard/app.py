"""Market AI - Local Streamlit Dashboard.

Reads live data from AWS (DynamoDB + S3) and renders five pages:
  1. Live Signals
  2. Paper Trading P&L
  3. Strategy Leaderboard
  4. Backtest Results
  5. Market Regime

Required environment variables
--------------------------------
DATA_BUCKET       – S3 bucket containing backtest results and signals
STATE_TABLE       – DynamoDB table used for paper trades / signals / regime
LEADERBOARD_TABLE – DynamoDB table for strategy leaderboard

AWS credentials are resolved via the standard boto3 chain
(AWS_PROFILE, AWS_ACCESS_KEY_ID/SECRET, IAM role, etc.).

Usage
-----
    pip install -r dashboard/requirements.txt
    export STATE_TABLE=market-ai-dev-state
    export LEADERBOARD_TABLE=market-ai-dev-leaderboard
    export DATA_BUCKET=market-ai-dev-data-ap-south-1-xxxxx
    streamlit run dashboard/app.py
"""
from __future__ import annotations

import json
import os
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from typing import Any

import boto3
import pandas as pd
import streamlit as st

# ---------------------------------------------------------------------------
# Page config — must be the first Streamlit call
# ---------------------------------------------------------------------------
st.set_page_config(
    page_title="Market AI Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ---------------------------------------------------------------------------
# AWS resource helpers
# ---------------------------------------------------------------------------

_AWS_REGION = os.environ.get("AWS_DEFAULT_REGION", "ap-south-1")
_STATE_TABLE = os.environ.get("STATE_TABLE", "")
_LEADERBOARD_TABLE = os.environ.get("LEADERBOARD_TABLE", "")
_DATA_BUCKET = os.environ.get("DATA_BUCKET", "")


@st.cache_resource(show_spinner=False)
def _dynamo_resource():
    return boto3.resource("dynamodb", region_name=_AWS_REGION)


@st.cache_resource(show_spinner=False)
def _s3_client():
    return boto3.client("s3", region_name=_AWS_REGION)


def _from_dynamo(value: Any) -> Any:
    """Recursively convert Decimal → float for pandas compatibility."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def _scan_table(table_name: str, filter_expression=None, **kwargs) -> list[dict]:
    """Paginated DynamoDB table scan. Returns [] on any error."""
    if not table_name:
        return []
    try:
        table = _dynamo_resource().Table(table_name)
        scan_kwargs: dict[str, Any] = dict(kwargs)
        if filter_expression is not None:
            scan_kwargs["FilterExpression"] = filter_expression
        resp = table.scan(**scan_kwargs)
        items = [_from_dynamo(i) for i in resp.get("Items", [])]
        while "LastEvaluatedKey" in resp:
            scan_kwargs["ExclusiveStartKey"] = resp["LastEvaluatedKey"]
            resp = table.scan(**scan_kwargs)
            items.extend(_from_dynamo(i) for i in resp.get("Items", []))
        return items
    except Exception as exc:
        st.warning(f"DynamoDB scan failed ({table_name}): {exc}")
        return []


def _get_item(table_name: str, pk: str, sk: str) -> dict:
    """Single DynamoDB GetItem. Returns {} on any error."""
    if not table_name:
        return {}
    try:
        table = _dynamo_resource().Table(table_name)
        resp = table.get_item(Key={"pk": pk, "sk": sk})
        return _from_dynamo(resp.get("Item", {}))
    except Exception as exc:
        st.warning(f"DynamoDB get_item failed: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Cached data loaders (TTL = 5 min to avoid hammering AWS on every rerun)
# ---------------------------------------------------------------------------

@st.cache_data(ttl=300, show_spinner="Loading paper trades…")
def load_paper_trades() -> list[dict]:
    """Load all items from STATE_TABLE where pk starts with PAPER_TRADE."""
    if not _STATE_TABLE:
        return []
    try:
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415
        return _scan_table(_STATE_TABLE, filter_expression=Attr("pk").begins_with("PAPER_TRADE"))
    except Exception as exc:
        st.warning(f"Could not load paper trades: {exc}")
        return []


@st.cache_data(ttl=300, show_spinner="Loading signals…")
def load_signals(days: int = 7) -> list[dict]:
    """Load recent signals from STATE_TABLE (pk == SIGNAL or pk begins with SIGNAL)."""
    if not _STATE_TABLE:
        return []
    try:
        from boto3.dynamodb.conditions import Attr  # noqa: PLC0415
        cutoff = (date.today() - timedelta(days=days)).isoformat()
        items = _scan_table(
            _STATE_TABLE,
            filter_expression=(
                Attr("pk").begins_with("SIGNAL")
                & Attr("signal_date").gte(cutoff)
            ),
        )
        # Fallback: if no SIGNAL items, show recent paper trades as proxy
        if not items:
            trades = load_paper_trades()
            items = [t for t in trades if t.get("entry_date", "") >= cutoff]
        return items
    except Exception as exc:
        st.warning(f"Could not load signals: {exc}")
        return []


@st.cache_data(ttl=300, show_spinner="Loading leaderboard…")
def load_leaderboard() -> list[dict]:
    """Scan leaderboard table; keep newest run_date per strategy."""
    items = _scan_table(_LEADERBOARD_TABLE)
    if not items:
        return []
    latest: dict[str, dict] = {}
    for item in items:
        sid = item.get("pk", "")
        run_date = item.get("sk", "")
        if sid not in latest or run_date > latest[sid].get("sk", ""):
            latest[sid] = item
    entries = list(latest.values())
    entries.sort(key=lambda e: float(e.get("avg_sharpe") or 0), reverse=True)
    return entries


@st.cache_data(ttl=300, show_spinner="Loading regime…")
def load_regime() -> str:
    item = _get_item(_STATE_TABLE, "REGIME", "current")
    return str(item.get("regime", "UNKNOWN"))


@st.cache_data(ttl=300, show_spinner="Loading backtest results from S3…")
def load_latest_backtest() -> dict:
    """List backtest/*/results.json in S3 and load the newest one."""
    if not _DATA_BUCKET:
        return {}
    try:
        s3 = _s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        all_keys: list[str] = []
        for page in paginator.paginate(Bucket=_DATA_BUCKET, Prefix="backtest/"):
            for obj in page.get("Contents", []):
                key = obj["Key"]
                if key.endswith("results.json"):
                    all_keys.append(key)
        if not all_keys:
            return {}
        # Pick the key that sorts last (date-prefixed keys → latest = last)
        latest_key = sorted(all_keys)[-1]
        resp = s3.get_object(Bucket=_DATA_BUCKET, Key=latest_key)
        data = json.loads(resp["Body"].read())
        data["_s3_key"] = latest_key
        return data
    except Exception as exc:
        st.warning(f"Could not load backtest results from S3: {exc}")
        return {}


# ---------------------------------------------------------------------------
# Sidebar navigation
# ---------------------------------------------------------------------------

PAGES = [
    "Live Signals",
    "Paper Trading P&L",
    "Strategy Leaderboard",
    "Backtest Results",
    "Market Regime",
]

st.sidebar.title("Market AI")
st.sidebar.caption("Indian equity trading platform")
selected_page = st.sidebar.radio("Navigate to", PAGES)
st.sidebar.markdown("---")
st.sidebar.caption(
    f"Region: `{_AWS_REGION}`  \n"
    f"State table: `{_STATE_TABLE or 'not set'}`  \n"
    f"Leaderboard: `{_LEADERBOARD_TABLE or 'not set'}`  \n"
    f"Bucket: `{_DATA_BUCKET or 'not set'}`"
)
if st.sidebar.button("Refresh data"):
    st.cache_data.clear()
    st.rerun()


# ---------------------------------------------------------------------------
# Page 1: Live Signals
# ---------------------------------------------------------------------------

def page_live_signals() -> None:
    st.title("Live Signals")
    st.caption("Today's signals from DynamoDB (falls back to recent paper trades)")

    items = load_signals(days=7)

    if not items:
        st.info("No signals found. Check that STATE_TABLE is set and contains SIGNAL items.")
        return

    rows = []
    for item in items:
        rows.append({
            "Symbol": item.get("symbol") or item.get("sk", ""),
            "Strategy": item.get("strategy_id", ""),
            "Score": item.get("score") or item.get("composite_score", ""),
            "Entry Price": item.get("entry_price") or item.get("planned_entry_price", ""),
            "Stop": item.get("stop_price", ""),
            "Target": (item.get("target_prices") or [""])[0] if isinstance(item.get("target_prices"), list) else item.get("target_price", ""),
            "Status": item.get("status", ""),
            "Date": item.get("signal_date") or item.get("entry_date", ""),
        })

    df = pd.DataFrame(rows)
    if "Date" in df.columns:
        df = df.sort_values("Date", ascending=False)

    st.dataframe(df, use_container_width=True, hide_index=True)
    st.caption(f"Showing {len(df)} records")


# ---------------------------------------------------------------------------
# Page 2: Paper Trading P&L
# ---------------------------------------------------------------------------

def page_paper_trading() -> None:
    st.title("Paper Trading P&L")

    trades = load_paper_trades()

    if not trades:
        st.info(
            "No paper trades found. "
            "Check that STATE_TABLE is set and contains PAPER_TRADE items."
        )
        return

    # Normalise numeric fields
    for t in trades:
        for field in ("realized_pnl", "pnl", "entry_price", "exit_price"):
            val = t.get(field)
            if val is not None:
                try:
                    t[field] = float(val)
                except (TypeError, ValueError):
                    pass

    closed_statuses = {"CLOSED_TARGET", "CLOSED_STOP", "CLOSED_TIME", "CLOSED_EOD"}
    open_statuses = {"OPEN", "PARTIALLY_CLOSED", "PENDING_ENTRY"}

    closed = [t for t in trades if t.get("status", "") in closed_statuses]
    open_trades = [t for t in trades if t.get("status", "") in open_statuses]

    total_pnl = sum(float(t.get("realized_pnl") or t.get("pnl") or 0) for t in closed)
    wins = [t for t in closed if float(t.get("realized_pnl") or t.get("pnl") or 0) > 0]
    win_rate = (len(wins) / len(closed) * 100) if closed else 0.0

    # Summary cards
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total P&L (closed)", f"₹{total_pnl:,.0f}", delta=None)
    col2.metric("Win Rate", f"{win_rate:.1f}%")
    col3.metric("Open Positions", len(open_trades))
    col4.metric("Closed Trades", len(closed))

    st.markdown("---")

    # Full trades table
    st.subheader("All Paper Trades")
    rows = []
    for t in trades:
        rows.append({
            "Symbol": t.get("symbol", ""),
            "Strategy": t.get("strategy_id", ""),
            "Status": t.get("status", ""),
            "Entry Price": t.get("entry_price", ""),
            "Exit Price": t.get("exit_price", ""),
            "Qty": t.get("entry_quantity") or t.get("quantity", ""),
            "P&L": t.get("realized_pnl") or t.get("pnl") or "",
            "Entry Date": t.get("entry_date") or t.get("entry_timestamp", "")[:10] if isinstance(t.get("entry_timestamp"), str) else t.get("entry_date", ""),
            "Closed Date": t.get("closed_date", ""),
        })
    df = pd.DataFrame(rows)
    if "Entry Date" in df.columns:
        df = df.sort_values("Entry Date", ascending=False)
    st.dataframe(df, use_container_width=True, hide_index=True)

    # Cumulative P&L chart
    st.subheader("Cumulative P&L")
    if closed:
        pnl_rows = []
        for t in closed:
            d = t.get("closed_date", "") or ""
            pnl = float(t.get("realized_pnl") or t.get("pnl") or 0)
            if d:
                pnl_rows.append({"date": d, "pnl": pnl})

        if pnl_rows:
            pnl_df = pd.DataFrame(pnl_rows).sort_values("date")
            pnl_df["cumulative_pnl"] = pnl_df["pnl"].cumsum()
            pnl_df = pnl_df.set_index("date")
            st.line_chart(pnl_df["cumulative_pnl"])
        else:
            st.info("No closed-date data available to plot cumulative P&L.")
    else:
        st.info("No closed trades yet.")


# ---------------------------------------------------------------------------
# Page 3: Strategy Leaderboard
# ---------------------------------------------------------------------------

def page_leaderboard() -> None:
    st.title("Strategy Leaderboard")

    entries = load_leaderboard()

    if not entries:
        st.info(
            "No leaderboard data found. "
            "Check that LEADERBOARD_TABLE is set and the backtest Lambda has run."
        )
        return

    rows = []
    for e in entries:
        rows.append({
            "Strategy": e.get("pk", ""),
            "Version": e.get("strategy_version", ""),
            "Avg Sharpe": round(float(e.get("avg_sharpe") or 0), 2),
            "Avg Return %": round(float(e.get("avg_net_return_pct") or 0), 1),
            "Win Rate %": round(float(e.get("avg_win_rate_pct") or 0), 1),
            "Avg Max DD %": round(float(e.get("avg_max_drawdown_pct") or 0), 1),
            "OOS Trades": e.get("total_oos_trades", ""),
            "Windows": e.get("windows_count", ""),
            "Validated": "Yes" if e.get("validated") else "No",
            "Last Run": e.get("sk", ""),
        })

    df = pd.DataFrame(rows)

    # Bar chart: strategies ranked by avg_sharpe
    st.subheader("Avg Sharpe by Strategy")
    chart_df = df[["Strategy", "Avg Sharpe"]].set_index("Strategy")
    st.bar_chart(chart_df)

    st.subheader("Full Leaderboard")
    st.dataframe(df, use_container_width=True, hide_index=True)


# ---------------------------------------------------------------------------
# Page 4: Backtest Results
# ---------------------------------------------------------------------------

def page_backtest_results() -> None:
    st.title("Backtest Results")

    if not _DATA_BUCKET:
        st.warning("DATA_BUCKET environment variable is not set.")
        return

    data = load_latest_backtest()

    if not data:
        st.info(
            "No backtest results found in S3. "
            f"Expected objects matching `backtest/*/results.json` in bucket `{_DATA_BUCKET}`."
        )
        return

    s3_key = data.get("_s3_key", "")
    st.caption(f"Loaded from: `s3://{_DATA_BUCKET}/{s3_key}`")

    # Per-strategy metrics
    st.subheader("Per-Strategy Metrics")

    strategies_data = data.get("strategies") or data.get("results") or []
    if isinstance(strategies_data, list) and strategies_data:
        rows = []
        for s in strategies_data:
            if isinstance(s, dict):
                rows.append({
                    "Strategy": s.get("strategy_id") or s.get("id", ""),
                    "Return %": s.get("return_pct") or s.get("net_return_pct", ""),
                    "Sharpe": s.get("sharpe") or s.get("sharpe_ratio", ""),
                    "Max DD %": s.get("max_drawdown_pct") or s.get("max_dd_pct", ""),
                    "Win Rate %": s.get("win_rate_pct") or s.get("win_rate", ""),
                    "Total Trades": s.get("total_trades", ""),
                })
        if rows:
            st.dataframe(pd.DataFrame(rows), use_container_width=True, hide_index=True)
        else:
            st.json(strategies_data)
    elif isinstance(data, dict):
        # Flat results dict (single strategy or summary)
        display = {k: v for k, v in data.items() if not k.startswith("_")}
        st.json(display)

    # Equity curve
    equity = data.get("equity_curve") or data.get("equity")
    if equity:
        st.subheader("Equity Curve")
        if isinstance(equity, list):
            eq_df = pd.DataFrame(equity)
            if "date" in eq_df.columns:
                eq_df = eq_df.set_index("date")
            value_col = next((c for c in eq_df.columns if "value" in c.lower() or "equity" in c.lower()), eq_df.columns[0])
            st.line_chart(eq_df[value_col])
        elif isinstance(equity, dict):
            eq_df = pd.DataFrame(list(equity.items()), columns=["date", "value"]).set_index("date")
            st.line_chart(eq_df["value"])


# ---------------------------------------------------------------------------
# Page 5: Market Regime
# ---------------------------------------------------------------------------

_REGIME_DESCRIPTIONS = {
    "BULL": (
        "Strong uptrend — breadth is expanding and most indices are above their "
        "200-day moving averages. Trend-following and momentum strategies tend to "
        "perform best in this regime. Risk/reward favours long setups."
    ),
    "BEAR": (
        "Broad market decline — indices are trading below key moving averages and "
        "breadth is deteriorating. Most trend strategies reduce exposure or stand "
        "aside. Focus shifts to capital preservation."
    ),
    "SIDEWAYS": (
        "Range-bound market — no clear directional bias. Mean-reversion strategies "
        "may outperform. Trend-following strategies generate more whipsaws. "
        "Tight stop-losses and smaller position sizes are prudent."
    ),
    "RECOVERY": (
        "Market is rebounding from a prior decline but has not yet confirmed a new "
        "uptrend. Selective longs in leading sectors are appropriate. "
        "Risk management remains important as re-tests of lows are possible."
    ),
    "UNKNOWN": (
        "Regime data is not yet available. The regime detection job may not have "
        "run, or the STATE_TABLE does not contain a REGIME item. "
        "Treat the current environment as neutral."
    ),
}


def page_market_regime() -> None:
    st.title("Market Regime")

    if not _STATE_TABLE:
        st.warning("STATE_TABLE environment variable is not set.")

    regime = load_regime()

    colour = {
        "BULL": "green",
        "BEAR": "red",
        "SIDEWAYS": "orange",
        "RECOVERY": "blue",
        "UNKNOWN": "grey",
    }.get(regime, "grey")

    st.markdown(
        f"<h2 style='color:{colour};'>{regime}</h2>",
        unsafe_allow_html=True,
    )

    description = _REGIME_DESCRIPTIONS.get(regime, _REGIME_DESCRIPTIONS["UNKNOWN"])
    st.info(description)

    st.markdown("---")
    st.subheader("What each regime means for your strategies")

    for r, desc in _REGIME_DESCRIPTIONS.items():
        if r == "UNKNOWN":
            continue
        with st.expander(r):
            st.write(desc)


# ---------------------------------------------------------------------------
# Router
# ---------------------------------------------------------------------------

if selected_page == "Live Signals":
    page_live_signals()
elif selected_page == "Paper Trading P&L":
    page_paper_trading()
elif selected_page == "Strategy Leaderboard":
    page_leaderboard()
elif selected_page == "Backtest Results":
    page_backtest_results()
elif selected_page == "Market Regime":
    page_market_regime()
