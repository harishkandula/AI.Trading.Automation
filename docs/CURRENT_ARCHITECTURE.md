# Current Architecture

Last updated: 2026-10-28

## Overview

Lambda-first with ECS Fargate for the Sunday full backtest. All scan handlers read pre-built S3 Parquet data — no market API calls at scan time. ECS is used for the backtest because it has no timeout limit (Lambda caps at 15 minutes; a 513-symbol × 8-strategy backtest needs only ~2 minutes on Fargate, but headroom matters).

## Component Map

```
EventBridge
  ├── 06:00 IST daily      → token_refresh Lambda         (Groww TOTP refresh)
  ├── 04:00 IST Mon-Fri    → data_pipeline Lambda          (bhavcopy → S3 Parquet)
  ├── 06:58 IST Mon-Fri    → morning Lambda (pre-warm)
  ├── 07:00 IST Mon-Fri    → morning Lambda                (scan → score → Telegram)
  ├── 18:58 IST Mon-Fri    → evening Lambda (pre-warm)
  ├── 19:00 IST Mon-Fri    → evening Lambda                (scan + holdings → Telegram)
  ├── 10:00 IST Sunday     → weekly Lambda                 (compounder research → Telegram)
  ├── 11:00 IST Sunday     → ECS Fargate backtest task     (full universe → DynamoDB + S3)
  └── 12:30 IST Sunday     → leaderboard Lambda            (rank strategies → DynamoDB)
```

## Data Flow

```
NSE (nsearchives.nseindia.com)
        │
        ▼
data_pipeline Lambda (04:00 IST)
  ├── fetch bhavcopy CSV (CDN → archive fallback)
  ├── filter EQ series, validate ≥ 100 rows
  ├── detect corporate actions (price ratio 0.75–1.35)
  │     ├── if split detected: re-fetch via yfinance auto_adjust
  │     └── write {SYMBOL}.adjusted_flag to S3
  └── append rows to S3 Parquet per symbol
        │
        ▼
S3: ohlcv/daily/{SYMBOL}.parquet
        │
        ▼
morning / evening Lambda (HybridDataProvider fill_gaps=False)
  ├── read last 400 calendar days from S3 per symbol
  ├── generate_signals() × 8 strategies
  ├── score_signal() → 0-100 score per signal
  ├── RiskGuard.check() → approve / reject
  └── Notifier → Telegram
```

## Infrastructure

| Resource | Config |
|---|---|
| AWS Region | ap-south-1 (Mumbai) |
| Lambda runtime | Python 3.12, container image, 1 GB memory, 120s timeout |
| data_pipeline Lambda | 1 GB memory, 900s timeout |
| backtest Lambda | 1 GB memory, 900s timeout (ad-hoc only; scheduled via ECS) |
| leaderboard Lambda | 256 MB memory, 60s timeout |
| ECS Fargate backtest | 4 vCPU, 8 GB memory, no timeout |
| ECR | Single shared image, lifecycle: last 10 images |
| S3 | Versioned, AES-256, public access blocked |
| DynamoDB `state` | PAY_PER_REQUEST, PITR enabled |
| DynamoDB `leaderboard` | PAY_PER_REQUEST, PITR enabled |
| Secrets Manager | `market-ai-dev/groww`, `market-ai-dev/telegram` |
| CloudWatch | Log groups per Lambda, error count alarms |

## Source Code Structure

| Path | Role |
|---|---|
| `src/app/` | Core domain library — no Lambda/ECS dependencies |
| `src/app/data/nifty500_symbols.py` | 513-symbol universe, SYMBOL_RENAMES, sector/segment maps |
| `src/app/data/store.py` | ParquetStore: S3 read/write, append_ohlcv, dedup, zero-vol filter |
| `src/app/data/providers/hybrid_provider.py` | S3-first provider; fills gaps from yfinance when fill_gaps=True |
| `src/app/data/providers/nse_bhavcopy.py` | NSE bhavcopy CDN/archive fetcher, BhavcopBlockedError |
| `src/app/core/time_utils.py` | NSE holiday calendar 2024–2027, is_trading_day, prev_trading_day |
| `src/app/strategies/` | 8 strategy implementations + registry |
| `src/app/backtesting/` | Event-driven backtester, metrics, leaderboard ranking |
| `src/app/research/scoring.py` | score_signal(): regime/RS/sector/setup/confluence → 0-100 |
| `src/app/risk/engine.py` | RiskGuard: sector/segment caps, position limits |
| `src/market_ai/handlers/morning.py` | Morning scan entry point |
| `src/market_ai/handlers/evening.py` | Evening scan entry point |
| `src/market_ai/handlers/data_pipeline.py` | Bhavcopy fetch + split detection + S3 append |
| `src/market_ai/handlers/backtest.py` | Full-universe backtester (Lambda + ECS) |
| `src/market_ai/handlers/leaderboard.py` | Strategy ranking from DynamoDB |
| `infrastructure/main.tf` | All AWS resources |
| `.github/workflows/main.yml` | CI/CD pipeline |

## CI/CD

```
push to main
    │
    ▼
validate job
  ├── pip install -e ".[dev]"
  ├── scripts/validate_environment.py
  ├── ruff check src/ tests/
  ├── pytest -q
  ├── run_backtest --universe mock (smoke test)
  └── terraform fmt -check
    │
    ▼
docker-build job
  ├── docker build
  └── smoke test: import market_ai.handlers.morning
    │
    ▼
deploy job
  ├── ECR push (SHA tag + latest)
  ├── terraform init (S3 backend)
  ├── terraform plan -var ecr_image_tag=$SHA
  └── terraform apply

AWS auth: static key secrets (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)
No assume_role, no OIDC, no saml profile.
```
