# Market AI — Indian Equity Trading Automation

Automated research platform for Indian equities. Scans the NIFTY 500 universe (513 symbols) against eight technical strategies, generates research signals with risk scoring, and delivers results to Telegram. **No live orders are placed.** The order-placement gate (`ENABLE_LIVE_ORDERS`) is hardcoded `"false"` in Terraform.

**Stack:** Python 3.12, AWS Lambda + ECS Fargate (container image), Terraform >= 1.6, GitHub Actions.

---

## Table of Contents

1. [What This System Does](#1-what-this-system-does)
2. [Architecture](#2-architecture)
3. [Schedule](#3-schedule)
4. [Repository Structure](#4-repository-structure)
5. [Data Pipeline](#5-data-pipeline)
6. [Strategies](#6-strategies)
7. [Signal Scoring and Risk Guard](#7-signal-scoring-and-risk-guard)
8. [Backtesting and Leaderboard](#8-backtesting-and-leaderboard)
9. [Prerequisites](#9-prerequisites)
10. [First-Time Setup](#10-first-time-setup)
11. [Daily Operations](#11-daily-operations)
12. [ECS Backtest (Manual)](#12-ecs-backtest-manual)
13. [Local Development](#13-local-development)
14. [CI/CD Pipeline](#14-cicd-pipeline)
15. [Groww Authentication](#15-groww-authentication)
16. [Adding a New Strategy](#16-adding-a-new-strategy)
17. [Symbol Universe](#17-symbol-universe)
18. [Key Constraints and Gotchas](#18-key-constraints-and-gotchas)
19. [Known Gaps (Intentional)](#19-known-gaps-intentional)

---

## 1. What This System Does

**Every trading day (automated):**
- 04:00 IST — Data pipeline fetches NSE bhavcopy, appends to S3 Parquet, detects corporate actions (splits, bonuses), adjusts prices, updates indicators.
- 07:00 IST — Morning scan: all 513 NIFTY 500 symbols × 8 strategies. Signals are scored (0–100) and filtered through a portfolio risk guard. Top signals sent to Telegram.
- 19:00 IST — Evening scan: same as morning + reads Groww holdings for open position count.

**Every Sunday (automated):**
- 10:00 IST — Weekly compounder research scan. Scans for long-term compounders using EMA200 trend filter. Results sent to Telegram.
- 11:00 IST — Full ECS Fargate backtest across all 8 strategies × all 513 symbols × 2019–present. Results written to DynamoDB and S3.
- 12:30 IST — Leaderboard Lambda reads backtest results, ranks strategies by risk-adjusted returns, writes top strategy to DynamoDB.

**What it does not do:**
- Place any orders — `ENABLE_LIVE_ORDERS` is always `"false"`.
- Call broker or market APIs at scan time — all OHLCV data comes from S3 Parquet (filled by the data pipeline beforehand).

---

## 2. Architecture

```
GitHub (push to main)
        │
        ▼
  GitHub Actions
  ┌─────────────────────────────────────┐
  │  1. validate (lint / test / tf fmt) │
  │  2. docker-build (smoke test)       │
  │  3. deploy                          │
  │     a. push image to ECR            │
  │     b. terraform apply              │
  └─────────────────────────────────────┘
        │
        ▼
  Amazon ECR  ──────────────────────────────────────┐
  (market-ai-dev, ap-south-1)                       │
        │                                           │
        ▼                                           ▼
  AWS Lambda (container, 1 GB)          ECS Fargate (4 vCPU / 8 GB)
  ┌────────────┬──────────────────┐     ┌──────────────────────────┐
  │ morning    │ 07:00 IST M-F    │     │ backtest (Sunday 11:00)  │
  │ evening    │ 19:00 IST M-F    │     │ 554 symbols × 8 strats   │
  │ weekly     │ 10:00 IST Sun    │     │ 2019-present, no timeout │
  │ token_refresh│ 06:00 IST daily│     └──────────────────────────┘
  │ data_pipeline│ 04:00 IST M-F  │
  │ leaderboard│ 12:30 IST Sun    │
  └────────────┴──────────────────┘
        │                    │
        ▼                    ▼
  Telegram Bot          S3 (Parquet)
  (signals, summary,    ohlcv/daily/{SYMBOL}.parquet
   errors, weekly)      ohlcv/daily/{SYMBOL}.adjusted_flag
                        backtest/{run_id}/results.json
        │
        ▼
  DynamoDB
  ├── market-ai-dev-state      (backtest state, open trades)
  └── market-ai-dev-leaderboard (weekly strategy ranking)
```

**AWS services:**

| Service | Role |
|---|---|
| EventBridge | 9 scheduled rules (see Schedule section) |
| S3 | Data bucket (versioned, AES-256, public access blocked). Parquet OHLCV + backtest results. |
| DynamoDB | `state` table (backtest/trade state) + `leaderboard` table (weekly ranking) |
| Secrets Manager | `market-ai-dev/groww` (API key + TOTP + access token), `market-ai-dev/telegram` |
| CloudWatch | Log groups per function, error metric alarms |
| ECR | Container registry (lifecycle: keep last 10 images) |
| ECS Fargate | Weekly full-universe backtest (no Lambda 15-min timeout limitation) |

---

## 3. Schedule

| Lambda / Task | EventBridge cron (UTC) | IST equivalent | Days |
|---|---|---|---|
| Pre-warm morning | `cron(28 1 ? * MON-FRI *)` | 06:58 | Mon–Fri |
| `token_refresh` | `cron(30 0 ? * * *)` | 06:00 | Daily |
| `morning` | `cron(30 1 ? * MON-FRI *)` | 07:00 | Mon–Fri |
| `data_pipeline` | `cron(30 22 ? * SUN-THU *)` | 04:00 | Mon–Fri |
| Pre-warm evening | `cron(28 13 ? * MON-FRI *)` | 18:58 | Mon–Fri |
| `evening` | `cron(30 13 ? * MON-FRI *)` | 19:00 | Mon–Fri |
| `weekly` | `cron(30 4 ? * SUN *)` | 10:00 | Sunday |
| ECS `backtest` | `cron(30 5 ? * SUN *)` | 11:00 | Sunday |
| `leaderboard` | `cron(0 7 ? * SUN *)` | 12:30 | Sunday |

IST = UTC+5:30. All EventBridge rules use UTC.

**Pre-warm pings:** Two EventBridge rules fire at 06:58 and 18:58 IST with `{"_prewarm": true}`. Each handler detects this payload and returns immediately, keeping the Lambda container warm and eliminating the cold-start penalty on the actual scan invocations 2 minutes later.

**Sunday order dependency:** ECS backtest (11:00) must complete before the leaderboard Lambda (12:30) reads DynamoDB. The 90-minute gap is conservative; the backtest typically finishes in under 5 minutes.

---

## 4. Repository Structure

```
AI.Trading.Automation/
├── .github/
│   └── workflows/
│       └── main.yml              # CI/CD: validate → docker-build → deploy
├── infrastructure/
│   └── main.tf                   # All AWS resources (IaC)
├── scripts/
│   ├── run_backtest.py           # Local/manual backtest CLI
│   ├── run_screener.py           # One-off screener CLI
│   └── validate_environment.py  # Pre-flight check (run in CI validate step)
├── src/
│   ├── app/                      # Core domain library (strategy-agnostic)
│   │   ├── backtesting/          #   Engine, trade state, metrics, leaderboard
│   │   ├── brokers/              #   GrowwBroker (holdings read; orders blocked)
│   │   ├── core/                 #   time_utils (NSE holidays 2024-2027), exceptions, identifiers
│   │   ├── data/
│   │   │   ├── nifty500_symbols.py  # 513-symbol universe + SYMBOL_RENAMES dict
│   │   │   ├── providers/
│   │   │   │   ├── hybrid_provider.py   # S3-first; fills gaps from yfinance (fill_gaps flag)
│   │   │   │   └── nse_bhavcopy.py      # NSE official EOD CSV fetcher + BhavcopBlockedError
│   │   │   ├── store.py          #   ParquetStore: S3 read/write, append_ohlcv, list_symbols
│   │   │   └── universe.py       #   get_universe("nifty500") → list[str]
│   │   ├── indicators/           #   RSI, EMA, ATR, Supertrend, VWAP, volume SMA
│   │   ├── models/               #   Candle, Signal, Timeframe (Pydantic)
│   │   ├── notifications/        #   Notifier, Telegram send, error alerts
│   │   ├── research/             #   scoring.py (signal scorer), regime.py (MarketRegime enum)
│   │   ├── risk/                 #   engine.py (RiskGuard, RiskConfig, PortfolioSnapshot)
│   │   └── strategies/           #   8 strategy implementations + registry
│   └── market_ai/                # Lambda/ECS entry points
│       └── handlers/
│           ├── morning.py        #   07:00 IST — scan + score + risk guard → Telegram
│           ├── evening.py        #   19:00 IST — scan + Groww holdings → Telegram
│           ├── weekly.py         #   Sunday 10:00 IST — compounder research → Telegram
│           ├── token_refresh.py  #   Daily 06:00 IST — Groww TOTP token refresh
│           ├── data_pipeline.py  #   Daily 04:00 IST — bhavcopy fetch, S3 append, split detect
│           ├── backtest.py       #   ECS + Lambda: full-universe backtest → DynamoDB + S3
│           └── leaderboard.py    #   Sunday 12:30 IST — rank strategies, write top to DynamoDB
├── tests/
│   ├── unit/                     # No AWS credentials needed
│   └── integration/              # Require AWS or live credentials
├── docs/                         # Additional documentation
├── Dockerfile                    # Shared image for Lambda and ECS
└── pyproject.toml
```

### src/app/ vs src/market_ai/ split

`src/app/` is the core domain library. It has no dependency on Lambda internals and is used from tests, scripts, and ECS tasks.

`src/market_ai/` contains entry points (Lambda handlers). Each is a thin orchestration layer that wires `app/` components and pushes results to Telegram. Lambda's `image_config.command` overrides point to the correct handler per function.

---

## 5. Data Pipeline

The data pipeline (`market_ai.handlers.data_pipeline`) runs at 04:00 IST every trading day, before the morning scan needs data. The scan handlers use `HybridDataProvider(fill_gaps=False)`, which reads S3 only — they make zero network calls and can't time out waiting for NSE or yfinance.

### Daily flow

```
04:00 IST — data_pipeline Lambda
  Phase 1: Fetch bhavcopy
    → GET nsearchives.nseindia.com CDN (recent format first)
    → Fall back to archives.nseindia.com zip (historical)
    → Parse, filter EQ series only, validate ≥ 100 rows
    → For each symbol in NIFTY 500:
        if adjusted_flag set → skip (yfinance-adjusted series, don't overwrite)
        else → store.append_ohlcv(symbol, today_row)

  Phase 2: Backfill gaps (yfinance)
    → Any symbol missing data from the last 5 trading days
    → yfinance with auto_adjust=True
    → Dedup + zero-volume filter before storing

  Phase 3: Corporate action detection
    → For each symbol: compute today_close / prev_close ratio
    → If ratio < 0.75 or ratio > 1.35 → likely split/bonus
    → Re-fetch full history via yfinance (auto_adjust=True)
    → Set adjusted_flag on S3 to prevent future bhavcopy overwrites
    → Send Telegram alert
```

### S3 data layout

```
s3://{DATA_BUCKET}/
  ohlcv/daily/{SYMBOL}.parquet      ← grows by 1 row per trading day
  ohlcv/daily/{SYMBOL}.adjusted_flag ← presence = series adjusted, skip bhavcopy
  backtest/{run_id}/results.json
  backtest/{run_id}/checkpoint.json  ← resume support for long runs
```

### Error handling

| Condition | Behaviour |
|---|---|
| NSE returns 403 / 429 | `BhavcopBlockedError` raised; Telegram error alert sent |
| Bhavcopy empty on trading day | Telegram alert: "bhavcopy empty for {date}" |
| Individual symbol backfill fails | Logged as warning; pipeline continues to next symbol |
| Checkpoint only saved if rows > 0 | Prevents empty checkpoint overwriting a good previous run |

---

## 6. Strategies

All strategies are registered via `@register` in `src/app/strategies/registry.py`. The `all_strategies()` call returns every registered strategy. Handlers import strategies as side effects; `@register` fires on import.

### Strategy List

| Strategy ID | Timeframe | Warmup | Description |
|---|---|---|---|
| `s00_benchmark` | D1 | 50 bars | EMA50/EMA200 trend filter + RSI momentum zone + volume expansion. Baseline benchmark. |
| `s01_rs_momentum` | D1 | 60 bars | Relative Strength Momentum. Close > EMA50 > EMA200 (rising), RSI 50–75, volume > 1.2×, 60-day positive return. Primary alpha strategy. |
| `s02_vcp` | D1 | 80 bars | Volatility Contraction Pattern. Price coiling near 52-week high with shrinking volatility and volume. Breakout on expansion. |
| `s03_trend_pullback` | D1 | 60 bars | Trend Pullback. Uptrend confirmed (EMA200 rising), RSI dips to 35–50 zone, recovers. Buy the dip in a healthy trend. |
| `gap_up_volume` | D1 | 15 bars | Opening gap > 3% above prior close, volume > 3× 10-day average, close above open. |
| `morning_star` | D1 | 30 bars | Three-candle morning-star reversal (large bearish → small body → bullish close erasing first body). |
| `rsi_supertrend_volume` | D1 | 30 bars | RSI(14) 60–80, Supertrend positive, volume above prior day. |
| `intraday_support_supertrend` | M5 | 60 bars | **Intraday only.** Price tests support, reclaims above on next bar, Supertrend positive, close > VWAP. Skipped in daily scans. |

The `intraday_support_supertrend` strategy is automatically skipped in morning and evening handlers (which request D1 candles). It is included in the registry for intraday-specific scan contexts.

### How Signals Work

Each `Signal` object contains:

| Field | Meaning |
|---|---|
| `entry_reference_price` | Closing price of the triggering bar |
| `stop_price` | 1.5× ATR(20) below entry (daily strategies) or 2% below entry (intraday) |
| `planned_entry_type` | `NEXT_SESSION_OPEN` for daily; `NEXT_BAR_OPEN` for intraday |
| `planned_entry_timestamp` | Next trading day 09:15 UTC |
| `holding_period_sessions` | 20 sessions (daily); square-off at 15:15 for intraday |
| `status` | Always `ELIGIBLE` — no orders placed |
| `qualification_reasons` | Human-readable list of conditions that passed |
| `indicators["score"]` | 0–100 confidence score (set by morning handler) |

---

## 7. Signal Scoring and Risk Guard

The morning handler adds two layers after raw signals are generated:

### Scoring (`app/research/scoring.py`)

Each signal is passed to `score_signal(...)` which produces a composite score:

| Dimension | Weight | Notes |
|---|---|---|
| `regime_score` | 20% | Market regime (BULL/BEAR/CHOPPY/UNKNOWN). Currently `UNKNOWN` = neutral. |
| `rs_score` | 20% | RS percentile vs NIFTY 500. Currently `None` = neutral. |
| `sector_score` | 15% | Sector rank. Currently `None` = neutral. |
| `setup_score` | 35% | Based on strategy family (MOMENTUM / BREAKOUT / PULLBACK) and relative volume. |
| `confluence_bonus` | 10% | Added when ≥2 strategy families fire on same symbol. |

The score and its breakdown are attached to `sig.indicators["score"]` and `sig.indicators["score_breakdown"]`. Signals are sorted by score descending before being passed to the risk guard.

### Risk Guard (`app/risk/engine.py`)

`RiskGuard` enforces portfolio-level rules per signal:

| Rule | Default |
|---|---|
| Max open positions | configurable via `RiskConfig` |
| Max capital in one sector | configurable |
| Max capital in one segment (LARGE/MID/SMALL) | configurable |

Rejected signals are logged with reason; only approved signals reach Telegram.

---

## 8. Backtesting and Leaderboard

### ECS Backtest (Sunday 11:00 IST)

The full-universe backtest runs on ECS Fargate (4 vCPU / 8 GB), not Lambda. This eliminates the Lambda 15-minute hard timeout. A 554-symbol run across 8 strategies completes in ~2 minutes.

ECS task command: `scripts/run_backtest.py --strategy all --universe nifty500 --start 2019-01-01`

Results are written to:
- `DynamoDB market-ai-dev-state` — per-symbol per-strategy trade state
- `S3 backtest/{run_id}/results.json` — full results JSON

### Backtest metrics per strategy

| Metric | Description |
|---|---|
| `avg_net_return_pct` | Average trade return after costs |
| `avg_sharpe` | Sharpe ratio |
| `win_rate` | % of closed trades that are profitable |
| `profit_factor` | gross_profit / gross_loss |
| `max_drawdown_pct` | Peak-to-trough drawdown |
| `total_trades` | Closed trades only (open trades excluded from metrics) |

**Important:** Backtests use the Oct 2026 NIFTY 500 constituent list. Companies removed from the index before Oct 2026 are absent (survivorship bias). A warning is logged once per backtest run.

Warmup buffer: 300 calendar days (needed for EMA200 with sufficient history).

### Leaderboard (Sunday 12:30 IST)

`market_ai.handlers.leaderboard` reads DynamoDB, ranks strategies using:
1. Profitable strategies first (positive `avg_net_return_pct`)
2. Within each group, sort by `avg_sharpe` descending

Top strategy and full ranking written back to `market-ai-dev-leaderboard` DynamoDB table.

---

## 9. Prerequisites

| Requirement | Notes |
|---|---|
| AWS account with programmatic access | IAM user with Lambda, ECR, S3, DynamoDB, Secrets Manager, ECS, EventBridge, CloudWatch, IAM permissions |
| Groww Trade API account | `groww.in/trade-api/api-keys` — free tier |
| Telegram bot + chat ID | Create via BotFather (`/newbot`); get chat ID via `@userinfobot` |
| Python 3.12 | For local development and CI |
| Terraform >= 1.6 | Required for S3 backend syntax |
| Docker | Required to build the Lambda/ECS container image locally |

---

## 10. First-Time Setup

### 10.1 AWS Bootstrap

These resources must exist before Terraform can run (they are the remote backend).

```bash
# Terraform state bucket
aws s3 mb s3://market-ai-tf-state-ap-south-1 --region ap-south-1
aws s3api put-bucket-versioning \
  --bucket market-ai-tf-state-ap-south-1 \
  --versioning-configuration Status=Enabled

# DynamoDB lock table
aws dynamodb create-table \
  --table-name market-ai-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1

# ECR repository
aws ecr create-repository \
  --repository-name market-ai-dev \
  --region ap-south-1
```

### 10.2 GitHub Secrets

**Settings → Secrets and variables → Actions:**

| Secret | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM secret access key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (numeric; `-1001234567890` for a channel) |

### 10.3 AWS Secrets Manager

Two secrets must be populated manually. Terraform creates the containers but does not set values. **Use the AWS Console** to avoid JSON escaping issues.

**`market-ai-dev/groww`:**

| Key | Description |
|---|---|
| `api_key` | TOTP token JWT from Groww dashboard ("Generate TOTP token") |
| `totp_secret` | Base32 TOTP secret from the same page (scan QR or copy secret) |
| `access_token` | Leave empty initially; `token_refresh` populates it |

**`market-ai-dev/telegram`:**

| Key | Description |
|---|---|
| `bot_token` | Same as `TELEGRAM_BOT_TOKEN` GitHub secret |
| `chat_id` | Same as `TELEGRAM_CHAT_ID` GitHub secret |

### 10.4 Deploy

```bash
git push origin main
```

Pipeline: validate → docker-build → deploy (ECR push, then Terraform apply). Monitor in the GitHub Actions tab.

**After first deploy — seed the Groww access token:**

```bash
aws lambda invoke \
  --function-name market-ai-dev-token_refresh \
  --region ap-south-1 \
  /tmp/token_refresh_output.json && cat /tmp/token_refresh_output.json
# Expected: {"statusCode": 200, "body": "Token refreshed"}
```

**Seed historical data (first-time only):**

The data pipeline runs daily from 2019-01-01, but the first run needs to backfill 7 years of history. Trigger the extended backfill Lambda (900s timeout):

```bash
aws lambda invoke \
  --function-name market-ai-dev-data-pipeline-backfill \
  --region ap-south-1 \
  /tmp/backfill_output.json
```

This runs once and populates S3 with the full Parquet history. Daily runs after that are incremental (1-day appends).

---

## 11. Daily Operations

### Manual Lambda Invocations

```bash
# Morning scan (07:00 IST equivalent)
aws lambda invoke \
  --function-name market-ai-dev-morning \
  --region ap-south-1 \
  /tmp/morning_output.json && cat /tmp/morning_output.json

# Evening scan (19:00 IST equivalent)
aws lambda invoke \
  --function-name market-ai-dev-evening \
  --region ap-south-1 \
  /tmp/evening_output.json && cat /tmp/evening_output.json

# Data pipeline (run manually to force refresh)
aws lambda invoke \
  --function-name market-ai-dev-data_pipeline \
  --region ap-south-1 \
  /tmp/pipeline_output.json && cat /tmp/pipeline_output.json

# Weekly compounder research
aws lambda invoke \
  --function-name market-ai-dev-weekly \
  --region ap-south-1 \
  /tmp/weekly_output.json && cat /tmp/weekly_output.json

# Leaderboard (reads DynamoDB, re-ranks)
aws lambda invoke \
  --function-name market-ai-dev-leaderboard \
  --region ap-south-1 \
  /tmp/leaderboard_output.json && cat /tmp/leaderboard_output.json
```

**Response body fields:**

| Lambda | Fields |
|---|---|
| morning / evening | `scan_date`, `universe_size`, `scanned`, `signals`, `errors` |
| evening (extra) | `open_positions` (from Groww holdings) |
| data_pipeline | `total_rows`, `total_days`, `symbols_updated`, `splits_detected` |
| leaderboard | `top_strategy`, `ranked` (array) |

### CloudWatch Logs

```bash
# Tail morning Lambda in real time
aws logs tail /aws/lambda/market-ai-dev-morning \
  --region ap-south-1 \
  --follow

# Last 30 minutes of evening logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/market-ai-dev-evening \
  --region ap-south-1 \
  --start-time $(date -d '30 minutes ago' +%s000)

# ECS backtest logs
aws logs tail /ecs/market-ai-dev-screener \
  --region ap-south-1 \
  --follow
```

Log groups: `/aws/lambda/market-ai-dev-{name}` for Lambdas, `/ecs/market-ai-dev-screener` for ECS.

CloudWatch metric alarms fire when any Lambda error count > 0 in a 5-minute window: `market-ai-dev-{name}-errors`.

### Checking the Leaderboard (DynamoDB)

```bash
aws dynamodb scan \
  --table-name market-ai-dev-leaderboard \
  --region ap-south-1 \
  --query "Items[*].[strategy_id.S, avg_net_return_pct.N, avg_sharpe.N]" \
  --output table
```

---

## 12. ECS Backtest (Manual)

To run the full backtest outside the Sunday schedule:

```bash
# Get subnet and security group IDs from Terraform outputs
SUBNET=$(terraform -chdir=infrastructure output -raw subnet_id)
SG=$(terraform -chdir=infrastructure output -raw ecs_security_group_id)

aws ecs run-task \
  --cluster market-ai-dev \
  --task-definition market-ai-dev-screener \
  --launch-type FARGATE \
  --network-configuration "awsvpcConfiguration={subnets=[$SUBNET],securityGroups=[$SG],assignPublicIp=ENABLED}" \
  --region ap-south-1
```

Or use the Lambda backtest function for ad-hoc runs (900s timeout, single-function convenience):

```bash
aws lambda invoke \
  --function-name market-ai-dev-backtest \
  --region ap-south-1 \
  /tmp/backtest_output.json
```

---

## 13. Local Development

### Install

```bash
pip install -e ".[dev]"
```

Installs in editable mode with: pytest, pytest-cov, ruff, mypy, freezegun, factory-boy.

### Run Tests

```bash
pytest
```

No AWS credentials needed — all AWS calls are mocked. Set these env vars:

```bash
export TRADING_MODE=RESEARCH_ONLY
export ENABLE_LIVE_ORDERS=false
export BROKER_PROVIDER=mock
export DATA_BUCKET=test-bucket
export STATE_TABLE=test-state
```

### Linting

```bash
ruff check src/ tests/
```

Rules: `E`, `F`, `W`, `B`, `UP`. Line length 100.

### Running Backtests Locally

```bash
# Mock universe (no network calls)
python -m scripts.run_backtest \
  --strategy all \
  --universe mock \
  --start 2023-01-01 \
  --end 2024-06-30

# Real symbols (requires internet + S3 data or yfinance fallback)
python -m scripts.run_backtest \
  --strategy s02_vcp \
  --universe nifty500 \
  --start 2022-01-01 \
  --end 2024-12-31 \
  --output reports/vcp_run.json
```

### Checking Data Freshness

```bash
# How many symbols have data, and what's the latest date?
python - <<'EOF'
import os; os.environ["DATA_BUCKET"] = "your-bucket-name"
from app.data.store import ParquetStore
store = ParquetStore(os.environ["DATA_BUCKET"])
for sym in ["RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK"]:
    print(sym, store.latest_date(sym))
EOF
```

### Environment Variables Reference

| Variable | Lambda value | Purpose |
|---|---|---|
| `TRADING_MODE` | `RESEARCH_ONLY` | Gates paper-trading logic |
| `ENABLE_LIVE_ORDERS` | `false` | Must be `"true"` to allow orders — never set this |
| `BROKER_PROVIDER` | `mock` locally, `groww` in Lambda | Controls broker adapter |
| `APP_ENV` | `dev` | Environment tag in logs and DynamoDB |
| `DATA_BUCKET` | set by Terraform | S3 bucket for OHLCV Parquet |
| `STATE_TABLE` | set by Terraform | DynamoDB state table |
| `LEADERBOARD_TABLE` | set by Terraform | DynamoDB leaderboard table |
| `UNIVERSE` | `nifty500` | Symbol universe for scan handlers |
| `PORTFOLIO_EQUITY` | `500000` | INR portfolio size for position sizing |
| `TELEGRAM_BOT_TOKEN` | set by Terraform | Telegram bot token |
| `TELEGRAM_CHAT_ID` | set by Terraform | Telegram chat ID |

---

## 14. CI/CD Pipeline

File: `.github/workflows/main.yml`. Triggers on push to `main` and PRs targeting `main`. Region: `ap-south-1`, Python 3.12.

### Job 1: validate

1. `pip install -e ".[dev]"`
2. `python -m scripts.validate_environment` with mock env vars
3. `ruff check src/ tests/`
4. `pytest -q`
5. Backtest smoke test: `run_backtest --strategy all --universe mock`
6. `terraform fmt -check infrastructure/`

### Job 2: docker-build (main branch only)

1. `docker build` the image
2. Smoke-test: `python -c "from market_ai.handlers.morning import handler; print('ok')"` inside container

### Job 3: deploy (main branch only, `prod` environment)

1. Configure AWS via `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY`
2. ECR login
3. Build and push image tagged `$GITHUB_SHA` and `latest`
4. `terraform init` (S3 backend)
5. `terraform plan -var="ecr_image_tag=$GITHUB_SHA" ...`
6. `terraform apply -auto-approve`

**Critical:** ECR push (step 3) must complete before `terraform apply` (step 6). Terraform sets each Lambda's `image_uri` to the new SHA tag. If the image doesn't exist yet when Terraform runs, the Lambda update fails.

---

## 15. Groww Authentication

### TOTP Flow

```
market-ai-dev/groww (Secrets Manager)
  api_key       ──► GrowwAuthManager.refresh_token()
  totp_secret   ──► pyotp.TOTP(totp_secret).now()  ──► 6-digit code
                             │
                             ▼
                  GrowwAPI.get_access_token(api_key, totp_code)
                             │
                             ▼
                  access_token  ──► written back to Secrets Manager
                             │
                             ▼
                  GrowwBroker._headers()  ──► Authorization: Bearer <token>
```

### Token Refresh

`market-ai-dev-token_refresh` runs at 06:00 IST daily (00:30 UTC), 30 minutes before the morning scan.

**Rate limit:** Groww allows 150 auth requests per 24 hours. Do not invoke `token_refresh` in loops or more than a few times manually per day.

### Holdings Read

The evening handler calls Groww holdings API (read-only). It is non-fatal — if Groww is unreachable, the scan continues and signals are still sent.

---

## 16. Adding a New Strategy

**Step 1 — Create the file** in `src/app/strategies/`:

```python
from app.strategies.base import StrategyBase
from app.strategies.registry import register
from app.models.candle import Candle, Timeframe

@register
class MyStrategy(StrategyBase):
    strategy_id = "my_strategy"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 30

    @classmethod
    def default_params(cls) -> dict:
        return {"my_param": 1.0}

    def generate_signals(self, symbol: str, candles: list[Candle], as_of_index=None):
        ...
        return []  # list[Signal]
```

**Step 2 — Import it in the handlers** that should run it. Add to `morning.py` and `evening.py`:

```python
import app.strategies.my_strategy  # noqa: F401
```

`@register` fires on import; `all_strategies()` picks it up automatically.

**Step 3 — Write tests** in `tests/unit/strategies/test_my_strategy.py`. Cover: empty candle list, fewer bars than `warmup_bars`, each filter boundary condition.

**Step 4 — Run locally:**

```bash
pytest tests/unit/strategies/test_my_strategy.py -v

python -m scripts.run_backtest \
  --strategy my_strategy \
  --universe mock \
  --start 2023-01-01 \
  --end 2024-06-30
```

**Step 5 — Add to family map** in `morning.py` if you want confluence scoring to include it:

```python
_FAMILY_MAP["my_strategy"] = StrategyFamily.BREAKOUT  # or MOMENTUM / PULLBACK
```

---

## 17. Symbol Universe

**File:** `src/app/data/nifty500_symbols.py`

| Metric | Value |
|---|---|
| Total symbols | 513 |
| Segments | LARGE (100), MID (150), SMALL (263) |
| Source | NIFTY 500 constituent list, Oct 2026 snapshot |

**82 symbols were removed** that are absent from the NSE bhavcopy (renamed or delisted). Key renames are tracked in `SYMBOL_RENAMES` dict:

| Old Symbol | Current Symbol | Reason |
|---|---|---|
| `ZOMATO` | `ETERNAL` | Rebranded |
| `MINDTREE` | `LTIMINDTREE` | Merger |
| `ADANITRANS` | `ADANIENT` | Restructure |
| `TVSMOTORS` | `TVSMOTOR` | NSE symbol change |

**Semi-annual refresh:** NIFTY 500 rebalances every March and September. The symbol list must be manually updated at each rebalance. No automated sync script currently exists.

### Corporate Action Handling

The data pipeline detects splits and bonuses by checking the daily price ratio:

```
ratio = today_close / prev_close
if ratio < 0.75 or ratio > 1.35 → corporate action suspected
```

When triggered:
1. Full history re-fetched via yfinance with `auto_adjust=True`
2. `{symbol}.adjusted_flag` written to S3 — prevents future bhavcopy appends from overwriting the adjusted series
3. Telegram alert sent

---

## 18. Key Constraints and Gotchas

| Constraint | Detail |
|---|---|
| `ENABLE_LIVE_ORDERS` must be `"true"` for real orders | Hardcoded `"false"` in `main.tf` locals. `GrowwBroker.place_order()` raises `ValueError` if not exactly `"true"`. No orders under any current configuration. |
| `fill_gaps=False` in scan handlers | Morning and evening handlers use `HybridDataProvider(fill_gaps=False)`. This means they read S3 only — zero broker/market API calls at scan time. The data pipeline must have run successfully first. If data is stale, a freshness warning is logged and sent to Telegram. |
| ECR push before Terraform apply | The deploy job pushes the image first. Reversing this order causes a Lambda update failure. |
| Terraform state backend | S3: `market-ai-tf-state-ap-south-1`, key: `market-ai/dev/terraform.tfstate`, lock table: `market-ai-tf-locks`. Both must exist before `terraform init`. |
| AWS auth — static keys only | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` used directly. No `assume_role` block in S3 backend or AWS provider. Do not add one. |
| Terraform >= 1.6 required | TF 1.6 changed S3 backend `assume_role` syntax. `required_version = ">= 1.6"` enforces this. |
| Lambda deployed as container image | `package_type = "Image"` avoids the 250 MB unzipped ZIP limit. All four scan Lambdas share a single ECR image; `image_config.command` overrides the CMD per function. |
| Intraday strategy skipped in daily scan | `intraday_support_supertrend` requests M5 candles. Morning and evening handlers skip any strategy where `strategy.timeframe == Timeframe.M5`. |
| Warmup window is 400 calendar days | Scan handlers fetch 400 days of daily history per symbol. EMA200 needs ~280 trading days; the 400-day calendar buffer ensures enough bars after weekends and holidays. |
| NSE holiday calendar | `app/core/time_utils.py` has NSE holidays for 2024, 2025, 2026, 2027. A module-level warning fires if the current year is not in the set. Add the next year's holidays each December. |
| Groww TOTP rate limit | 150 auth requests per 24 hours. Do not invoke `token_refresh` in loops. |
| ECR lifecycle | Only the last 10 images are retained. Do not rely on image tags beyond the most recent 10 pushes. |
| Survivorship bias in backtest | The backtest uses the Oct 2026 NIFTY 500 list. Companies delisted between 2019 and 2026 are absent. A warning is logged once per run. Fixing this requires historical index membership data (significant architecture work). |
| Open trades excluded from backtest metrics | Only closed trades are included in win rate, Sharpe, and return calculations. Open trades at end-of-backtest are counted separately. |

---

## 19. Known Gaps (Intentional)

These are tracked but not bugs:

| Gap | Notes |
|---|---|
| Market regime computation | `regime=MarketRegime.UNKNOWN` in morning.py. Regime scoring dimension is neutral (no boost/penalty). Fix: detect NIFTY trend state from S3 data and pass to scorer. |
| RS percentile and sector rank | `rs_percentile=None, sector_rank=None` in morning.py. These scoring dimensions are neutral. Fix: cross-symbol RS computation across the full NIFTY 500 each morning. |
| Weekly fundamentals | `weekly.py` uses a price-based proxy for fundamental scores (EMA200 trend as quality filter). Real P/E, EPS, ROCE from Tijori Finance or Tickertape would improve accuracy. |
| NIFTY 500 rebalance sync | `nifty500_symbols.py` is a static snapshot (Oct 2026). It should be refreshed each March and September when NSE publishes the rebalanced constituent list. |
| Point-in-time universe | Backtest uses the current constituent list, not the historical one. Survivorship bias exists. |

---

## Disclaimer

For personal research and education only. This software does not provide guaranteed returns or personalised investment advice. Market investments involve risk, including possible loss of capital. Verify all information with original sources before acting on any signal.
