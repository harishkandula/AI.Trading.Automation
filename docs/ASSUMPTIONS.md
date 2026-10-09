# Assumptions

## Financial / Strategy Assumptions

1. **Intraday square-off time**: 15:15 IST. Configurable via `INTRADAY_SQUAREOFF_TIME`.
2. **No-new-positions cutoff**: 14:30 IST. Configurable.
3. **Strategy 1 "bump stop 2% up"**: interpreted as adding 2% of entry price to the original stop, bringing it approximately to breakeven. Configurable via `stop_adjustment_mode = FIXED_PCT_OF_ENTRY | FIXED_PCT_OF_STOP | BREAKEVEN`.
4. **Strategy 1 "break support"**: default mode SUPPORT_RECLAIM (price dips to/below support, then closes back above). Other modes: SUPPORT_BOUNCE, RESISTANCE_BREAKOUT.
5. **Same-bar target+stop ambiguity**: default conservative policy — assume stop hit first (worst case for long). Configurable.
6. **Gap-through stop**: executes at open price + adverse slippage, not at stop price.
7. **Holding period ("5 days")**: 5 trading sessions, not calendar days.
8. **Daily signal entry**: next trading session open, because daily close info is only known after close.
9. **Morning Star midpoint**: midpoint of Candle 1's real body = (open + close) / 2 of Candle 1.
10. **RSI cap for Strategy 3**: default 80 to avoid extended entries. Configurable.
11. **Gap-up volume comparison**: cumulative current-day volume vs average of prior 10 completed sessions. Intraday variant uses elapsed-session comparison.
12. **Survivorship bias**: backtests use only current index membership unless historical constituent data is supplied. Reports include a warning.
13. **Cost model**: default estimates (labeled as such). See docs/BACKTESTING_ASSUMPTIONS.md for rates and effective date.

## Data Assumptions

1. All timestamps stored as UTC internally; displayed as IST in reports.
2. Candle OHLCV validated: high>=open, high>=close, low<=open, low<=close, high>=low, prices>0, volume>=0.
3. VWAP resets at each NSE session start (09:15 IST).
4. Support/resistance computed using only completed prior bars.
5. Historical data availability limitations documented in docs/DATA_LIMITATIONS.md.

## Infrastructure Assumptions

1. AWS region: ap-south-1 (Mumbai). Configurable.
2. Default research capital: INR 5,000,000. Configurable.
3. PostgreSQL used for metadata. SQLite accepted for local dev (DATABASE_URL env var).
4. Parquet used for candle storage on S3 and locally.
5. Existing Lambda/EventBridge/S3/DynamoDB resources preserved; new ECS/RDS resources are additive.

## Out of Scope (This Release)

- Live order placement (ENABLE_LIVE_ORDERS must remain false)
- Fundamental data (P/E, EPS, etc.)
- Options or derivatives
- Sector-rotation logic (requires reliable sector classification data)
- Historical index constituent membership
