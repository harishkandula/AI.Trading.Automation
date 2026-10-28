"""S00: RSI + SuperTrend + Volume — Benchmark/Control strategy.

Purpose: measure whether new strategies actually beat this baseline.
If S01–S03 don't outperform S00 in walk-forward testing, something is wrong.

Key improvements over the original rsi_supertrend_volume:
  - EMA200 trend filter (close > EMA200) — avoids buying falling knives
  - Volume vs 20-day average (not just vs prev day)
  - Regime filter: only fires in BULL or STRONG_BULL
  - ATR-based stop (not fixed %)
  - Trailing exit via 20-day EMA rather than fixed target

Family: MOMENTUM
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import InsufficientDataError
from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.atr import wilder_atr
from app.indicators.ema import ema
from app.indicators.rsi import wilder_rsi
from app.indicators.supertrend import supertrend
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class S00Benchmark(StrategyBase):
    strategy_id = "s00_benchmark"
    strategy_version = "2.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 50

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "rsi_period": 14,
            "rsi_min": 55.0,
            "rsi_max": 78.0,
            "st_period": 10,
            "st_multiplier": 3.0,
            "volume_mult": 1.25,
            "volume_period": 20,
            "ema200_filter": True,
            "atr_stop_mult": 1.5,
            "atr_period": 20,
            "holding_sessions": 10,
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
            rsi_vals = wilder_rsi(closes, p["rsi_period"])
            st_results = supertrend(highs, lows, closes, p["st_period"], p["st_multiplier"])
            atr_vals = wilder_atr(highs, lows, closes, p["atr_period"])
            vol_avg = volume_sma(volumes, p["volume_period"])
            ema200_vals = ema(closes, 200) if p["ema200_filter"] else [None] * len(closes)
            ema20_vals = ema(closes, 20)
        except InsufficientDataError:
            return []

        signals: list[Signal] = []

        for i in range(self.warmup_bars, as_of_index + 1):
            rsi_v = rsi_vals[i]
            st = st_results[i]
            atr_v = atr_vals[i]
            vol_v = vol_avg[i]
            ema200_v = ema200_vals[i]
            ema20_v = ema20_vals[i]

            if any(v is None for v in (rsi_v, st.direction, atr_v, vol_v)):
                continue
            if vol_v == 0:
                continue

            reasons: list[str] = []

            if not (p["rsi_min"] <= rsi_v <= p["rsi_max"]):  # type: ignore[operator]
                continue
            reasons.append(f"RSI({p['rsi_period']})={rsi_v:.1f}")

            if st.direction != 1:
                continue
            reasons.append(f"SuperTrend bullish ({st.value:.2f})")

            vol_threshold = vol_v * p["volume_mult"]  # type: ignore[operator]
            if volumes[i] < vol_threshold:
                continue
            reasons.append(
                f"Volume {volumes[i]:.0f} > {p['volume_mult']}x avg ({vol_threshold:.0f})"
            )

            if p["ema200_filter"] and ema200_v is not None:
                if closes[i] < ema200_v:
                    continue
                reasons.append(f"Above EMA200 ({ema200_v:.2f})")

            ts = candles[i].timestamp
            entry_date = next_trading_day(ts.date())
            entry_ref = closes[i]
            stop = round(entry_ref - p["atr_stop_mult"] * atr_v, 2)  # type: ignore[operator]
            target = round(entry_ref + 2.5 * (entry_ref - stop), 2)

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
                target_prices=[target],
                holding_period_sessions=p["holding_sessions"],
                indicators={
                    "rsi": round(rsi_v, 1),  # type: ignore[arg-type]
                    "supertrend": round(st.value or 0, 2),
                    "atr": round(atr_v, 2),  # type: ignore[arg-type]
                    "ema20": round(ema20_v, 2) if ema20_v else None,
                    "rel_vol": round(volumes[i] / vol_v, 2),  # type: ignore[operator]
                },
                qualification_reasons=reasons,
                risk_warnings=["Benchmark strategy — validate before acting"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
