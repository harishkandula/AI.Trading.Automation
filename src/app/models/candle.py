from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, model_validator


class Timeframe(str, Enum):
    M1 = "1m"
    M5 = "5m"
    M15 = "15m"
    M30 = "30m"
    H1 = "1h"
    D1 = "1d"


class Candle(BaseModel):
    instrument_id: str
    exchange: str
    trading_symbol: str
    timeframe: Timeframe
    timestamp: datetime          # UTC
    open: float
    high: float
    low: float
    close: float
    volume: float
    source: str = "unknown"
    adjusted: bool = False
    ingested_at: datetime | None = None

    @model_validator(mode="after")
    def _validate_ohlcv(self) -> "Candle":
        if self.open <= 0:
            raise ValueError(f"open must be > 0, got {self.open}")
        if self.high <= 0:
            raise ValueError(f"high must be > 0, got {self.high}")
        if self.low <= 0:
            raise ValueError(f"low must be > 0, got {self.low}")
        if self.close <= 0:
            raise ValueError(f"close must be > 0, got {self.close}")
        if self.volume < 0:
            raise ValueError(f"volume must be >= 0, got {self.volume}")
        if self.high < self.open - 1e-9:
            raise ValueError(f"high {self.high} < open {self.open}")
        if self.high < self.close - 1e-9:
            raise ValueError(f"high {self.high} < close {self.close}")
        if self.low > self.open + 1e-9:
            raise ValueError(f"low {self.low} > open {self.open}")
        if self.low > self.close + 1e-9:
            raise ValueError(f"low {self.low} > close {self.close}")
        if self.high < self.low - 1e-9:
            raise ValueError(f"high {self.high} < low {self.low}")
        return self

    @property
    def typical_price(self) -> float:
        return (self.high + self.low + self.close) / 3
