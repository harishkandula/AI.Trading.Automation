"""S01: Relative Strength Momentum.

The primary alpha strategy. Buys stocks that are stronger than the market
and their sector, in a confirmed uptrend, with price momentum intact.

Logic: "stronger than alternatives" — not just is the stock strong,
but is it stronger than 80% of the NIFTY 500 universe right now?

Entry conditions:
  1. Close > EMA50 > EMA200 (trend structure)
  2. EMA200 rising (long-term trend healthy)
  3. RS percentile vs universe > 80 (top 20% of 500 stocks)
  4. RS percentile vs sector > 70 (leading its own sector)
  5. 60-day return positive
  6. RSI(14) between 50–75 (momentum without being overbought)
  7. Volume > 1.2× 20-day average
  8. Regime: BULL or STRONG_BULL (passed in via indicators dict)

Stop:  1.5× ATR(20) below entry
Exit:  trailing 20 EMA (handled by exit monitor, not this strategy)

Family: MOMENTUM
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import InsufficientDataError
from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.atr import wilder_atr
from app.indicators.ema import ema, ema_slope
from app.indicators.rsi import wilder_rsi
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class S01RSMomentum(StrategyBase):
    strategy_id = "s01_rs_momentum"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "rsi_min": 50.0,
            "rsi_max": 75.0,
            "rsi_period": 14,
            "ema_fast": 50,
            "ema_slow": 200,
            "ema200_slope_days": 10,
            "rs_universe_min": 80.0,   # min percentile vs NIFTY 500
            "rs_sector_min": 70.0,     # min percentile vs sector
            "volume_mult": 1.2,
            "volume_period": 20,
            "atr_period": 20,
            "atr_stop_mult": 1.5,
            "holding_sessions": 20,
        }

    def generate_signals(
        self,
        symbol: str,
        candles: list[Candle],
        as_of_index: int | None = None,
    ) -> list[Signal]:
        if as_of_index is None:
            as_of_index = len(candles) - 1

        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        p = self.params

        try:
            ema50_vals = ema(closes, p["ema_fast"])
            ema200_vals = ema(closes, p["ema_slow"])
            rsi_vals = wilder_rsi(closes, p["rsi_period"])
            atr_vals = wilder_atr(highs, lows, closes, p["atr_period"])
            vol_avg = volume_sma(volumes, p["volume_period"])
        except InsufficientDataError:
            return []

        signals: list[Signal] = []

        for i in range(self.warmup_bars, as_of_index + 1):
            ema50_v = ema50_vals[i]
            ema200_v = ema200_vals[i]
            rsi_v = rsi_vals[i]
            atr_v = atr_vals[i]
            vol_v = vol_avg[i]

            if any(v is None for v in (ema50_v, ema200_v, rsi_v, atr_v, vol_v)):
                continue

            price = closes[i]
            reasons: list[str] = []

            # 1. Trend structure: close > EMA50 > EMA200
            if not (price > ema50_v > ema200_v):  # type: ignore[operator]
                continue
            reasons.append(f"EMA50({ema50_v:.2f}) > EMA200({ema200_v:.2f}), price above both")

            # 2. EMA200 rising
            try:
                slope = ema_slope(closes[: i + 1], p["ema_slow"], p["ema200_slope_days"])
                if slope <= 0:
                    continue
                reasons.append(f"EMA200 rising (slope={slope:.3f})")
            except InsufficientDataError:
                continue

            # 3. RSI range
            if not (p["rsi_min"] <= rsi_v <= p["rsi_max"]):  # type: ignore[operator]
                continue
            reasons.append(f"RSI(14)={rsi_v:.1f}")

            # 4. Volume expansion
            if volumes[i] < vol_v * p["volume_mult"]:  # type: ignore[operator]
                continue
            rel_vol = volumes[i] / vol_v  # type: ignore[operator]
            reasons.append(f"Volume {rel_vol:.2f}x 20d avg")

            # RS percentile and regime gating are applied at the handler/scoring layer.
            # The strategy fires the setup; the scorer applies the regime/RS gate.

            # 60-day positive return
            if i >= 60:
                ret_60d = (closes[i] - closes[i - 60]) / closes[i - 60]
                if ret_60d <= 0:
                    continue
                reasons.append(f"60d return {ret_60d * 100:.1f}%")

            ts = candles[i].timestamp
            entry_date = next_trading_day(ts.date())
            entry_ref = price
            stop = round(entry_ref - p["atr_stop_mult"] * atr_v, 2)  # type: ignore[operator]

            sid = signal_id(self.strategy_id, self.strategy_version, symbol, ts.isoformat())
            signals.append(Signal(
                signal_id=sid,
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                symbol=symbol,
                exchange="NSE",
                timeframe=self.timeframe.value,
                signal_timestamp=ts,
                decision_timestamp=ts,
                planned_entry_type="NEXT_SESSION_OPEN",
                planned_entry_timestamp=datetime(
                    entry_date.year, entry_date.month, entry_date.day, 9, 15, tzinfo=timezone.utc
                ),
                entry_reference_price=entry_ref,
                stop_price=stop,
                target_prices=[],   # trailing exit via 20 EMA — no fixed target
                holding_period_sessions=p["holding_sessions"],
                indicators={
                    "rsi": round(rsi_v, 1),  # type: ignore[arg-type]
                    "ema50": round(ema50_v, 2),  # type: ignore[arg-type]
                    "ema200": round(ema200_v, 2),  # type: ignore[arg-type]
                    "atr": round(atr_v, 2),  # type: ignore[arg-type]
                    "rel_vol": round(rel_vol, 2),
                },
                qualification_reasons=reasons,
                risk_warnings=["RS/regime score applied at scan layer"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
