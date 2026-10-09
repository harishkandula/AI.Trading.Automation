"""Strategy 3: RSI > 60 + positive Supertrend + volume > prev day."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.rsi import wilder_rsi
from app.indicators.supertrend import supertrend
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class RSISuperTrendVolume(StrategyBase):
    strategy_id = "rsi_supertrend_volume"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 30

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "rsi_period": 14,
            "rsi_min": 60.0,
            "rsi_max": 80.0,
            "st_period": 10,
            "st_multiplier": 3.0,
            "volume_variant": "prev_day",   # prev_day | 1.25x_10d | 1.50x_20d
            "stop_pct": 0.03,
            "holding_sessions": 5,
            "ema_filter": False,
        }

    def generate_signals(
        self,
        symbol: str,
        candles: list[Candle],
        as_of_index: int | None = None,
    ) -> list[Signal]:
        if as_of_index is None:
            as_of_index = len(candles) - 1

        signals: list[Signal] = []
        closes = [c.close for c in candles]
        highs = [c.high for c in candles]
        lows = [c.low for c in candles]
        volumes = [c.volume for c in candles]

        from app.core.exceptions import InsufficientDataError
        try:
            rsi_values = wilder_rsi(closes, self.params["rsi_period"])
        except InsufficientDataError:
            return []
        try:
            st_results = supertrend(highs, lows, closes, self.params["st_period"], self.params["st_multiplier"])
        except InsufficientDataError:
            return []

        variant = self.params["volume_variant"]
        vol_avg_10 = volume_sma(volumes, 10)
        vol_avg_20 = volume_sma(volumes, 20)

        p = self.params
        for i in range(self.warmup_bars, as_of_index + 1):
            rsi_val = rsi_values[i]
            st = st_results[i]
            if rsi_val is None or st.direction is None:
                continue

            reasons: list[str] = []
            fails: list[str] = []

            # RSI condition
            if p["rsi_min"] <= rsi_val <= p["rsi_max"]:
                reasons.append(f"RSI({p['rsi_period']})={rsi_val:.1f} in [{p['rsi_min']},{p['rsi_max']}]")
            else:
                fails.append(f"RSI={rsi_val:.1f} outside [{p['rsi_min']},{p['rsi_max']}]")

            # Supertrend
            if st.direction == 1:
                reasons.append(f"Supertrend positive ({st.value:.2f})")
            else:
                fails.append("Supertrend not positive")

            # Volume
            vol_ok = False
            if variant == "prev_day":
                if i >= 1:
                    vol_ok = volumes[i] > volumes[i - 1]
                    if vol_ok:
                        reasons.append(f"Volume {volumes[i]:.0f} > prev {volumes[i-1]:.0f}")
                    else:
                        fails.append(f"Volume {volumes[i]:.0f} <= prev {volumes[i-1]:.0f}")
            elif variant == "1.25x_10d":
                avg = vol_avg_10[i]
                if avg is not None:
                    threshold = 1.25 * avg
                    vol_ok = volumes[i] >= threshold
                    if vol_ok:
                        reasons.append(f"Volume {volumes[i]:.0f} >= 1.25x 10d avg {threshold:.0f}")
                    else:
                        fails.append(f"Volume < 1.25x 10d avg")
            elif variant == "1.50x_20d":
                avg = vol_avg_20[i]
                if avg is not None:
                    threshold = 1.50 * avg
                    vol_ok = volumes[i] >= threshold
                    if vol_ok:
                        reasons.append(f"Volume {volumes[i]:.0f} >= 1.50x 20d avg {threshold:.0f}")
                    else:
                        fails.append(f"Volume < 1.50x 20d avg")

            if fails:
                continue

            ts = candles[i].timestamp
            decision_ts = ts
            entry_date = next_trading_day(ts.date())
            entry_ref = candles[i].close
            stop = round(entry_ref * (1 - p["stop_pct"]), 4)
            sid = signal_id(self.strategy_id, self.strategy_version, symbol, decision_ts.isoformat())

            signals.append(Signal(
                signal_id=sid,
                strategy_id=self.strategy_id,
                strategy_version=self.strategy_version,
                symbol=symbol,
                exchange="NSE",
                timeframe=self.timeframe.value,
                signal_timestamp=ts,
                decision_timestamp=decision_ts,
                planned_entry_type="NEXT_SESSION_OPEN",
                planned_entry_timestamp=datetime(entry_date.year, entry_date.month, entry_date.day, 9, 15, tzinfo=timezone.utc),
                entry_reference_price=entry_ref,
                stop_price=stop,
                holding_period_sessions=p["holding_sessions"],
                indicators={"rsi": round(rsi_val, 2), "supertrend": round(st.value or 0, 2), "volume": volumes[i]},
                qualification_reasons=reasons,
                risk_warnings=["Historical strategy evidence required before acting"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
