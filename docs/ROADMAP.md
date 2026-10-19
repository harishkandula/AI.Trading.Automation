# Professional Trading Automation — Roadmap

This document captures the honest assessment of where the system is today,
what a professional-grade trading automation system looks like, and the
phased plan to get there.

---

## Honest Assessment: Where We Are Today

```
Infrastructure
  ✓ AWS Lambda, S3, DynamoDB, ECR, EventBridge, Secrets Manager
  ✓ Terraform IaC, GitHub Actions CI/CD
  ✓ Container image deployment (handles large dependencies)

Signal Generation
  ✓ Event-driven backtesting engine (no look-ahead bias)
  ✓ 3 working strategies: gap_up_volume, morning_star, rsi_supertrend_volume
  ✓ Backtest framework with Sharpe, drawdown, profit factor metrics
  ✓ Telegram notifications (morning scan, evening review, weekly backtest)

Gaps
  ✗ Data layer — yfinance is unstable, poor mid/small-cap coverage
  ✗ Market regime detection — system runs strategies in all conditions
  ✗ Signal scoring — no confidence ranking, all signals treated equally
  ✗ Risk management — entire layer missing (position sizing, loss limits)
  ✗ Walk-forward validation — backtests tested on seen data only
  ✗ Live execution — disabled intentionally, no GTT order placement
  ✗ Multi-index support — limited to NIFTY 50 (48 symbols)
```

The foundation is real and reusable. The gaps are data quality and risk
management — those two determine whether a system makes or loses money.

---

## The Mental Model

Think of the system as a factory with five departments.
Most retail systems have department 2. Almost none have 1 and 3 done properly.

```
┌─────────────────────────────────────────────────────────────┐
│  1. DATA          Raw OHLCV, fundamentals, FII/DII flows    │
│  2. SIGNALS       Strategy engine, scoring, ranking         │
│  3. RISK          Position sizing, loss limits, regime      │
│  4. EXECUTION     Orders, GTT, trade logging                │
│  5. ANALYTICS     Performance review, strategy tuning       │
└─────────────────────────────────────────────────────────────┘
```

Department 3 (Risk) is what separates systems that survive from those that
blow up. It is more important than having good strategies.

---

## Layer 1: Data Infrastructure (the foundation)

### Problem with yfinance

yfinance scrapes Yahoo Finance — it was never meant for production use.

| Issue | Impact |
|---|---|
| Inconsistent 404s (TATAMOTORS, ZOMATO) | Missed signals |
| No SLA, breaks without notice | Unreliable daily runs |
| Poor mid/small-cap NSE coverage | Can't scale beyond NIFTY 50 |
| No live quotes | Can't verify entry prices |
| No fundamentals | Can't do earnings-based strategies |

### Target architecture

```
4:00 AM  Fetch previous day's OHLCV for all symbols → append to Parquet on S3
4:15 AM  Fetch corporate actions (bonus, split, dividend)
4:20 AM  Adjust historical prices for corporate actions
4:30 AM  Pre-compute indicators (RSI, EMA, ATR, SuperTrend) → cache to S3
4:45 AM  Set "data ready" flag in SSM Parameter Store
7:00 AM  Morning Lambda reads from S3 — never calls broker API at scan time
```

Data is fetched once and stored permanently as Parquet files on S3.
The morning scan reads pre-computed data — no API calls, no failures at 7 AM.

```
s3://market-ai-data/ohlcv/daily/RELIANCE.parquet    ← grows by 1 row each day
s3://market-ai-data/ohlcv/5min/RELIANCE.parquet     ← for intraday strategies
s3://market-ai-data/indicators/RELIANCE.parquet     ← pre-computed RSI, EMA etc.
s3://market-ai-data/fundamentals/RELIANCE.json      ← quarterly results
s3://market-ai-data/index/nifty500_constituents.csv ← updated quarterly
```

### Recommended data providers

| Provider | Cost | Coverage | Reliability | Notes |
|---|---|---|---|---|
| **Zerodha Kite Connect** | ₹2,000/month | NSE + BSE all segments | Very high | Best for serious use |
| **Dhan API** | Free | NSE + BSE | High | Best free option |
| **Angel One SmartAPI** | Free | NSE + BSE | Medium | Good fallback |
| **Upstox API** | Free tier | NSE + BSE | High | Alternative |
| yfinance | Free | NSE partial | Low | Current, replace ASAP |

