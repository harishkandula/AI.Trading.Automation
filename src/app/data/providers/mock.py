"""Synthetic data provider for testing and local development."""
from __future__ import annotations

from datetime import date, datetime

from app.core.time_utils import IST, trading_days_between
from app.data.providers.base import MarketDataProvider
from app.models.candle import Candle, Timeframe
from app.models.instrument import Instrument

_SYMBOLS = [
    ("DEMOALPHA", 500.0, 0.15),
    ("DEMOBETA",  300.0, 0.08),
    ("DEMOGAMMA", 800.0, 0.22),
    ("DEMODELTA", 150.0, -0.05),
    ("DEMOEPSILON", 1200.0, 0.30),
]


class MockDataProvider(MarketDataProvider):
    """
    Deterministic synthetic data for testing.
    NEVER use these in reported strategy results.
    """
    LABEL = "SYNTHETIC_TEST_DATA"

    def get_instruments(self) -> list[Instrument]:
        return [
            Instrument(
                instrument_id=sym,
                trading_symbol=sym,
                exchange="NSE",
                segment="CASH",
                instrument_type="EQ",
                tradable=True,
            )
            for sym, _, _ in _SYMBOLS
        ]

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> list[Candle]:
        params = {s: (base, drift) for s, base, drift in _SYMBOLS}
        if symbol not in params:
            return []
        base, drift = params[symbol]
        days = trading_days_between(start, end)
        candles = []
        price = base
        for i, d in enumerate(days):
            cycle = ((i % 7) - 3) * 0.5
            price = max(1.0, price + drift * base / 252 + cycle)
            ts = datetime(d.year, d.month, d.day, 9, 15, tzinfo=IST)
            volume = 500_000 + i * 1_000
            candles.append(Candle(
                instrument_id=symbol,
                exchange="NSE",
                trading_symbol=symbol,
                timeframe=timeframe,
                timestamp=ts,
                open=round(price - 0.5, 2),
                high=round(price + 1.0, 2),
                low=round(price - 1.2, 2),
                close=round(price, 2),
                volume=float(volume),
                source=self.LABEL,
            ))
        return candles
