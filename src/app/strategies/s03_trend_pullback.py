"""S03: Trend Pullback.

Structurally opposite to S01 and S02 — buys temporary weakness inside
a confirmed strong uptrend. The entry is during the pullback, not at the
breakout. This gives better entries but requires the trend to be unambiguously
established before looking for weakness.

Logic:
  - S01/S02 buy strength and expansion
  - S03 buys the dip in an already-strong stock

Entry conditions:
  1. Primary trend non-negotiable:
     close > EMA200, EMA50 > EMA200, EMA200 rising for ≥20 sessions
  2. Recent strength: stock made a 20-day high in the last 10 sessions
  3. Pullback to EMA20 or EMA50 zone: price within 3% of EMA20 or EMA50
  4. RSI cooling: RSI(14) dropped to 42–58 (not oversold, just resting)
  5. Volume declining during pullback (no distribution / selling pressure)
  6. Reversal trigger: bullish close (close > open) with volume recovering

Stop:  below EMA50 (or 1.5× ATR if tighter)
Exit:  RSI > 68 OR new 20-day high made then price reverses OR max 15 sessions

Regime: BULL or STRONG_BULL only
  (don't buy pullbacks in SIDEWAYS — could be distribution)

Family: PULLBACK
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

_EMA200_SLOPE_DAYS = 20
_PULLBACK_ZONE_PCT = 0.03     # within 3% of EMA20 or EMA50
_PULLBACK_VOL_BARS = 5        # volume window during pullback
_RECENT_HIGH_WINDOW = 10      # must have made 20-day high in last N sessions


@register
class S03TrendPullback(StrategyBase):
    strategy_id = "s03_trend_pullback"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "ema_fast": 20,
            "ema_mid": 50,
            "ema_slow": 200,
            "rsi_period": 14,
            "rsi_pullback_min": 42.0,
            "rsi_pullback_max": 58.0,
            "pullback_zone_pct": 0.03,
            "recent_high_window": 10,
            "vol_decline_ratio": 0.85,
            "atr_period": 20,
            "atr_stop_mult": 1.5,
            "holding_sessions": 15,
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
        opens = [c.open for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]
        p = self.params

        try:
            ema20_vals = ema(closes, p["ema_fast"])
            ema50_vals = ema(closes, p["ema_mid"])
            ema200_vals = ema(closes, p["ema_slow"])
            rsi_vals = wilder_rsi(closes, p["rsi_period"])
            atr_vals = wilder_atr(highs, lows, closes, p["atr_period"])
            vol_avg_20 = volume_sma(volumes, 20)
        except InsufficientDataError:
            return []

        signals: list[Signal] = []

        for i in range(self.warmup_bars, as_of_index + 1):
            ema20_v = ema20_vals[i]
            ema50_v = ema50_vals[i]
            ema200_v = ema200_vals[i]
            rsi_v = rsi_vals[i]
            atr_v = atr_vals[i]
            vol20_v = vol_avg_20[i]

            if any(v is None for v in (ema20_v, ema50_v, ema200_v, rsi_v, atr_v, vol20_v)):
                continue

            price = closes[i]
            reasons: list[str] = []

            # 1. Primary trend structure: close > EMA200, EMA50 > EMA200
            if not (price > ema200_v and ema50_v > ema200_v):  # type: ignore[operator]
                continue

            # 2. EMA200 must be rising for at least 20 sessions
            try:
                slope = ema_slope(closes[: i + 1], p["ema_slow"], _EMA200_SLOPE_DAYS)
                if slope <= 0:
                    continue
            except InsufficientDataError:
                continue
            reasons.append(f"EMA200 rising (slope={slope:.3f})")

            # 3. Stock made a 20-day high in the last N sessions
            rh_window = p["recent_high_window"]
            if i < 20 + rh_window:
                continue
            high_20d = max(highs[i - 20: i])
            recent_highs_window = highs[i - rh_window: i]
            made_recent_high = any(h >= high_20d * 0.995 for h in recent_highs_window)
            if not made_recent_high:
                continue
            reasons.append(f"Made 20d high in last {rh_window} sessions")

            # 4. Price has pulled back to EMA20 or EMA50 zone (within pullback_zone_pct)
            near_ema20 = abs(price - ema20_v) / ema20_v <= p["pullback_zone_pct"]  # type: ignore[operator]
            near_ema50 = abs(price - ema50_v) / ema50_v <= p["pullback_zone_pct"]  # type: ignore[operator]
            if not (near_ema20 or near_ema50):
                continue
            support_label = "EMA20" if near_ema20 else "EMA50"
            reasons.append(f"Pulled back to {support_label} zone")

            # 5. RSI cooled down to pullback range
            if not (p["rsi_pullback_min"] <= rsi_v <= p["rsi_pullback_max"]):  # type: ignore[operator]
                continue
            reasons.append(f"RSI(14)={rsi_v:.1f} in pullback zone")

            # 6. Volume declining during pullback (last 5 bars vs 20-day avg)
            pullback_vol = vol_avg_20[max(0, i - _PULLBACK_VOL_BARS): i + 1]
            recent_vol_avg = sum(v for v in pullback_vol if v is not None) / max(
                len([v for v in pullback_vol if v is not None]), 1
            )
            if recent_vol_avg >= vol20_v * p["vol_decline_ratio"]:  # type: ignore[operator]
                continue
            reasons.append(
                f"Volume declining during pullback ({recent_vol_avg:.0f} < "
                f"{p['vol_decline_ratio']}x avg)"
            )

            # 7. Reversal trigger: bullish candle (close > open)
            if closes[i] <= opens[i]:
                continue
            candle_body_pct = (closes[i] - opens[i]) / opens[i] * 100
            reasons.append(f"Bullish reversal candle ({candle_body_pct:.1f}% body)")

            # Stop: below EMA50 or 1.5× ATR (whichever is tighter / higher)
            atr_stop = price - p["atr_stop_mult"] * atr_v  # type: ignore[operator]
            ema50_stop = ema50_v * 0.995  # type: ignore[operator]
            stop = round(max(atr_stop, ema50_stop), 2)

            risk = price - stop
            target = round(price + 2.0 * risk, 2)

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
                target_prices=[target],
                holding_period_sessions=p["holding_sessions"],
                indicators={
                    "rsi": round(rsi_v, 1),  # type: ignore[arg-type]
                    "ema20": round(ema20_v, 2),  # type: ignore[arg-type]
                    "ema50": round(ema50_v, 2),  # type: ignore[arg-type]
                    "ema200": round(ema200_v, 2),  # type: ignore[arg-type]
                    "support": support_label,
                },
                qualification_reasons=reasons,
                risk_warnings=["Pullback entry — confirm trend intact before entering"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