**Recommendation: Open Zerodha or Dhan account. Choose based on:**
- Zerodha — if trading seriously (₹2,000/month API worth it above ₹5L capital)
- Dhan — if starting out or cost-conscious (free API, good docs)

---

## Layer 2: Market Regime Detection

**The most important layer most systems skip.**

Before running any strategy, the system must ask: what kind of market is this?

```
REGIME = one of:
  TRENDING_UP      NIFTY above 200 EMA, VIX < 15, consistent trend
  TRENDING_DOWN    NIFTY below 200 EMA
  CHOPPY           NIFTY crossing 200 EMA repeatedly, no clear direction
  HIGH_VOLATILITY  India VIX > 20, large daily swings
```

### Strategy-regime compatibility

| Strategy | Trending Up | Trending Down | Choppy | High VIX |
|---|---|---|---|---|
| 52-week breakout | ✓ Run | ✗ Skip | ✗ Skip | ⚠ Reduce size |
| RSI pullback | ✓ Run | ✗ Skip | ✓ Run | ⚠ Reduce size |
| EMA crossover | ✓ Run | ✗ Skip | ✗ Skip | ⚠ Reduce size |
| Opening range breakout | ✓ Run | ✓ Run (short) | ⚠ Reduce | ✗ Skip |
| Sector rotation | ✓ Run | ✓ Run | ✓ Run | ⚠ Reduce size |

**Why this matters:** The same strategy in a trending market has a 60% win
rate. In a choppy market it drops to 35%. Same code, completely different
outcome. Running strategies in the wrong regime is the single biggest cause
of drawdown in retail algo systems.

---

## Layer 3: Signal Generation (improved)

### Current vs target

| | Current | Target |
|---|---|---|
| Strategies | 3 | 8–10 |
| Timeframes | Daily only | Daily + 5-min (intraday) |
| Scoring | None | 0–100 confidence score |
| Universe | NIFTY 50 (48 symbols) | NIFTY 500 |
| Regime awareness | None | Built in |

### Strategies that work in Indian markets (evidence-based)

**Strategy 1: 52-Week High Breakout (Momentum)**
```
Logic:     Institutional accumulation precedes breakouts. Stocks near
           52-week highs attract momentum buyers.
Regime:    TRENDING_UP only
Signal:    Price closes above 52-week high with volume > 1.5× average
Win rate:  ~45% but avg win is 2.5× avg loss (positive expectancy)
Hold:      10–20 sessions
```

**Strategy 2: EMA Crossover with Volume (Trend following)**
```
Logic:     20 EMA crossing above 50 EMA signals medium-term trend change.
           Volume confirms institutional participation.
Regime:    TRENDING_UP
Signal:    20 EMA crosses 50 EMA upward, price above 200 EMA, volume spike
Win rate:  ~42%, avg win 3× avg loss
Hold:      15–30 sessions
```

**Strategy 3: RSI Pullback in Uptrend (Mean reversion)**
```
Logic:     Temporary weakness in structurally strong stocks. The 200 EMA
           filter is crucial — avoids buying falling knives.
Regime:    TRENDING_UP, mildly CHOPPY
Signal:    RSI < 35, price above 200 EMA (key filter), volume spike on down day
Win rate:  ~55%, avg win 1.5× avg loss
Hold:      5–10 sessions
```

**Strategy 4: Opening Range Breakout (Intraday)**
```
Logic:     First 15-min candle range predicts day direction when NIFTY
           confirms the same direction.
Regime:    All, with NIFTY direction filter
Signal:    Price breaks above/below first 15-min candle high/low with
           2× average volume, NIFTY confirming
Win rate:  ~50%, tight stops
Hold:      Same day, exit before 3:20 PM
```

**Strategy 5: Sector Rotation**
```
Logic:     Money flows from sector to sector in a cycle. Detect early.
Regime:    All
Signal:    Sector outperforming NIFTY by >3% over 20 days, FII buying
           sector ETFs, individual stock breaking out within sector
Win rate:  ~50%, large moves
Hold:      20–40 sessions
```

### Signal scoring (0–100)

Every signal is scored before trading. Only signals >= 65 are acted upon.

