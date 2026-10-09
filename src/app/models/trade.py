from __future__ import annotations

from datetime import date, datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class TradeStatus(str, Enum):
    PENDING_ENTRY = "PENDING_ENTRY"
    OPEN = "OPEN"
    PARTIALLY_CLOSED = "PARTIALLY_CLOSED"
    CLOSED_TARGET = "CLOSED_TARGET"
    CLOSED_STOP = "CLOSED_STOP"
    CLOSED_TIME = "CLOSED_TIME"
    CLOSED_EOD = "CLOSED_EOD"
    CANCELLED = "CANCELLED"
    DATA_ERROR = "DATA_ERROR"


class TradeEvent(BaseModel):
    event_type: str
    timestamp: datetime
    quantity: float
    price: float
    cost: float = 0.0
    notes: str = ""


class Trade(BaseModel):
    trade_id: str
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    exchange: str
    direction: str = "LONG"

    entry_timestamp: datetime | None = None
    entry_price: float | None = None
    entry_quantity: float = 0.0
    entry_cost: float = 0.0

    stop_price: float | None = None
    target_prices: list[float] = []
    holding_period_sessions: int | None = None
    exit_deadline: date | None = None

    realized_pnl: float = 0.0
    remaining_quantity: float = 0.0
    status: TradeStatus = TradeStatus.PENDING_ENTRY
    events: list[TradeEvent] = []
    parameter_snapshot: dict[str, Any] = {}
    rejection_reason: str = ""

    @property
    def is_open(self) -> bool:
        return self.status in (TradeStatus.OPEN, TradeStatus.PARTIALLY_CLOSED)
