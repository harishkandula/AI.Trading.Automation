from __future__ import annotations


class MarketAIError(Exception):
    """Base error for all platform exceptions."""


class ConfigurationError(MarketAIError):
    """Missing or invalid configuration."""


class DataError(MarketAIError):
    """Bad or missing market data."""


class DataQualityError(DataError):
    """OHLCV validation failure."""


class ProviderError(MarketAIError):
    """External data provider error."""


class RateLimitError(ProviderError):
    """Provider rate-limit hit."""


class LiveOrdersDisabledError(MarketAIError):
    """Raised when live order placement is attempted."""


class StrategyError(MarketAIError):
    """Strategy configuration or execution error."""


class BacktestError(MarketAIError):
    """Backtesting engine error."""


class PaperTradingError(MarketAIError):
    """Paper-trading engine error."""


class NotificationError(MarketAIError):
    """Notification delivery error (non-fatal)."""


class InsufficientDataError(DataError):
    """Not enough bars for the requested computation."""
