"""Tests for the strategy leaderboard module."""
from __future__ import annotations

import sys
from decimal import Decimal
from typing import Any

sys.path.insert(0, "src")

from app.backtesting.leaderboard import (
    _from_dynamo,
    _to_dynamo,
    rank_leaderboard,
)


# ---------------------------------------------------------------------------
# DynamoDB type conversion helpers
# ---------------------------------------------------------------------------

class TestDynamoConversion:
    def test_float_to_decimal(self) -> None:
        result = _to_dynamo(1.23456)
        assert isinstance(result, Decimal)

    def test_dict_with_floats_converted(self) -> None:
        d = {"sharpe": 1.5, "win_rate": 60.0, "name": "s01"}
        result = _to_dynamo(d)
        assert isinstance(result["sharpe"], Decimal)
        assert isinstance(result["win_rate"], Decimal)
        assert result["name"] == "s01"

    def test_nested_dict_converted(self) -> None:
        d = {"outer": {"inner": 3.14}}
        result = _to_dynamo(d)
        assert isinstance(result["outer"]["inner"], Decimal)

    def test_list_with_floats_converted(self) -> None:
        result = _to_dynamo([1.0, 2.0, "x"])
        assert isinstance(result[0], Decimal)
        assert isinstance(result[1], Decimal)
        assert result[2] == "x"

    def test_decimal_to_float(self) -> None:
        result = _from_dynamo(Decimal("1.23456"))
        assert isinstance(result, float)
        assert abs(result - 1.23456) < 1e-5

    def test_roundtrip(self) -> None:
        original = {"sharpe": 1.5, "consistency": 0.75, "trades": 42, "label": "ok"}
        result = _from_dynamo(_to_dynamo(original))
        assert abs(result["sharpe"] - 1.5) < 1e-6
        assert abs(result["consistency"] - 0.75) < 1e-6
        assert result["trades"] == 42
        assert result["label"] == "ok"

    def test_none_passthrough(self) -> None:
        assert _to_dynamo(None) is None
        assert _from_dynamo(None) is None

    def test_int_passthrough(self) -> None:
        assert _to_dynamo(42) == 42
        assert _from_dynamo(42) == 42


# ---------------------------------------------------------------------------
# rank_leaderboard
# ---------------------------------------------------------------------------

def _entry(
    sid: str,
    sharpe: float | None,
    consistency: float = 0.8,
    run_date: str = "2025-10-26",
) -> dict[str, Any]:
    return {
        "pk": sid,
        "sk": run_date,
        "avg_sharpe": sharpe,
        "consistency_score": consistency,
        "avg_net_return_pct": 10.0,
        "avg_max_drawdown_pct": 8.0,
        "avg_win_rate_pct": 55.0,
        "total_oos_trades": 30,
        "windows_count": 4,
        "validated": consistency >= 0.6 and (sharpe or 0) > 0.5,
    }


class TestRankLeaderboard:
    def test_sorted_by_sharpe_descending(self) -> None:
        entries = [
            _entry("s01", 0.9),
            _entry("s03", 1.5),
            _entry("s00", 0.3),
        ]
        ranked = rank_leaderboard(entries)
        assert ranked[0]["pk"] == "s03"
        assert ranked[1]["pk"] == "s01"
        assert ranked[2]["pk"] == "s00"

    def test_none_sharpe_last(self) -> None:
        entries = [
            _entry("s_null", None),
            _entry("s_pos", 1.2),
        ]
        ranked = rank_leaderboard(entries)
        assert ranked[0]["pk"] == "s_pos"
        assert ranked[-1]["pk"] == "s_null"

    def test_empty_list(self) -> None:
        assert rank_leaderboard([]) == []

    def test_single_entry(self) -> None:
        entries = [_entry("s01", 0.8)]
        ranked = rank_leaderboard(entries)
        assert len(ranked) == 1
        assert ranked[0]["pk"] == "s01"

    def test_preserves_all_fields(self) -> None:
        entries = [_entry("s01", 1.0)]
        ranked = rank_leaderboard(entries)
        assert "consistency_score" in ranked[0]
        assert "validated" in ranked[0]
        assert "windows_count" in ranked[0]

    def test_negative_sharpe_sorted_correctly(self) -> None:
        entries = [
            _entry("s_neg", -0.5),
            _entry("s_zero", 0.0),
            _entry("s_pos", 0.8),
        ]
        ranked = rank_leaderboard(entries)
        assert ranked[0]["pk"] == "s_pos"
        assert ranked[1]["pk"] == "s_zero"
        assert ranked[2]["pk"] == "s_neg"
