"""Paper trade journal — persists open/closed paper trades to DynamoDB.

Table: value of STATE_TABLE env var (e.g. ``market-ai-dev-state``).

DynamoDB schema
---------------
PK  : "PAPER_TRADE"           (string literal, same for every trade)
SK  : trade_id                (e.g. "PT-RELIANCE-20261028-3F2A1B0C")

Attributes
----------
symbol, strategy_id           strings
entry_price, stop_price,
  target_price, pnl           Number (Decimal in Python)
qty                           Number (int)
entry_date                    String  "YYYY-MM-DD"
exit_price, exit_date,
  exit_reason                 optional String / Number
status                        "OPEN" | "CLOSED"
holding_period_sessions       optional Number (int)
"""
from __future__ import annotations

import logging
import os
import uuid
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from zoneinfo import ZoneInfo

import boto3  # type: ignore[import-untyped]
from boto3.dynamodb.conditions import Attr, Key

from app.models.signal import Signal

log = logging.getLogger(__name__)

_PK = "PAPER_TRADE"
_IST = ZoneInfo("Asia/Kolkata")


def _today_ist() -> "date":  # noqa: F821
    from datetime import date as _date  # local import avoids re-export confusion
    return datetime.now(tz=_IST).date()


class PaperTradeJournal:
    """Persists paper trades to DynamoDB table `market-ai-dev-state`."""

    def __init__(self, table_name: str | None = None) -> None:
        self._table_name = table_name or os.environ.get("STATE_TABLE", "")
        self._table = None  # lazy init

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _get_table(self):
        if not self._table_name:
            raise RuntimeError(
                "STATE_TABLE env var not set — paper trading journal unavailable"
            )
        if self._table is None:
            self._table = boto3.resource("dynamodb").Table(self._table_name)
        return self._table

    def _put_item(self, item: dict) -> None:
        """Store item, omitting None values (DynamoDB rejects nulls in most ops)."""
        clean = {k: v for k, v in item.items() if v is not None}
        self._get_table().put_item(Item=clean)

    @staticmethod
    def _normalize(item: dict) -> dict:
        """Convert Decimal values to float for downstream consumption."""
        result: dict[str, Any] = {}
        for k, v in item.items():
            result[k] = float(v) if isinstance(v, Decimal) else v
        return result

    def _query_all(self, **kwargs: Any) -> list[dict]:
        """Issue a paginated DynamoDB Query and return all normalised items."""
        table = self._get_table()
        items: list[dict] = []
        resp = table.query(**kwargs)
        items.extend(resp.get("Items", []))
        while "LastEvaluatedKey" in resp:
            page_kwargs = dict(kwargs, ExclusiveStartKey=resp["LastEvaluatedKey"])
            resp = table.query(**page_kwargs)
            items.extend(resp.get("Items", []))
        return [self._normalize(i) for i in items]

    @staticmethod
    def _trade_id(symbol: str, entry_date: Any) -> str:  # entry_date: date
        short = uuid.uuid4().hex[:8].upper()
        date_str = entry_date.strftime("%Y%m%d")
        return f"PT-{symbol}-{date_str}-{short}"

    @staticmethod
    def _week_bounds() -> tuple[str, str]:
        today = datetime.now(tz=_IST).date()
        monday = today - timedelta(days=today.weekday())
        sunday = monday + timedelta(days=6)
        return monday.isoformat(), sunday.isoformat()

    @staticmethod
    def _dec(value: float) -> Decimal:
        return Decimal(str(round(value, 2)))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def open_trade(self, signal: Signal, entry_price: float, qty: int) -> str:
        """Record a new paper trade. Returns trade_id."""
        today = _today_ist()
        trade_id = self._trade_id(signal.symbol, today)

        item: dict[str, Any] = {
            "PK": _PK,
            "SK": trade_id,
            "symbol": signal.symbol,
            "strategy_id": signal.strategy_id,
            "entry_price": self._dec(entry_price),
            "stop_price": self._dec(signal.stop_price) if signal.stop_price else None,
            "target_price": (
                self._dec(signal.target_prices[0]) if signal.target_prices else None
            ),
            "qty": qty,
            "entry_date": today.isoformat(),
            "status": "OPEN",
            "holding_period_sessions": signal.holding_period_sessions,
        }
        self._put_item(item)
        log.debug("Opened paper trade %s", trade_id)
        return trade_id

    def close_trade(self, trade_id: str, exit_price: float, exit_reason: str) -> dict:
        """Mark a trade closed. Returns the updated trade dict with pnl."""
        table = self._get_table()
        resp = table.get_item(Key={"PK": _PK, "SK": trade_id})
        raw = resp.get("Item")
        if not raw:
            raise KeyError(f"Trade {trade_id} not found in journal")

        item = self._normalize(raw)
        if item.get("status") == "CLOSED":
            log.warning("Trade %s is already closed — skipping double-close", trade_id)
            return item

        entry_price = float(item["entry_price"])
        qty = int(item["qty"])
        pnl = round((exit_price - entry_price) * qty, 2)
        today_iso = _today_ist().isoformat()

        table.update_item(
            Key={"PK": _PK, "SK": trade_id},
            UpdateExpression=(
                "SET exit_price = :ep, exit_date = :ed, "
                "exit_reason = :er, pnl = :pnl, #st = :st"
            ),
            ExpressionAttributeNames={"#st": "status"},
            ExpressionAttributeValues={
                ":ep": self._dec(exit_price),
                ":ed": today_iso,
                ":er": exit_reason,
                ":pnl": self._dec(pnl),
                ":st": "CLOSED",
            },
        )
        item["exit_price"] = exit_price
        item["exit_date"] = today_iso
        item["exit_reason"] = exit_reason
        item["pnl"] = pnl
        item["status"] = "CLOSED"
        log.debug("Closed paper trade %s pnl=%.2f reason=%s", trade_id, pnl, exit_reason)
        return item

    def get_open_trades(self) -> list[dict]:
        """Return all open paper trades."""
        try:
            return self._query_all(
                KeyConditionExpression=Key("PK").eq(_PK),
                FilterExpression=Attr("status").eq("OPEN"),
            )
        except Exception:
            log.warning("Could not fetch open paper trades", exc_info=True)
            return []

    def get_closed_trades_today(self) -> list[dict]:
        """Return trades closed today (IST)."""
        try:
            today_iso = _today_ist().isoformat()
            return self._query_all(
                KeyConditionExpression=Key("PK").eq(_PK),
                FilterExpression=(
                    Attr("status").eq("CLOSED") & Attr("exit_date").eq(today_iso)
                ),
            )
        except Exception:
            log.warning("Could not fetch today's closed paper trades", exc_info=True)
            return []

    def get_closed_trades_this_week(self) -> list[dict]:
        """Return trades closed in the current calendar week (Mon-Sun, IST)."""
        try:
            mon_iso, sun_iso = self._week_bounds()
            return self._query_all(
                KeyConditionExpression=Key("PK").eq(_PK),
                FilterExpression=(
                    Attr("status").eq("CLOSED")
                    & Attr("exit_date").between(mon_iso, sun_iso)
                ),
            )
        except Exception:
            log.warning("Could not fetch this week's closed paper trades", exc_info=True)
            return []

    def get_pnl_today(self) -> float:
        """Sum of pnl for trades closed today. Negative = loss."""
        try:
            trades = self.get_closed_trades_today()
            return sum(float(t.get("pnl", 0.0)) for t in trades)
        except Exception:
            log.warning("Could not compute today's paper P&L", exc_info=True)
            return 0.0

    def get_pnl_week(self) -> float:
        """Sum of pnl for trades closed this calendar week."""
        try:
            trades = self.get_closed_trades_this_week()
            return sum(float(t.get("pnl", 0.0)) for t in trades)
        except Exception:
            log.warning("Could not compute this week's paper P&L", exc_info=True)
            return 0.0