```
Base score:   50

+10   Regime matches strategy's preferred regime
+10   Sector outperforming NIFTY over last 20 days
+10   FII net buyers in sector for last 3 sessions
+10   Stock above 20 EMA, 50 EMA, 200 EMA (all aligned)
+10   Volume > 1.5× 20-day average on signal day

-10   Earnings announcement within next 5 trading days
-10   Stock already up > 15% in last 20 days (extended/risky)
-20   India VIX > 20

Threshold:  Only trade signals scoring >= 65
```

**Example scored signal:**
```
Symbol:       RELIANCE.NS
Strategy:     52-week breakout
Score:        78/100

Triggers:
  ✓ (+10) TRENDING_UP regime
  ✓ (+10) Energy sector outperforming NIFTY last 20 days
  ✓ (+10) FII net buyers last 3 sessions
  ✓ (+10) Above 20/50/200 EMA
  ✗ (  0) Volume only 1.2× average (threshold not met)
  ✗ (-10) Earnings in 6 days (just outside penalty window)
  ✓ (  0) VIX = 13 (no penalty)

Entry:        2847
Stop loss:    2780 (below breakout base, -2.3%)
Target 1:     2960 (+4%)
Target 2:     3050 (+7%)
Hold:         15 sessions max
```

---

## Layer 4: Risk Management (non-negotiable)

**This layer must exist before live orders are ever enabled.**

### Portfolio-level rules

```
Max open positions:        8–10
Max capital in one sector: 25%
Max capital in one stock:  10%
Daily loss limit:          2% of portfolio → stop all trading today
Weekly loss limit:         5% of portfolio → stop all trading this week
Peak drawdown limit:       15% from portfolio peak → pause and review
```

### Per-trade rules

```
Risk per trade:            1% of portfolio value
Position size formula:     (Portfolio × 1%) / (entry_price - stop_price)
Maximum position value:    10% of portfolio (overrides size formula if larger)
No-entry buffer:           Skip if price moved > 1% from signal price
Market open buffer:        No new entries 9:15–9:20 AM
Earnings blackout:         No new entries 3 sessions before results
```

### Position sizing example

```
Portfolio:        ₹5,00,000
1% risk:          ₹5,000
Signal:           RELIANCE entry 2847, stop 2780
Stop distance:    ₹67 per share

Formula size:     ₹5,000 / ₹67 = 74 shares → value ₹2,10,678 (42% — too large)
Cap at 10%:       ₹50,000 / 2847 = 17 shares → actual risk ₹1,139 (0.2%)

Always take the smaller of the two.
```

---

## Layer 5: Execution

```
Signal passes risk checks
        ↓
Place BUY order at market open (9:20 AM, after 5-min buffer)
        ↓
Order fills at ₹X (confirmed via broker API)
        ↓
Immediately place GTT order (Good Till Triggered):
  Leg 1: Stop loss  at ₹(entry - stop_distance)
  Leg 2: Target     at ₹(entry + 2 × stop_distance)   ← minimum 2:1 R:R
        ↓
Log to DynamoDB:
  symbol, strategy, score, entry_price, stop, target,
  quantity, capital_at_risk, regime_at_entry, timestamp
        ↓
Telegram confirmation sent
        ↓
Evening Lambda checks GTT status, logs exits, sends P&L update
```

**Why GTT over a polling Lambda:**
Exchange monitors the GTT — not our server. Triggers at exact price.
Works even if AWS has an issue. Exits in seconds, not 15 minutes.

---

## Layer 6: Analytics and Learning

Every Sunday the system produces a performance report:

```
Strategy performance (last 30 days):
  52wk_breakout:    12 trades  58% win rate  Sharpe 1.8  ← healthy
  rsi_pullback:      8 trades  37% win rate  Sharpe 0.4  ← underperforming
  ema_crossover:     6 trades  50% win rate  Sharpe 1.2  ← acceptable

Market regime:
  Correctly identified TRENDING_UP 18/22 days

Top performing sectors:    IT (+8.2% vs NIFTY), Banking (+5.1%)
Worst performing sectors:  Pharma (-3.1%), FMCG (-1.8%)

Suggested tuning:
  rsi_pullback win rate improved to 52% if RSI threshold raised 30 → 35
```

