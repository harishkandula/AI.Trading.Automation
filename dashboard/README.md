# Market AI Dashboard

A local Streamlit dashboard that reads live data from AWS (DynamoDB + S3) and
displays five pages: Live Signals, Paper Trading P&L, Strategy Leaderboard,
Backtest Results, and Market Regime.

## Prerequisites

- Python 3.10+
- AWS credentials configured (profile, env vars, or IAM role)
- The Market AI infrastructure deployed (STATE_TABLE, LEADERBOARD_TABLE, DATA_BUCKET)

## Setup

```bash
pip install -r dashboard/requirements.txt
```

## Running

```bash
export AWS_PROFILE=default  # or set AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY
export DATA_BUCKET=market-ai-dev-data-ap-south-1-xxxxx
export STATE_TABLE=market-ai-dev-state
export LEADERBOARD_TABLE=market-ai-dev-leaderboard
streamlit run dashboard/app.py
```

The dashboard opens at http://localhost:8501.

## Pages

| Page | Data source | Key metrics |
|------|------------|-------------|
| Live Signals | STATE_TABLE (pk=SIGNAL*) | Symbol, strategy, score, entry/stop/target |
| Paper Trading P&L | STATE_TABLE (pk=PAPER_TRADE*) | Total P&L, win rate, cumulative equity curve |
| Strategy Leaderboard | LEADERBOARD_TABLE | Sharpe, return %, win rate, drawdown |
| Backtest Results | S3 `backtest/*/results.json` | Per-strategy metrics, equity curve |
| Market Regime | STATE_TABLE (pk=REGIME, sk=current) | Current regime + description |

## Tips

- Click **Refresh data** in the sidebar to clear the 5-minute cache and reload
  from AWS.
- If any table/bucket is not set, that page shows a friendly warning instead
  of crashing.
- All AWS calls are wrapped in try/except — a missing table or expired
  credentials shows a warning banner, not an unhandled exception.
