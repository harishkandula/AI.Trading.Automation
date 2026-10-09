"""Strategy base class and interface."""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any

from app.models.candle import Candle, Timeframe
from app.models.signal import Signal


class StrategyBase(ABC):
    strategy_id: str = ""
    strategy_version: str = "1.0.0"
    timeframe: Timeframe = Timeframe.D1
    warmup_bars: int = 30

    def __init__(self, params: dict[str, Any] | None = None) -> None:
        self.params: dict[str, Any] = self.default_params()
        if params:
            self._validate_params(params)
            self.params.update(params)

    @classmethod
    @abstractmethod
    def default_params(cls) -> dict[str, Any]:
        """Return the canonical default parameters."""

    def _validate_params(self, params: dict[str, Any]) -> None:
        """Override to add parameter validation."""

    @abstractmethod
    def generate_signals(
        self,
        symbol: str,
        candles: list[Candle],
        as_of_index: int | None = None,
    ) -> list[Signal]:
        """
        Generate signals from candles up to as_of_index (inclusive).
        as_of_index = None means use all candles.
        Must NOT access candles beyond as_of_index.
        """

    def params_hash(self) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(self.params, sort_keys=True).encode()).hexdigest()[:16]

    def describe(self) -> str:
        return f"{self.strategy_id} v{self.strategy_version} | {self.timeframe.value} | params={self.params}"
