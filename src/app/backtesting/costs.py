"""
Indian equity cost model.

DISCLAIMER: Rates below are ESTIMATES as of 2024.
Verify current statutory rates before using for real decisions.
Source your own rates from NSE/BSE circulars and SEBI regulations.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CostProfile:
    name: str
    # Brokerage as fraction of turnover (both sides)
    brokerage_pct: float = 0.0003          # 0.03% per side (estimate)
    # STT: buy+sell delivery = 0.1% on both; intraday sell only = 0.025%
    stt_delivery_pct: float = 0.001        # per side on delivery
    stt_intraday_pct: float = 0.00025      # sell side only for intraday
    # Exchange transaction charge (NSE)
    exchange_charge_pct: float = 0.0000297 # estimate
    # SEBI regulatory fee
    sebi_fee_pct: float = 0.000001
    # GST on (brokerage + exchange charges)
    gst_pct: float = 0.18
    # Stamp duty (buy side only, delivery)
    stamp_duty_pct: float = 0.00015
    # Slippage (each side)
    slippage_pct: float = 0.001
    # Segment
    is_intraday: bool = False
    disclaimer: str = "ESTIMATED – verify current statutory rates from NSE/SEBI circulars"

    def compute(self, entry_value: float, exit_value: float) -> dict[str, float]:
        """
        Returns itemized costs and total for a round-trip trade.
        entry_value = entry_price * quantity
        exit_value  = exit_price  * quantity
        """
        brokerage = (entry_value + exit_value) * self.brokerage_pct

        if self.is_intraday:
            stt = exit_value * self.stt_intraday_pct
        else:
            stt = (entry_value + exit_value) * self.stt_delivery_pct

        exchange = (entry_value + exit_value) * self.exchange_charge_pct
        sebi = (entry_value + exit_value) * self.sebi_fee_pct
        gst = (brokerage + exchange) * self.gst_pct
        stamp = entry_value * self.stamp_duty_pct if not self.is_intraday else 0.0
        slippage = (entry_value + exit_value) * self.slippage_pct

        total = brokerage + stt + exchange + sebi + gst + stamp + slippage

        return {
            "brokerage": round(brokerage, 4),
            "stt": round(stt, 4),
            "exchange_charge": round(exchange, 4),
            "sebi_fee": round(sebi, 4),
            "gst": round(gst, 4),
            "stamp_duty": round(stamp, 4),
            "slippage": round(slippage, 4),
            "total": round(total, 4),
            "disclaimer": self.disclaimer,
        }


DELIVERY_PROFILE = CostProfile(name="default_delivery_estimate", is_intraday=False)
INTRADAY_PROFILE = CostProfile(name="default_intraday_estimate", is_intraday=True)
ZERO_COST_PROFILE = CostProfile(
    name="zero_cost",
    brokerage_pct=0, stt_delivery_pct=0, stt_intraday_pct=0,
    exchange_charge_pct=0, sebi_fee_pct=0, gst_pct=0,
    stamp_duty_pct=0, slippage_pct=0,
    disclaimer="WARNING: Zero-cost model – gross returns only",
)


def get_profile(name: str) -> CostProfile:
    profiles = {
        "default_delivery_estimate": DELIVERY_PROFILE,
        "default_intraday_estimate": INTRADAY_PROFILE,
        "zero_cost": ZERO_COST_PROFILE,
    }
    if name not in profiles:
        raise ValueError(f"Unknown cost profile '{name}'. Available: {list(profiles)}")
    return profiles[name]
