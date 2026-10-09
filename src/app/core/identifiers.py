from __future__ import annotations

import hashlib
import uuid


def new_uuid() -> str:
    return str(uuid.uuid4())


def deterministic_id(*parts: str) -> str:
    """SHA-256-based deterministic ID for idempotency keys."""
    combined = "|".join(str(p) for p in parts)
    return hashlib.sha256(combined.encode()).hexdigest()[:32]


def signal_id(strategy_id: str, strategy_version: str, symbol: str, decision_ts: str) -> str:
    return deterministic_id(strategy_id, strategy_version, symbol, decision_ts)


def backtest_run_id(strategy_id: str, universe: str, start: str, end: str, params_hash: str) -> str:
    return deterministic_id(strategy_id, universe, start, end, params_hash)
