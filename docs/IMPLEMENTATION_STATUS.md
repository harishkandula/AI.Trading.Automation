# Implementation Status

Last updated: 2026-10-28

## Deployed and Working

| Component | Status | Notes |
|---|---|---|
| CI/CD pipeline (GitHub Actions) | COMPLETE | validate → docker-build → deploy |
| Terraform IaC | COMPLETE | Lambda, ECS, S3, DynamoDB, ECR, EventBridge, Secrets Manager |
| Container image deployment | COMPLETE | All Lambdas + ECS share one ECR image |
| NSE bhavcopy data pipeline | COMPLETE | Daily 04:00 IST, BhavcopBlockedError handled |
| Corporate action detection | COMPLETE | Ratio threshold 0.75/1.35, yfinance re-adjust, adjusted_flag on S3 |
| HybridDataProvider (fill_gaps=False) | COMPLETE | Scan handlers read S3 only — zero API calls at scan time |
| 8-strategy signal engine | COMPLETE | s00_benchmark, s01_rs_momentum, s02_vcp, s03_trend_pullback, gap_up_volume, morning_star, rsi_supertrend_volume, intraday_support_supertrend |
| NIFTY 500 universe (513 symbols) | COMPLETE | 82 absent symbols removed; SYMBOL_RENAMES tracked |
| Morning scan Lambda | COMPLETE | 07:00 IST, scores signals, risk guard, Telegram |
| Evening scan Lambda | COMPLETE | 19:00 IST, Groww holdings read |
| Weekly compounder Lambda | COMPLETE | Sunday 10:00 IST |
| Token refresh Lambda | COMPLETE | Daily 06:00 IST |
| Pre-warm pings | COMPLETE | 06:58 and 18:58 IST to eliminate cold start |
| Signal scoring (0-100) | COMPLETE | regime/RS/sector/setup/confluence dimensions |
| Portfolio risk guard | COMPLETE | Sector + segment caps, position limits |
| ECS Fargate backtest | COMPLETE | Sunday 11:00 IST, 4 vCPU / 8 GB, no timeout |
| Leaderboard Lambda | COMPLETE | Sunday 12:30 IST, profitable-first ranking |
| DynamoDB leaderboard table | COMPLETE | ECS task role IAM fixed |
| NSE holiday calendar | COMPLETE | 2024–2027 covered |
| Telegram notifications | COMPLETE | Signals, daily summary, error alerts, freshness warnings |
| Data freshness check | COMPLETE | morning.py + evening.py check S3 age vs prev_trading_day |
| Survivorship bias warning | COMPLETE | Logged once per backtest run |
| Warmup bar guard | COMPLETE | Skips symbol+strategy if candles < warmup_bars + 5 |
| Intraday strategy guard | COMPLETE | M5 strategies skipped in daily scan handlers |

## Known Gaps (Intentional — Not Bugs)

| Gap | Notes |
|---|---|
| Market regime computation | `MarketRegime.UNKNOWN` = neutral score. Needs NIFTY trend state from S3. |
| RS percentile | `rs_percentile=None` = neutral. Needs cross-symbol RS scan at morning time. |
| Sector rank | `sector_rank=None` = neutral. Needs cross-sector comparison. |
| Weekly fundamentals API | Price-based EMA200 proxy in use. Tijori Finance / Tickertape integration pending. |
| NIFTY 500 semi-annual rebalance | Static Oct 2026 snapshot. Manual refresh needed each March / September. |
| Point-in-time universe | Survivorship bias in backtest. Historical constituent data needed. |
| Live order execution | `ENABLE_LIVE_ORDERS` is `"false"` and hardcoded. RESEARCH_ONLY mode by design. |
