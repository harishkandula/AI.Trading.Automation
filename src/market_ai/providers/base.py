from abc import ABC, abstractmethod

from market_ai.models import Bar

class DisabledTradingError(RuntimeError):
    pass

class BrokerProvider(ABC):
    @abstractmethod
    def holdings(self) -> list[dict]: ...

    @abstractmethod
    def daily_bars(self) -> dict[str, list[Bar]]: ...

    def place_order(self, *_: object, **__: object) -> str:
        raise DisabledTradingError("Live order placement is disabled")
