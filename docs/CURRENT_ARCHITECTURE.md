# Current Architecture

## Repository State (inspected 2026-10-09)

### Duplicated source trees
The repository contains two identical copies of the application source:
- `src/market_ai/` — used by pyproject.toml (pythonpath = ["src"])
- `market-ai/src/market_ai/` — appears to be a stale copy

`src/market_ai/` is the active tree. `market-ai/` is superseded and preserved only for reference.

### Active Components

| Component | File | Role |
|-----------|------|------|
| Settings | src/market_ai/config.py | Dataclass with safety guard |
| Bar / Candidate | src/market_ai/models.py | OHLCV and research output |
| S3Store | src/market_ai/storage.py | put_json helper (unused by handlers) |
| Morning handler | src/market_ai/handlers/morning.py | Swing watchlist Lambda |
| Evening handler | src/market_ai/handlers/evening.py | Post-market scan Lambda |
| Weekly handler | src/market_ai/handlers/weekly.py | Compounder screen Lambda |
| Common bootstrap | src/market_ai/handlers/common.py | Settings validation + mock provider |
| BrokerProvider ABC | src/market_ai/providers/base.py | Abstract interface + DisabledTradingError |
| MockBrokerProvider | src/market_ai/providers/mock.py | 90-day synthetic bars |
| GrowwBrokerProvider | src/market_ai/providers/groww.py | Stub (NotImplementedError) |
| Indicators | src/market_ai/services/indicators.py | sma, pct_change, normalize |
| Scoring | src/market_ai/services/scoring.py | swing_candidate, compounder_candidate |
| Reporting | src/market_ai/services/reporting.py | render_report dict |

### Infrastructure
- Terraform 1.6+, AWS provider ~5.0, region ap-south-1
- S3 bucket (versioned, AES256)
- DynamoDB table (PAY_PER_REQUEST, PITR)
- 3 Lambda functions (Python 3.12, 512 MB, 120s)
- EventBridge cron rules (morning Mon-Fri 01:30 UTC, evening 13:30 UTC, weekly Sun 04:30 UTC)
- IAM role scoped to logs + S3 + DynamoDB

### CI/CD
- GitHub Actions: validate (pytest + ruff + tf fmt-check) → deploy (build zip + tf apply)
- AWS auth: static key secrets (AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY)

### Tests (3 passing)
- test_swing_score_range
- test_live_orders_disabled
- test_compounder_requires_data

### Gaps
- S3Store exists but handlers never call it
- DynamoDB provisioned but nothing writes to it
- Groww provider is NotImplementedError
- No intraday data support
- No backtest engine
- No paper trading
- No notifications
- No screener framework
- No Docker
- No PostgreSQL
