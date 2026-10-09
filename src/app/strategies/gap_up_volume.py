"""Strategy 4: Gap-up > 3% + volume >= 3x 10-day average."""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.core.identifiers import signal_id
from app.core.time_utils import next_trading_day
from app.indicators.volume import volume_sma
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class GapUpVolume(StrategyBase):
    strategy_id = "gap_up_volume"
    strategy_version = "1.0.0"
    timeframe = Timeframe.D1
    warmup_bars = 15

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "gap_min_pct": 3.0,
            "volume_multiplier": 3.0,
            "volume_lookback": 10,
            "require_close_above_open": True,
            "stop_pct": 0.03,
            "holding_sessions": 5,
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
        volumes = [c.volume for c in candles]

        p = self.params
        vol_avg = volume_sma(volumes, p["volume_lookback"])

        for i in range(max(self.warmup_bars, 1), as_of_index + 1):
            prev_close = closes[i - 1]
            cur_open = candles[i].open
            cur_close = candles[i].close
            cur_vol = volumes[i]

            gap_pct = (cur_open - prev_close) / prev_close * 100.0

            reasons: list[str] = []
            fails: list[str] = []

            if gap_pct > p["gap_min_pct"]:
                reasons.append(f"Gap {gap_pct:.2f}% > {p['gap_min_pct']}%")
            else:
                fails.append(f"Gap {gap_pct:.2f}% <= {p['gap_min_pct']}%")

            avg = vol_avg[i - 1]  # use prior complete sessions only
            if avg is not None:
                threshold = p["volume_multiplier"] * avg
                if cur_vol >= threshold:
                    reasons.append(f"Volume {cur_vol:.0f} >= {p['volume_multiplier']}x {p['volume_lookback']}d avg {threshold:.0f}")
                else:
                    fails.append(f"Volume {cur_vol:.0f} < {p['volume_multiplier']}x avg {threshold:.0f}")
            else:
                fails.append("Insufficient volume history")

            if p["require_close_above_open"] and cur_close <= cur_open:
                fails.append(f"Close {cur_close:.2f} <= Open {cur_open:.2f} (gap not sustained)")
            elif p["require_close_above_open"]:
                reasons.append(f"Close {cur_close:.2f} > Open {cur_open:.2f}")

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
                indicators={"gap_pct": round(gap_pct, 2), "volume": cur_vol, "volume_avg": round(avg or 0, 0)},
                qualification_reasons=reasons,
                risk_warnings=[
                    "Historical strategy evidence required before acting",
                    "NOTE: Volume comparison uses prior completed sessions only",
                ],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
