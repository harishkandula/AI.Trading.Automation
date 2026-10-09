"""Strategy 2: Daily Morning Star."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.candlestick_patterns import CandleAttrs, is_morning_star
from app.indicators.supertrend import supertrend
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class MorningStar(StrategyBase):
    strategy_id = "morning_star"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 30

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "c1_body_min_ratio": 0.60,
            "c2_body_max_ratio": 0.30,
            "stop_pct": 0.03,
            "holding_sessions": 5,
            "require_supertrend": False,
            "require_volume_above_avg": False,
            "volume_period": 20,
            "st_period": 10,
            "st_multiplier": 3.0,
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

        p = self.params
        st_results = supertrend(highs, lows, closes, p["st_period"], p["st_multiplier"])
        vol_avg = volume_sma(volumes, p["volume_period"])

        for i in range(max(self.warmup_bars, 2), as_of_index + 1):
            c1 = CandleAttrs(candles[i-2].open, candles[i-2].high, candles[i-2].low, candles[i-2].close)
            c2 = CandleAttrs(candles[i-1].open, candles[i-1].high, candles[i-1].low, candles[i-1].close)
            c3 = CandleAttrs(candles[i].open, candles[i].high, candles[i].low, candles[i].close)

            qualifies, pattern_reasons = is_morning_star(
                c1, c2, c3,
                c1_body_min_ratio=p["c1_body_min_ratio"],
                c2_body_max_ratio=p["c2_body_max_ratio"],
            )
            if not qualifies:
                continue

            reasons = list(pattern_reasons)
            fails: list[str] = []

            if p["require_supertrend"]:
                st = st_results[i]
                if st.direction == 1:
                    reasons.append(f"Supertrend positive ({st.value:.2f})")
                else:
                    fails.append("Supertrend not positive")

            if p["require_volume_above_avg"]:
                avg = vol_avg[i]
                if avg is not None and volumes[i] > avg:
                    reasons.append(f"Volume {volumes[i]:.0f} > {p['volume_period']}d avg {avg:.0f}")
                else:
                    fails.append("Volume not above average")

            if fails:
                continue

            ts = candles[i].timestamp
            entry_date = next_trading_day(ts.date())
            entry_ref = candles[i].close
            stop = round(entry_ref * (1 - p["stop_pct"]), 4)
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
                planned_entry_timestamp=datetime(entry_date.year, entry_date.month, entry_date.day, 9, 15, tzinfo=timezone.utc),
                entry_reference_price=entry_ref,
                stop_price=stop,
                holding_period_sessions=p["holding_sessions"],
                indicators={"c3_close": candles[i].close, "c1_body_ratio": round(c1.body_ratio, 3)},
                qualification_reasons=reasons,
                risk_warnings=["Historical strategy evidence required before acting"],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
