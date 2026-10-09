"""Strategy registry – central lookup."""
from __future__ import annotations


from app.strategies.base import StrategyBase

_REGISTRY: dict[str, type[StrategyBase]] = {}


def register(cls: type[StrategyBase]) -> type[StrategyBase]:
    """Decorator to register a strategy class."""
    if not cls.strategy_id:
        raise ValueError(f"Strategy {cls.__name__} must define strategy_id")
    _REGISTRY[cls.strategy_id] = cls
    return cls


def get(strategy_id: str) -> type[StrategyBase]:
    if strategy_id not in _REGISTRY:
        raise KeyError(f"Strategy '{strategy_id}' not registered. Available: {list(_REGISTRY)}")
    return _REGISTRY[strategy_id]


def all_ids() -> list[str]:
    return list(_REGISTRY.keys())


def all_strategies() -> dict[str, type[StrategyBase]]:
    return dict(_REGISTRY)
