"""Strategy leaderboard — DynamoDB persistence for walk-forward results.

Schema (leaderboard table):
  pk  = strategy_id          (e.g. "s01_rs_momentum")
  sk  = run_date             (ISO date, e.g. "2025-10-26")
  All aggregated fields stored as top-level attributes for easy querying.

Access patterns:
  - write_result()         : put one strategy result for today's run
  - read_latest_per_strategy(): scan table, keep newest sk per pk → ranked list
"""
from __future__ import annotations

import json
import logging
import os
from decimal import Decimal
from typing import Any

log = logging.getLogger(__name__)

_TABLE_ENV = "LEADERBOARD_TABLE"


def _table_name() -> str:
    name = os.environ.get(_TABLE_ENV, "")
    if not name:
        raise RuntimeError(f"{_TABLE_ENV} environment variable is not set")
    return name


def _to_dynamo(value: Any) -> Any:
    """Recursively convert floats to Decimal for DynamoDB."""
    if isinstance(value, float):
        return Decimal(str(round(value, 6)))
    if isinstance(value, dict):
        return {k: _to_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamo(v) for v in value]
    return value


def _from_dynamo(value: Any) -> Any:
    """Recursively convert Decimal back to float."""
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, dict):
        return {k: _from_dynamo(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_from_dynamo(v) for v in value]
    return value


def write_result(
    strategy_id: str,
    run_date: str,
    strategy_version: str,
    aggregated: dict[str, Any],
    provenance: dict[str, Any],
    windows_count: int,
    s3_key: str = "",
) -> None:
    """Persist a walk-forward result to the leaderboard DynamoDB table."""
    import boto3  # type: ignore[import-untyped]

    table = boto3.resource("dynamodb").Table(_table_name())
    item: dict[str, Any] = {
        "pk": strategy_id,
        "sk": run_date,
        "strategy_version": strategy_version,
        "windows_count": windows_count,
        "s3_key": s3_key,
        **{k: v for k, v in aggregated.items() if v is not None},
        "provenance": json.dumps(provenance),
    }
    table.put_item(Item=_to_dynamo(item))
    log.info("Leaderboard: wrote %s @ %s", strategy_id, run_date)


def read_latest_per_strategy() -> list[dict[str, Any]]:
    """Scan the leaderboard table and return the most recent entry per strategy."""
    import boto3  # type: ignore[import-untyped]

    table = boto3.resource("dynamodb").Table(_table_name())
    response = table.scan()
    items = [_from_dynamo(item) for item in response.get("Items", [])]

    # Keep only the latest run_date per strategy
    latest: dict[str, dict[str, Any]] = {}
    for item in items:
        sid = item.get("pk", "")
        run_date = item.get("sk", "")
        if sid not in latest or run_date > latest[sid].get("sk", ""):
            latest[sid] = item

    return list(latest.values())


def rank_leaderboard(entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort entries by avg_sharpe descending (None treated as -inf)."""
    def _key(e: dict[str, Any]) -> float:
        v = e.get("avg_sharpe")
        return float(v) if v is not None else float("-inf")

    return sorted(entries, key=_key, reverse=True)
