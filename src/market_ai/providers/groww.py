"""Read-only Groww adapter boundary.

Official docs: https://groww.in/trade-api/docs
This adapter intentionally avoids guessing authentication or endpoint behavior.
Wire documented SDK methods only after reviewing the account's current API documentation.
"""
from market_ai.providers.base import BrokerProvider, DisabledTradingError

class GrowwBrokerProvider(BrokerProvider):
    def __init__(self, token: str | None = None) -> None:
        self._token = token

    def holdings(self) -> list[dict]:
        raise NotImplementedError("Configure against official Groww SDK documentation")

    def daily_bars(self) -> dict:
        raise NotImplementedError("Configure against official Groww historical-data documentation")

    def place_order(self, *_: object, **__: object) -> str:
        raise DisabledTradingError("Live Groww order placement is disabled")
