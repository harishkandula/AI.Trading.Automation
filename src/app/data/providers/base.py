"""Abstract market data provider."""
from __future__ import annotations

from abc import ABC, abstractmethod
from datetime import date

from app.models.candle import Candle, Timeframe
from app.models.instrument import Instrument


class MarketDataProvider(ABC):
    @abstractmethod
    def get_instruments(self) -> list[Instrument]: ...

    @abstractmethod
    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> list[Candle]: ...

    def get_live_quote(self, symbol: str) -> dict: ...

    def get_market_status(self) -> dict: ...
