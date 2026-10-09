from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel


class BacktestConfig(BaseModel):
    strategy_id: str
    strategy_version: str
    universe: str
    start_date: date
    end_date: date
    initial_capital: float
    parameter_snapshot: dict[str, Any] = {}
    cost_profile: str = "default_estimate"
    slippage_pct: float = 0.001
    in_sample_end: date | None = None
    oos_start: date | None = None


class BacktestMetrics(BaseModel):
    run_id: str
    config: BacktestConfig

    starting_capital: float
    ending_capital: float
    gross_return_pct: float
    net_return_pct: float
    cagr_pct: float | None = None
    benchmark_return_pct: float | None = None
    excess_return_pct: float | None = None

    total_signals: int = 0
    eligible_trades: int = 0
    executed_trades: int = 0
    rejected_trades: int = 0
    winners: int = 0
    losers: int = 0
    breakeven_trades: int = 0
    win_rate_pct: float = 0.0
    avg_winner_pct: float = 0.0
    avg_loser_pct: float = 0.0
    payoff_ratio: float = 0.0
    expectancy_pct: float = 0.0
    profit_factor: float = 0.0

    max_drawdown_pct: float = 0.0
    max_drawdown_duration_sessions: int = 0
    sharpe_ratio: float | None = None
    sortino_ratio: float | None = None

    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    avg_holding_sessions: float = 0.0
    median_holding_sessions: float = 0.0
    exposure_pct: float = 0.0
    turnover: float = 0.0
    gross_costs: float = 0.0
    slippage_cost: float = 0.0
    ambiguous_bar_count: int = 0

    profitable_months_pct: float = 0.0
    performance_by_year: dict[str, float] = {}
    performance_by_symbol: dict[str, float] = {}

    survivorship_bias_warning: bool = True
    cost_model_note: str = "Estimated – verify current statutory rates"
    generated_at: datetime = datetime.utcnow()
