# Market AI — Indian Equity Research Platform

Automated research platform for Indian equities. Scans the NIFTY 50 universe against four technical strategies, generates research signals, and delivers results to Telegram. **No live orders are placed.** The platform runs in `RESEARCH_ONLY` mode by design, and the order-placement gate (`ENABLE_LIVE_ORDERS`) is hardcoded to `"false"` in Terraform.

**Stack:** Python 3.12, AWS Lambda (container image), Terraform >= 1.6, GitHub Actions.

---

## Table of Contents

1. [Project Overview](#1-project-overview)
2. [Architecture](#2-architecture)
3. [Repository Structure](#3-repository-structure)
4. [Strategies](#4-strategies)
5. [Prerequisites](#5-prerequisites)
6. [First-Time Setup](#6-first-time-setup)
7. [Daily Operations](#7-daily-operations)
8. [Local Development](#8-local-development)
9. [CI/CD Pipeline](#9-cicd-pipeline)
10. [Groww Authentication](#10-groww-authentication)
11. [Adding a New Strategy](#11-adding-a-new-strategy)
12. [Key Constraints and Gotchas](#12-key-constraints-and-gotchas)

---

## 1. Project Overview

Market AI runs four technical strategies across all 50 NIFTY 50 constituents each trading day. It fetches daily (and intraday) OHLCV data from Yahoo Finance via `yfinance`, evaluates strategy conditions over a 100-day lookback window, and publishes any signals generated to a Telegram channel.

**What it does:**
- Screens NIFTY 50 (50 symbols) on every trading day via scheduled Lambda functions.
- Runs four strategies: momentum gap, morning-star candlestick, RSI+Supertrend+volume, and intraday support reclaim.
- Fetches live Groww holdings in the evening scan (read-only — no orders placed).
- Runs a weekly compounder research scan using synthetic fundamental data.
- Sends all results to Telegram (HTML-formatted messages, chunked to 10 signals per message).

**What it does not do:**
- Place any orders — `ENABLE_LIVE_ORDERS` is always `"false"`.
- Fetch real-time intraday data from Groww — all OHLCV data comes from Yahoo Finance.
- Check for NSE exchange holidays — the market-status helper uses wall-clock time only.

---

## 2. Architecture

```
GitHub (push to main)
        |
        v
  GitHub Actions
  +-------------------------------------+
  |  1. validate (lint/test/tf fmt)     |
  |  2. docker-build (smoke test)       |
  |  3. deploy                          |
  |     a. push image to ECR            |
  |     b. terraform apply              |
  +-------------------------------------+
        |
        v
  Amazon ECR
  (market-ai-dev, ap-south-1)
        |
        v
  AWS Lambda (container image, 512 MB, 120 s timeout)
  +------------------+-------------------+------------+------------------+
  | market-ai-dev-   | market-ai-dev-    | market-    | market-ai-dev-   |
  | morning          | evening           | ai-dev-    | token_refresh    |
  | 07:00 IST M-F    | 19:00 IST M-F     | weekly     | 06:00 IST daily  |
  |                  | + Groww holdings  | 10:00 IST  |                  |
  +------------------+-------------------+--Sun-------+------------------+
        |                                                      |
        v                                                      v
  Telegram Bot                                     AWS Secrets Manager
  (signal alerts, daily summary,                   (access_token written back)
   error alerts, weekly research)
```

**Supporting AWS services:**

| Service | Role |
|---|---|
| EventBridge | Schedules the four Lambda functions |
| S3 | Data bucket (versioned, AES-256, public access blocked) |
| DynamoDB | Application state table (PAY_PER_REQUEST, PITR enabled) |
| Secrets Manager | `market-ai-dev/groww`, `market-ai-dev/telegram` |
| CloudWatch | Log groups per Lambda (configurable retention), error metric alarms |
| ECR | Container image registry (lifecycle: keep last 10 images) |

### EventBridge Schedules

| Lambda | Schedule (UTC cron) | Equivalent IST | Days |
|---|---|---|---|
| morning | `cron(30 1 ? * MON-FRI *)` | 07:00 | Mon–Fri |
| evening | `cron(30 13 ? * MON-FRI *)` | 19:00 | Mon–Fri |
| weekly | `cron(30 4 ? * SUN *)` | 10:00 | Sunday |
| token_refresh | `cron(30 0 ? * * *)` | 06:00 | Daily |

IST = UTC+5:30. All EventBridge rules use UTC.

---

## 3. Repository Structure

```
AI.Trading.Automation/
├── .github/
│   └── workflows/
│       └── main.yml              # CI/CD pipeline (validate -> docker-build -> deploy)
├── infrastructure/
│   └── main.tf                   # All AWS resources (Lambda, ECR, S3, DynamoDB, etc.)
├── scripts/
│   ├── build_lambda.sh           # Legacy ZIP packager (not used in CI; Lambda runs as image)
│   ├── run_backtest.py           # Backtest runner: --strategy, --universe, --start, --end
│   ├── run_screener.py           # One-off screener CLI
│   └── validate_environment.py  # Pre-flight check run at CI validate step
├── src/
│   ├── app/                      # Core domain library (strategy-agnostic, reusable)
│   │   ├── auth/                 #   GrowwAuthManager - TOTP token lifecycle
│   │   ├── backtesting/          #   Backtesting engine and trade simulator
│   │   ├── brokers/              #   GrowwBroker - holdings, positions, order stubs
│   │   ├── core/                 #   Shared utilities: time_utils, exceptions, identifiers
│   │   ├── data/
│   │   │   ├── providers/        #   MarketDataProvider base + YFinanceProvider
│   │   │   ├── ingestion/        #   Ingestion pipelines
│   │   │   ├── quality/          #   Data quality checks
│   │   │   ├── repositories/     #   Candle/signal persistence
│   │   │   └── universe.py       #   NIFTY50, NIFTY_NEXT50, mock symbol lists
│   │   ├── indicators/           #   RSI, Supertrend, VWAP, volume SMA, candlestick patterns
│   │   ├── models/               #   Pydantic models: Candle, Signal, Instrument, etc.
│   │   ├── notifications/        #   Notifier, telegram.send(), Jinja2 templates
│   │   ├── paper_trading/        #   Paper trading engine (no live orders)
│   │   ├── screener/             #   Screener orchestration
│   │   └── strategies/           #   Strategy implementations + registry
│   └── market_ai/                # Lambda entry points (thin handlers that call into app/)
│       ├── handlers/
│       │   ├── morning.py        #   07:00 IST scan - all 4 strategies over NIFTY 50
│       │   ├── evening.py        #   19:00 IST scan + Groww holdings read
│       │   ├── weekly.py         #   Compounder research with synthetic fundamentals
│       │   └── token_refresh.py  #   Refreshes Groww access_token via TOTP
│       ├── models.py             #   market_ai-level models (Candidate, etc.)
│       ├── providers/            #   market_ai-level provider wrappers
│       └── services/             #   scoring.py, reporting.py for weekly scan
├── tests/
│   ├── unit/                     # Unit tests per domain area (auth, brokers, data, etc.)
│   │   ├── strategies/           #   One test file per strategy
│   │   ├── indicators/           #   Indicator math tests
│   │   └── ...
│   ├── integration/              # Integration tests (require AWS or live credentials)
│   └── fixtures/                 # Shared pytest fixtures, factory-boy factories
├── docs/                         # Additional documentation
├── reports/                      # CI backtest output (gitignored)
├── Dockerfile                    # Container image built and pushed to ECR
└── pyproject.toml                # Package metadata, dependencies, pytest/ruff config
```

### src/app/ vs src/market_ai/ split

`src/app/` is the core domain library. It contains all business logic: strategies, indicators, data providers, models, broker wrappers, and notifications. It has no dependency on AWS Lambda internals and is usable from tests, scripts, or any future API surface.

`src/market_ai/` contains the Lambda handler functions. Each handler is a thin orchestration layer: it wires together `app/` components, calls strategies, and pushes results to Telegram. The Lambda's `image_config.command` overrides point directly to `market_ai.handlers.<name>.handler`.

---

## 4. Strategies

All strategies are registered via a `@register` decorator in `src/app/strategies/registry.py`. The `all_strategies()` function returns every registered strategy; new strategies are picked up automatically once imported and registered.

### Strategy List

| Strategy ID | Class | Timeframe | Description |
|---|---|---|---|
| `intraday_support_supertrend` | `IntradaySupportSupertrend` | M5 (5-min) | Signals when price tests a rolling support level, reclaims above it on the next bar's close, Supertrend is positive, and close is above session VWAP. Intraday only; square-off at 15:15. |
| `morning_star` | `MorningStar` | D1 | Three-candle morning-star candlestick reversal: large bearish candle, small-body indecision candle, bullish close that erases the first candle's body. Optional Supertrend and volume filters. |
| `rsi_supertrend_volume` | `RSISuperTrendVolume` | D1 | RSI(14) in the 60–80 momentum zone, Supertrend positive, and volume above prior day (configurable variants: 1.25x 10-day SMA or 1.50x 20-day SMA). 5-session holding period. |
| `gap_up_volume` | `GapUpVolume` | D1 | Opening gap of >3% above the prior close, volume at least 3x the 10-day average, and close above open (gap held through the session). 5-session holding period. |

### How Signals Work

Each `Signal` object produced by a strategy contains:

| Field | Meaning |
|---|---|
| `entry_reference_price` | Closing price of the bar that triggered the signal |
| `stop_price` | `entry_reference_price * (1 - stop_pct)` — 3% for daily strategies, 2% for intraday |
| `target_prices` | Explicit only for intraday: T1 at +2% (`entry_ref * 1.02`), with 50% partial exit |
| `planned_entry_type` | `NEXT_SESSION_OPEN` for daily strategies; `NEXT_BAR_OPEN` for intraday |
| `holding_period_sessions` | 5 sessions (daily strategies only; intraday squares off same day at 15:15) |
| `status` | Always `ELIGIBLE` — the platform does not place orders |
| `qualification_reasons` | Human-readable list of conditions that passed |
| `risk_warnings` | Fixed disclaimers attached to every signal |

Daily strategies use a 100-day warmup window (`_WARMUP_DAYS = 100`) to ensure indicators have sufficient history. The intraday strategy requires 60 warmup bars (5-minute bars).

---

## 5. Prerequisites

| Requirement | Notes |
|---|---|
| AWS account with programmatic access | IAM user with permissions for Lambda, ECR, S3, DynamoDB, Secrets Manager, EventBridge, CloudWatch, IAM |
| Groww Trade API account | Sign up at [groww.in/trade-api/api-keys](https://groww.in/trade-api/api-keys) — free tier available |
| Telegram bot + chat ID | Create via BotFather (`/newbot`); get your chat ID by messaging `@userinfobot` |
| Python 3.12 | Required for local development and CI |
| Terraform >= 1.6 | Required for S3 backend syntax compatibility |
| Docker | Required to build the Lambda container image locally |

---

## 6. First-Time Setup

### 6.1 AWS Bootstrap

These resources must be created once before Terraform can run, because Terraform uses them as its remote backend.

**Create the Terraform state S3 bucket:**

```bash
aws s3 mb s3://market-ai-tf-state-ap-south-1 --region ap-south-1

aws s3api put-bucket-versioning \
  --bucket market-ai-tf-state-ap-south-1 \
  --versioning-configuration Status=Enabled
```

**Create the DynamoDB lock table:**

```bash
aws dynamodb create-table \
  --table-name market-ai-tf-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ap-south-1
```

**Create the ECR repository:**

```bash
aws ecr create-repository \
  --repository-name market-ai-dev \
  --region ap-south-1
```

The ECR repository name `market-ai-dev` matches the `ECR_REPOSITORY` value hardcoded in `.github/workflows/main.yml`. Terraform also manages this resource; if you create it manually first, Terraform will import and manage it after apply.

### 6.2 GitHub Secrets

Navigate to **Settings > Secrets and variables > Actions** in your GitHub repository and add the following secrets:

| Secret Name | Value |
|---|---|
| `AWS_ACCESS_KEY_ID` | IAM access key ID |
| `AWS_SECRET_ACCESS_KEY` | IAM secret access key |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token from BotFather (e.g. `123456:ABC-DEF...`) |
| `TELEGRAM_CHAT_ID` | Telegram chat ID (numeric; e.g. `-1001234567890` for a channel) |

These map directly to how the deploy job authenticates: `aws-actions/configure-aws-credentials` uses `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY`; Terraform receives `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` as `-var` arguments and writes them to Lambda environment variables.

### 6.3 AWS Secrets Manager

Two secrets must be populated manually **before the first Lambda invocation**. Terraform creates the secret containers (name and ARN) but does not set the values — those are never stored in Terraform state.

**Recommendation:** Use the AWS Console UI to set the secret values to avoid JSON escaping issues with the CLI.

---

**Secret: `market-ai-dev/groww`**

| Key | Value | Where to find it |
|---|---|---|
| `api_key` | TOTP token JWT | `groww.in/trade-api/api-keys` — click "Generate TOTP token". Copies a JWT string. |
| `totp_secret` | Base32 TOTP secret | Same page — scan the QR code with an authenticator app and note the base32 secret, or use the "copy secret" link if shown. |

The auth flow is: `api_key` (TOTP JWT) + `totp_secret` (base32) → `GrowwAPI.get_access_token()` → `access_token`. The `token_refresh` Lambda writes `access_token` back into this same secret every day automatically.

---

**Secret: `market-ai-dev/telegram`**

| Key | Value |
|---|---|
| `bot_token` | Telegram bot token (same value as `TELEGRAM_BOT_TOKEN` GitHub secret) |
| `chat_id` | Telegram chat ID (same value as `TELEGRAM_CHAT_ID` GitHub secret) |

Note: Telegram credentials reach Lambda via two paths — environment variables (from Terraform vars) and Secrets Manager (via `Notifier.from_secrets()`). The handlers use the Secrets Manager path at runtime.

### 6.4 Deploy

Once GitHub Secrets and Secrets Manager values are set, push to `main` to trigger the full pipeline:

```bash
git push origin main
```

The pipeline runs: validate → docker-build → deploy (ECR push, then Terraform apply). Monitor progress in the GitHub Actions tab.

**After the first deploy — manual token refresh:**

The `access_token` field in `market-ai-dev/groww` starts empty. Invoke `token_refresh` once manually to populate it before the morning scan runs:

```bash
aws lambda invoke \
  --function-name market-ai-dev-token_refresh \
  --region ap-south-1 \
  /tmp/token_refresh_output.json

cat /tmp/token_refresh_output.json
# Expected: {"statusCode": 200, "body": "Token refreshed"}
```

---

## 7. Daily Operations

### Schedule Summary

| Lambda | IST Time | Days | Action |
|---|---|---|---|
| `market-ai-dev-token_refresh` | 06:00 | Daily | Refreshes Groww access_token in Secrets Manager |
| `market-ai-dev-morning` | 07:00 | Mon–Fri | NIFTY 50 scan across all 4 strategies, Telegram alert |
| `market-ai-dev-evening` | 19:00 | Mon–Fri | NIFTY 50 scan + Groww holdings read, Telegram alert |
| `market-ai-dev-weekly` | 10:00 | Sunday | Compounder research (synthetic data), Telegram summary |

### Manually Invoking a Lambda

```bash
# Morning scan
aws lambda invoke \
  --function-name market-ai-dev-morning \
  --region ap-south-1 \
  /tmp/morning_output.json

# Evening scan
aws lambda invoke \
  --function-name market-ai-dev-evening \
  --region ap-south-1 \
  /tmp/evening_output.json

# Weekly compounder research
aws lambda invoke \
  --function-name market-ai-dev-weekly \
  --region ap-south-1 \
  /tmp/weekly_output.json
```

The response body contains: `scan_date`, `universe_size`, `scanned`, `signals`, `errors` (and `open_positions` for the evening scan).

### Reading CloudWatch Logs

```bash
# Tail the morning Lambda log group in real time
aws logs tail /aws/lambda/market-ai-dev-morning \
  --region ap-south-1 \
  --follow

# Fetch the last 30 minutes of evening logs
aws logs filter-log-events \
  --log-group-name /aws/lambda/market-ai-dev-evening \
  --region ap-south-1 \
  --start-time $(date -d '30 minutes ago' +%s000)
```

Log group names follow the pattern `/aws/lambda/market-ai-dev-<name>`. Retention is controlled by the `log_retention_days` Terraform variable.

CloudWatch metric alarms are configured for each Lambda: any error count above 0 in a 5-minute window triggers an alarm named `market-ai-dev-<name>-errors`.

---

## 8. Local Development

### Install

```bash
pip install -e ".[dev]"
```

This installs the package in editable mode with all dev dependencies: pytest, pytest-cov, ruff, mypy, freezegun, factory-boy.

### Run Tests

```bash
pytest
```

106 tests. `pythonpath` is set to `src/` in `pyproject.toml`, so both `app` and `market_ai` packages are importable without additional configuration.

Tests require no AWS credentials — all AWS calls are mocked. Set these environment variables to match CI:

```bash
export TRADING_MODE=RESEARCH_ONLY
export ENABLE_LIVE_ORDERS=false
export BROKER_PROVIDER=mock
```

### Linting

```bash
ruff check src/ tests/
```

Rules: `E`, `F`, `W`, `B`, `UP`. Line length 100. Import sort rule `I001` is disabled.

### Type Checking

```bash
mypy src/
```

`ignore_missing_imports = true`, `strict = false`. Run for guidance; not a CI gate.

### Running Backtests Locally

```bash
python -m scripts.run_backtest \
  --strategy all \
  --universe mock \
  --start 2023-01-01 \
  --end 2024-06-30
```

The `mock` universe uses three synthetic symbols (`DEMOALPHA`, `DEMOBETA`, `DEMOGAMMA`) with generated OHLCV data — no network calls are required. Use `--universe nifty50` to run against real Yahoo Finance data (requires internet access). Use `--output reports/my_run.json` to write results to a file.

### Environment Variables Reference

| Variable | Lambda value | Purpose |
|---|---|---|
| `TRADING_MODE` | `RESEARCH_ONLY` | Gates paper-trading logic |
| `ENABLE_LIVE_ORDERS` | `false` | Must be `"true"` to allow `GrowwBroker.place_order()` — never set to true |
| `BROKER_PROVIDER` | `mock` | Use `mock` locally; `groww` in Lambda when Groww is configured |
| `APP_ENV` | `dev` | Environment tag propagated to logs and DynamoDB records |
| `DATA_BUCKET` | set by Terraform | S3 bucket name for data storage |
| `STATE_TABLE` | set by Terraform | DynamoDB table name for application state |
| `TELEGRAM_BOT_TOKEN` | set by Terraform | Telegram bot token (Lambda env var path) |
| `TELEGRAM_CHAT_ID` | set by Terraform | Telegram chat ID (Lambda env var path) |

---

## 9. CI/CD Pipeline

File: `.github/workflows/main.yml`. Triggers on push to `main` and pull requests targeting `main`. Region: `ap-south-1`. Python: 3.12.

### Job 1: validate (all pushes and PRs)

1. Install dependencies with `pip install -e ".[dev]"`.
2. `python -m scripts.validate_environment` with mock env vars.
3. `ruff check src/ tests/` — fail on any lint error.
4. `pytest -q` — fail on any test failure.
5. Backtest smoke test: `python -m scripts.run_backtest --strategy all --universe mock --start 2023-01-01 --end 2024-06-30 --output reports/ci_backtest.json`.
6. `terraform fmt -check` on the `infrastructure/` directory.

### Job 2: docker-build (main branch only, after validate)

1. `docker build` the image from the repo root.
2. Smoke-test the image by running `python -c "from market_ai.handlers.morning import handler; print('import ok')"` inside the container with mock env vars.

This job confirms the container builds and all handler imports resolve before the deploy job pushes to ECR.

### Job 3: deploy (main branch only, after validate + docker-build, environment: prod)

1. Configure AWS credentials using `AWS_ACCESS_KEY_ID` and `AWS_SECRET_ACCESS_KEY` GitHub secrets.
2. Log in to Amazon ECR.
3. Build the Docker image, tag as both `$GITHUB_SHA` and `latest`, push both tags to the `market-ai-dev` ECR repository.
4. `terraform init` (reads S3 backend).
5. `terraform plan -var="ecr_image_tag=$GITHUB_SHA" -var="telegram_bot_token=..." -var="telegram_chat_id=..." -out=tfplan`.
6. `terraform apply -auto-approve tfplan`.

**Critical ordering constraint:** The ECR push (step 3) happens before Terraform apply (step 6). Terraform sets each Lambda's `image_uri` to the new `$GITHUB_SHA` tag. If Terraform runs before the image is pushed, the Lambda update will fail because the image URI does not yet exist in ECR.

### Lambda Deployment Model

Lambdas are deployed as container images (`package_type = "Image"`). This avoids the 250 MB unzipped size limit that applies to ZIP deployments. All four Lambdas share a single ECR image; the `image_config.command` block in each `aws_lambda_function` resource overrides the container CMD to point to the correct handler module.

`scripts/build_lambda.sh` builds a ZIP package for reference but is not used by the CI/CD pipeline.

---

## 10. Groww Authentication

### TOTP Flow

The platform uses the Groww Trade API's TOTP authentication flow. The three credentials involved:

- `api_key` — a long-lived JWT issued by the Groww dashboard under "Generate TOTP token".
- `totp_secret` — a base32 secret from the TOTP setup QR code on the same page. Used with `pyotp` to generate time-based 6-digit codes.
- `access_token` — a short-lived Bearer token returned by `GrowwAPI.get_access_token(api_key=..., totp=...)`.

```
Secrets Manager: market-ai-dev/groww
  api_key        ->  passed to GrowwAuthManager.refresh_token()
  totp_secret    ->  pyotp.TOTP(totp_secret).now()  ->  6-digit code
                          |
                          v
                 GrowwAPI.get_access_token(api_key, totp_code)
                          |
                          v
                 access_token  ->  written back to Secrets Manager
                          |
                          v
                 GrowwBroker._headers()  ->  Authorization: Bearer <access_token>
```

The TOTP code is generated immediately before the API call to minimise the risk of a 30-second TOTP window expiry mid-request.

### Token Refresh Lambda

`market-ai-dev-token_refresh` runs at 06:00 IST daily (00:30 UTC), 30 minutes before the morning scan. It calls `GrowwAuthManager.refresh_token()` and writes the new `access_token` back into the `market-ai-dev/groww` secret.

**Rate limit:** Groww allows a maximum of 150 authentication requests per 24 hours per account. Do not invoke `token_refresh` in loops or more than a few times manually per day.

### Holdings and Positions

The evening handler reads Groww holdings via:
- `GET /holdings/user` — equity holdings (long-term CNC positions)
- `GET /positions/user` — intraday/positional holdings

Both calls are read-only and non-fatal. If Groww is unreachable or the token is stale, the evening scan logs a warning and continues; signals are still sent to Telegram.

---

## 11. Adding a New Strategy

**Step 1 — Create the strategy file** in `src/app/strategies/`:

```python
# src/app/strategies/my_strategy.py
from app.strategies.base import StrategyBase
from app.strategies.registry import register
from app.models.candle import Candle, Timeframe

@register
class MyStrategy(StrategyBase):
    strategy_id = "my_strategy"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 20

    @classmethod
    def default_params(cls) -> dict:
        return {"my_param": 1.0}

    def generate_signals(self, symbol: str, candles: list[Candle], as_of_index=None):
        ...
        return []  # list[Signal]
```

**Step 2 — Import it in the handlers** that should run it. Add a side-effect import to `src/market_ai/handlers/morning.py` and `evening.py`:

```python
import app.strategies.my_strategy  # noqa: F401
```

The `@register` decorator fires on import and adds the strategy to the registry. `all_strategies()` will include it automatically from that point.

**Step 3 — Write tests** in `tests/unit/strategies/test_my_strategy.py`. Use existing strategy test files as reference for fixture patterns and edge-case coverage (empty candle list, insufficient warmup bars, boundary conditions on each filter).

**Step 4 — Verify locally:**

```bash
pytest tests/unit/strategies/test_my_strategy.py -v

python -m scripts.run_backtest \
  --strategy my_strategy \
  --universe mock \
  --start 2023-01-01 \
  --end 2024-06-30
```

---

## 12. Key Constraints and Gotchas

| Constraint | Detail |
|---|---|
| `ENABLE_LIVE_ORDERS` must be `"true"` for real orders | Currently hardcoded to `"false"` in `main.tf` locals. `GrowwBroker.place_order()` raises `ValueError` if this env var is not exactly `"true"`. No orders are placed under any current configuration. |
| Lambda deployed as container image | Due to the 250 MB unzipped size limit for ZIP deployments, `package_type = "Image"` is used. Do not switch to ZIP without auditing total dependency size. |
| ECR push must happen before Terraform apply | The deploy job pushes the image first, then runs Terraform apply. Reversing this order causes a Lambda update failure because the image URI would not yet exist in ECR. |
| Terraform state backend | S3 bucket: `market-ai-tf-state-ap-south-1`, key: `market-ai/dev/terraform.tfstate`, lock table: `market-ai-tf-locks`. Both must exist before `terraform init` runs. |
| AWS auth — static keys only | `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` are used directly. No role assumption is configured in the S3 backend or AWS provider. Do not add an `assume_role` block to either. |
| Terraform >= 1.6 required | TF 1.6 changed the S3 backend `assume_role` syntax (block vs top-level attribute). The `required_version = ">= 1.6"` constraint in `main.tf` enforces this. |
| `M&M.NS` ampersand | Yahoo Finance accepts `M&M.NS` directly. No escaping or symbol substitution is needed. |
| `TATAMOTORS.NS` and `ZOMATO.NS` transient failures | These symbols occasionally return empty data from Yahoo Finance. `YFinanceProvider.get_historical_candles()` logs a warning and returns `[]`; the handler increments the error counter and continues to the next symbol. No manual action is needed. |
| Groww TOTP rate limit | 150 authentication requests per 24 hours. Do not invoke `token_refresh` more than a few times manually per day. The daily scheduled invocation counts toward this limit. |
| Warmup window | Each handler fetches 100 calendar days of daily data per symbol. If a symbol has fewer than `warmup_bars` candles in that window (e.g. recently listed), it generates no signals — this is expected behaviour. |
| NSE holiday handling | `YFinanceProvider.get_market_status()` uses weekday and wall-clock time only; it does not consult a holiday calendar. On NSE holidays, Lambda still invokes and scans; yfinance returns the last available close for any missing session, which is acceptable for all current strategy logic. |
| ECR lifecycle policy | Only the last 10 images are retained in ECR. Older images are expired automatically. Do not rely on image tags beyond the most recent 10 pushes. |

---

## Disclaimer

For personal research and education only. This software does not provide guaranteed returns or personalised investment advice. Market investments involve risk, including possible loss of capital. Verify all information with original sources before acting on any signal.
