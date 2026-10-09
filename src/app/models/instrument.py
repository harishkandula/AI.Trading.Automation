from __future__ import annotations

from pydantic import BaseModel


class Instrument(BaseModel):
    instrument_id: str
    trading_symbol: str
    exchange: str = "NSE"
    segment: str = "CASH"
    instrument_type: str = "EQ"
    isin: str = ""
    sector: str = ""
    industry: str = ""
    tradable: bool = True
    lot_size: int = 1

    @property
    def is_eligible_equity(self) -> bool:
        return (
            self.exchange == "NSE"
            and self.segment == "CASH"
            and self.instrument_type == "EQ"
            and self.tradable
        )
