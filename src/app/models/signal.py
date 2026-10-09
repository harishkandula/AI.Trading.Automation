from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Any

from pydantic import BaseModel


class SignalStatus(str, Enum):
    DETECTED = "DETECTED"
    ELIGIBLE = "ELIGIBLE"
    REJECTED = "REJECTED"
    PAPER_OPEN = "PAPER_OPEN"
    PARTIALLY_EXITED = "PARTIALLY_EXITED"
    CLOSED = "CLOSED"
    EXPIRED = "EXPIRED"
    INVALID_DATA = "INVALID_DATA"


class Signal(BaseModel):
    signal_id: str
    strategy_id: str
    strategy_version: str
    symbol: str
    exchange: str
    timeframe: str
    signal_timestamp: datetime
    decision_timestamp: datetime
    planned_entry_type: str = "NEXT_BAR_OPEN"
    planned_entry_timestamp: datetime | None = None
    entry_reference_price: float | None = None
    stop_price: float | None = None
    target_prices: list[float] = []
    holding_period_sessions: int | None = None
    indicators: dict[str, Any] = {}
    qualification_reasons: list[str] = []
    risk_warnings: list[str] = []
    data_quality_flags: list[str] = []
    parameter_snapshot: dict[str, Any] = {}
    generation_timestamp: datetime = datetime.utcnow()
    status: SignalStatus = SignalStatus.DETECTED
    rejection_reason: str = ""
