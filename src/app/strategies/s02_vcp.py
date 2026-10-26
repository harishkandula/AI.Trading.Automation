"""S02: Volatility Contraction Pattern (VCP) — Mark Minervini methodology.

A VCP forms when a stock in a strong uptrend goes through a series of
contracting price swings on declining volume. When price breaks out of the
tight base on expanding volume, it signals institutional accumulation completing.

Look-ahead guard: every measurement uses only data available at bar i.
The contraction window is fully closed before the breakout bar.

Entry conditions:
  1. Long-term trend: close > EMA50 > EMA200 (must be present before contraction)
  2. RS percentile > 70 (strong relative strength — scored at handler layer)
  3. Prior advance: stock up > 15% from its 60-day low (worth contracting from)
  4. Volatility contracting: ATR(10)/close falling over last 15 bars
  5. Last 15-day price range < prior 30-day range × 0.6 (range compression)
  6. Volume drying up during contraction (last 10-day vol avg < 20-day avg)
  7. Breakout trigger: today closes above 15-day high
  8. Breakout volume: > 1.75× 20-day average (expansion on breakout day)

Stop:  low of the contraction base (last 15-day low)
Exit:  take 50% off at 2R, trail remainder at 2× ATR
       OR max holding period 30 sessions

Family: BREAKOUT
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.exceptions import InsufficientDataError
from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.atr import wilder_atr
from app.indicators.ema import ema
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register

_CONTRACTION_BARS = 15
_BASE_BARS = 30       # prior base for range comparison
_LOOKBACK_60 = 60     # for prior advance measurement
_VOL_CONTRACTION_BARS = 10


@register
class S02VCP(StrategyBase):
    strategy_id = "s02_vcp"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 80

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "ema_fast": 50,
            "ema_slow": 200,
            "prior_advance_min_pct": 15.0,
            "range_compression_ratio": 0.6,
            "breakout_vol_mult": 1.75,
            "vol_contraction_ratio": 0.85,  # contraction-window vol < 85% of 20d avg
            "atr_stop_period": 14,
            "holding_sessions": 30,
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
            atr_vals = wilder_atr(highs, lows, closes, p["atr_stop_period"])
            vol_avg_20 = volume_sma(volumes, 20)
        except InsufficientDataError:
            return []

        signals: list[Signal] = []
        min_i = self.warmup_bars

        for i in range(min_i, as_of_index + 1):
            ema50_v = ema50_vals[i]
            ema200_v = ema200_vals[i]
            atr_v = atr_vals[i]
            vol20_v = vol_avg_20[i]

            if any(v is None for v in (ema50_v, ema200_v, atr_v, vol20_v)):
                continue

            price = closes[i]
            reasons: list[str] = []

            # 1. Trend structure at breakout bar
            if not (price > ema50_v > ema200_v):  # type: ignore[operator]
                continue

            # 2. Prior advance: up > min% from 60-day low
            low_60 = min(lows[max(0, i - _LOOKBACK_60): i])
            if low_60 <= 0:
                continue
            advance_pct = (price - low_60) / low_60 * 100
            if advance_pct < p["prior_advance_min_pct"]:
                continue
            reasons.append(f"Prior advance {advance_pct:.1f}% from 60d low")

            # 3. Range compression over contraction window (bars i-15 to i-1)
            # Must use only past data — contraction window ends yesterday
            if i < _BASE_BARS + _CONTRACTION_BARS:
                continue

            contr_start = i - _CONTRACTION_BARS
            base_start = i - _BASE_BARS - _CONTRACTION_BARS

            contr_range = max(highs[contr_start:i]) - min(lows[contr_start:i])
            base_range = max(highs[base_start: contr_start]) - min(lows[base_start: contr_start])

            if base_range <= 0:
                continue
            compression = contr_range / base_range
            if compression > p["range_compression_ratio"]:
                continue
            reasons.append(f"Range compressed to {compression * 100:.1f}% of prior base")

            # 4. Volume drying up during contraction
            contr_vol_avg = sum(volumes[contr_start:i]) / _CONTRACTION_BARS
            vol_contraction_ok = contr_vol_avg < vol20_v * p["vol_contraction_ratio"]  # type: ignore[operator]
            if not vol_contraction_ok:
                continue
            reasons.append(
                f"Volume drying up ({contr_vol_avg:.0f} < "
                f"{p['vol_contraction_ratio']}x 20d avg)"
            )

            # 5. ATR(10) / close declining over contraction — volatility contracting
            # Compare ATR at start vs end of contraction window
            atr_at_contr_start = atr_vals[contr_start]
            if atr_at_contr_start is None or closes[contr_start] <= 0:
                continue
            atr_pct_start = atr_at_contr_start / closes[contr_start]
            atr_pct_now = atr_v / price  # type: ignore[operator]
            if atr_pct_now >= atr_pct_start:
                continue
            reasons.append(
                f"ATR contracting ({atr_pct_now * 100:.2f}% vs {atr_pct_start * 100:.2f}%)"
            )

            # 6. Breakout: today closes above 15-day high (prior to today)
            high_15d = max(highs[contr_start:i])
            if price <= high_15d:
                continue
            reasons.append(f"Breakout above 15d high ({high_15d:.2f})")

            # 7. Breakout volume expansion
            rel_vol = volumes[i] / vol20_v  # type: ignore[operator]
            if rel_vol < p["breakout_vol_mult"]:
                continue
            reasons.append(f"Breakout volume {rel_vol:.2f}x 20d avg")

            # Stop = low of contraction base
            base_low = min(lows[contr_start:i])
            stop = round(base_low * 0.995, 2)   # small buffer below base low
            risk = price - stop
            target_1 = round(price + 2.0 * risk, 2)   # 2R — take half
            target_2 = round(price + 4.0 * risk, 2)   # 4R — trail remainder

            ts = candles[i].timestamp
            entry_date = next_trading_day(ts.date())
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
                entry_reference_price=price,
                stop_price=stop,
                target_prices=[target_1, target_2],
                holding_period_sessions=p["holding_sessions"],
                indicators={
                    "ema50": round(ema50_v, 2),  # type: ignore[arg-type]
                    "ema200": round(ema200_v, 2),  # type: ignore[arg-type]
                    "compression_pct": round(compression * 100, 1),
                    "breakout_vol_x": round(rel_vol, 2),
                    "prior_advance_pct": round(advance_pct, 1),
                },
                qualification_reasons=reasons,
                risk_warnings=["VCP requires objective base criteria — verify manually"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
