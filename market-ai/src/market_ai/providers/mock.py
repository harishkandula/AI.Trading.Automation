from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from market_ai.models import Bar
from market_ai.providers.base import BrokerProvider

class MockBrokerProvider(BrokerProvider):
    def holdings(self) -> list[dict]:
        return [
            {"symbol": "DEMOALPHA", "quantity": 100, "average_price": 100.0},
            {"symbol": "DEMOBETA", "quantity": 40, "average_price": 180.0},
        ]

    def daily_bars(self) -> dict[str, list[Bar]]:
        today = datetime.now(tz=ZoneInfo("Asia/Kolkata")).date()
        output: dict[str, list[Bar]] = {}
        for symbol, base, drift in [("DEMOALPHA", 100.0, 0.7), ("DEMOBETA", 180.0, -0.1), ("DEMOGAMMA", 75.0, 0.35)]:
            bars = []
            for i in range(90):
                close = base + i * drift + ((i % 7) - 3) * 0.25
                bars.append(Bar(symbol, today - timedelta(days=89-i), close-0.5, close+1, close-1, close, 100000+i*2500))
            output[symbol] = bars
        return output
