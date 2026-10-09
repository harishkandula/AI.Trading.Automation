"""Strategy registry – central lookup."""
from __future__ import annotations

from typing import Type

from app.strategies.base import StrategyBase

_REGISTRY: dict[str, Type[StrategyBase]] = {}


def register(cls: Type[StrategyBase]) -> Type[StrategyBase]:
    """Decorator to register a strategy class."""
    if not cls.strategy_id:
        raise ValueError(f"Strategy {cls.__name__} must define strategy_id")
    _REGISTRY[cls.strategy_id] = cls
    return cls


def get(strategy_id: str) -> Type[StrategyBase]:
    if strategy_id not in _REGISTRY:
        raise KeyError(f"Strategy '{strategy_id}' not registered. Available: {list(_REGISTRY)}")
    return _REGISTRY[strategy_id]


def all_ids() -> list[str]:
    return list(_REGISTRY.keys())


def all_strategies() -> dict[str, Type[StrategyBase]]:
    return dict(_REGISTRY)