This feedback loop is what separates a system that improves from one that
stagnates. Every month you have data to make evidence-based changes.

---

## What Determines Success

The code matters less than these:

| Factor | Why it matters |
|---|---|
| **Data quality** | Garbage in, garbage out. No strategy survives bad data. |
| **Risk management** | Bad strategy + good risk = slow losses, time to fix. Good strategy + no risk = one bad week blows up. |
| **Regime awareness** | Same strategy: 60% win rate trending, 35% win rate choppy. |
| **Discipline** | System will have losing weeks. Overriding it emotionally is how people fail. The code enforces rules when you don't want to. |
| **Walk-forward validation** | Backtesting on seen data is memorising answers. Test on data the strategy has never seen. |

**The single biggest edge for a retail algo trader in India:**
Discipline. You don't panic. You don't revenge trade. You don't hold losers
hoping they recover. The code does what the rules say, every time.

**Realistic win rate expectation:** 40–55% depending on strategy.
Profitability comes from the profit factor (avg win / avg loss > 2.0),
not from being right most of the time.

---

## Phased Roadmap

### Phase 1 — Solid Foundation (4–6 weeks)
- [ ] Open Zerodha or Dhan account
- [ ] Replace yfinance with broker's historical API
- [ ] Build data pipeline: fetch → S3 Parquet → pre-compute indicators
- [ ] SSM Parameter Store for runtime config (universe, thresholds)
- [ ] Market regime detection (NIFTY 200 EMA + VIX)
- [ ] Expand universe to NIFTY 500 constituents

### Phase 2 — Better Signals (4–6 weeks)
- [ ] Signal scoring system (0–100 confidence)
- [ ] Add 52-week breakout strategy
- [ ] Add EMA crossover strategy
- [ ] Add sector rotation strategy
- [ ] Walk-forward backtesting (out-of-sample validation)
- [ ] Paper trading: real signals, no real money, 4 weeks minimum

### Phase 3 — Risk Layer (2–3 weeks)
- [ ] Position sizing engine (1% risk rule)
- [ ] Portfolio-level limits (max positions, sector caps)
- [ ] Daily and weekly loss limits (auto-halt)
- [ ] Earnings blackout calendar
- [ ] All of the above must pass before live orders are enabled

### Phase 4 — Live Execution (carefully)
- [ ] Enable live orders (ENABLE_LIVE_ORDERS=true)
- [ ] GTT placement immediately after entry fill
- [ ] Trade log in DynamoDB (every order, every exit)
- [ ] Start with ₹50,000–₹1,00,000 capital
- [ ] Monitor daily for 4 weeks before increasing position sizes

### Phase 5 — Scale and Dashboard
- [ ] Streamlit dashboard: signals, P&L, backtest results, config UI
- [ ] ECS Fargate for large universe runs (500+ symbols, no Lambda timeout)
- [ ] OpenAI integration for natural language daily summaries
- [ ] Signal ML scoring model trained on live trade results
- [ ] Expand to NIFTY Total Market (750 symbols)

---

## Current System vs Target

| Component | Current | Target |
|---|---|---|
| Data source | yfinance (unstable) | Zerodha Kite / Dhan API |
| Data storage | Fetched on demand | S3 Parquet (permanent) |
| Universe | NIFTY 50 (48 symbols) | NIFTY 500 |
| Regime detection | None | NIFTY 200 EMA + VIX |
| Strategies | 3 (daily only) | 8–10 (daily + intraday) |
| Signal scoring | None | 0–100 confidence score |
| Risk management | None | Full position sizing + limits |
| Execution | Manual (research only) | GTT orders via broker API |
| Validation | In-sample backtest only | Walk-forward (out-of-sample) |
| Configuration | Code / Terraform | SSM Parameter Store (live) |
| Dashboard | Telegram only | Streamlit web UI |

---

## The Bottom Line

90% of automated trading systems — even well-built ones — don't beat a
simple NIFTY 50 SIP after costs and taxes. The ones that do are built over
2–3 years of iteration with real capital at risk.

That said, a well-built system doesn't need to beat the market to be
valuable. It needs to be **consistent, disciplined, and better than a human
is emotionally**. That is achievable, and this roadmap is how to get there.

The current foundation is solid and reusable. Phase 1 is the right next
step — get the data layer right before building anything else on top of it.
