"""Strategy 1: Intraday Support Reclaim + Positive Supertrend."""
from __future__ import annotations

from typing import Any

from app.core.identifiers import signal_id
from app.core.time_utils import IST, INTRADAY_SIGNAL_START, INTRADAY_NO_NEW_ENTRY
from app.indicators.supertrend import supertrend
from app.indicators.support_resistance import rolling_support
from app.indicators.vwap import session_vwap
from app.models.candle import Candle, Timeframe
from app.models.signal import Signal, SignalStatus
from app.strategies.base import StrategyBase
from app.strategies.registry import register


@register
class IntradaySupportSupertrend(StrategyBase):
    strategy_id = "intraday_support_supertrend"
    strategy_version = "1.0.0"
    timeframe = Timeframe.M5
    warmup_bars = 60

    @classmethod
    def default_params(cls) -> dict[str, Any]:
        return {
            "mode": "SUPPORT_RECLAIM",  # SUPPORT_RECLAIM | SUPPORT_BOUNCE | RESISTANCE_BREAKOUT
            "support_lookback": 20,
            "support_tolerance_pct": 0.005,
            "st_period": 10,
            "st_multiplier": 3.0,
            "require_vwap_confirm": True,
            "stop_pct": 0.02,
            "target1_pct": 0.02,
            "target1_partial_exit_pct": 0.50,
            "stop_adjustment_mode": "FIXED_PCT_OF_ENTRY",
            "stop_adjustment_pct": 0.02,
            "squareoff_time": "15:15",
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
        timestamps = [c.timestamp for c in candles]

        p = self.params
        st_results = supertrend(highs, lows, closes, p["st_period"], p["st_multiplier"])
        support_vals = rolling_support(lows, p["support_lookback"])
        vwap_vals = session_vwap(timestamps, highs, lows, closes, volumes)

        mode = p["mode"]

        for i in range(max(self.warmup_bars, 1), as_of_index + 1):
            ts = candles[i].timestamp
            ts_ist = ts.astimezone(IST)
            bar_time = ts_ist.time()

            # Only signal within allowed trading window
            if bar_time < INTRADAY_SIGNAL_START or bar_time >= INTRADAY_NO_NEW_ENTRY:
                continue

            st = st_results[i]
            support = support_vals[i]
            if st.direction is None or support is None:
                continue

            prev_low = lows[i - 1]
            closes[i - 1]
            cur_close = closes[i]

            reasons: list[str] = []
            fails: list[str] = []

            # Mode-based entry condition
            if mode == "SUPPORT_RECLAIM":
                dipped = prev_low <= support * (1 + p["support_tolerance_pct"])
                reclaimed = cur_close > support
                if dipped:
                    reasons.append(f"Price tested support {support:.2f}")
                else:
                    fails.append(f"Price did not test support {support:.2f}")
                if reclaimed:
                    reasons.append(f"Close {cur_close:.2f} reclaimed above support")
                else:
                    fails.append(f"Close {cur_close:.2f} did not reclaim support")
            elif mode == "SUPPORT_BOUNCE":
                near = (
                    cur_close >= support
                    and cur_close <= support * (1 + p["support_tolerance_pct"])
                )
                if near:
                    reasons.append(
                        f"Close {cur_close:.2f} bouncing near support {support:.2f}"
                    )
                else:
                    fails.append("Price not bouncing near support")
            elif mode == "RESISTANCE_BREAKOUT":
                from app.indicators.support_resistance import rolling_resistance
                resistance_vals = rolling_resistance(highs, p["support_lookback"])
                resistance = resistance_vals[i]
                if resistance is not None and cur_close > resistance:
                    reasons.append(f"Breakout above resistance {resistance:.2f}")
                else:
                    fails.append("No resistance breakout")

            # Supertrend must be positive
            if st.direction == 1:
                reasons.append(f"Supertrend positive ({st.value:.2f})")
            else:
                fails.append("Supertrend not positive")

            # Optional VWAP confirmation
            if p["require_vwap_confirm"]:
                vwap = vwap_vals[i]
                if vwap is not None and cur_close > vwap:
                    reasons.append(f"Close {cur_close:.2f} above VWAP {vwap:.2f}")
                elif vwap is None:
                    reasons.append("VWAP unavailable – skipping confirmation")
                else:
                    fails.append(f"Close {cur_close:.2f} below VWAP {vwap:.2f}")

            if fails:
                continue

            entry_ref = cur_close
            stop = round(entry_ref * (1 - p["stop_pct"]), 4)
            target1 = round(entry_ref * (1 + p["target1_pct"]), 4)
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
                planned_entry_type="NEXT_BAR_OPEN",
                entry_reference_price=entry_ref,
                stop_price=stop,
                target_prices=[target1],
                indicators={
                    "support": round(support, 2),
                    "supertrend": round(st.value or 0, 2),
                    "vwap": round(vwap_vals[i] or 0, 2),
                    "mode": mode,
                },
                qualification_reasons=reasons,
                risk_warnings=[
                    "Intraday only – square-off at 15:15",
                    "Short history – preliminary results only",
                    "Same-bar ambiguity: conservative (stop first) policy applied",
                ],
                parameter_snapshot=dict(self.params),
                status=SignalStatus.ELIGIBLE,
            ))

        return signals
