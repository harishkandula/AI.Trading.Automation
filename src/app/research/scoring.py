"""Signal Quality Engine — scores each signal candidate 0–100.

Score is used for RANKING candidates, not binary filtering.
The backtester will determine where useful score thresholds lie.

Components:
  Regime score      0–25  (how well does the regime suit this strategy family)
  RS score          0–25  (percentile vs NIFTY 500 universe)
  Sector score      0–20  (sector rank among all sectors)
  Setup quality     0–20  (volume expansion, pattern clarity)
  Confluence bonus  0–10  (signal from 2+ independent strategy families)
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.research.regime import MarketRegime

log = logging.getLogger(__name__)


class StrategyFamily(str, Enum):
    MOMENTUM = "MOMENTUM"
    BREAKOUT = "BREAKOUT"
    PULLBACK = "PULLBACK"
    MEAN_REVERSION = "MEAN_REVERSION"
    QUALITY = "QUALITY"


# Which regimes each family performs in
_FAMILY_PREFERRED_REGIMES: dict[StrategyFamily, set[MarketRegime]] = {
    StrategyFamily.MOMENTUM: {MarketRegime.STRONG_BULL, MarketRegime.BULL},
    StrategyFamily.BREAKOUT: {MarketRegime.STRONG_BULL, MarketRegime.BULL},
    StrategyFamily.PULLBACK: {MarketRegime.STRONG_BULL, MarketRegime.BULL},
    StrategyFamily.MEAN_REVERSION: {MarketRegime.SIDEWAYS, MarketRegime.CORRECTION},
    StrategyFamily.QUALITY: {
        MarketRegime.STRONG_BULL, MarketRegime.BULL,
        MarketRegime.SIDEWAYS, MarketRegime.RECOVERY,
    },
}

_FAMILY_ACCEPTABLE_REGIMES: dict[StrategyFamily, set[MarketRegime]] = {
    StrategyFamily.MOMENTUM: {MarketRegime.RECOVERY, MarketRegime.SIDEWAYS},
    StrategyFamily.BREAKOUT: {MarketRegime.RECOVERY},
    StrategyFamily.PULLBACK: {MarketRegime.SIDEWAYS},
    StrategyFamily.MEAN_REVERSION: {MarketRegime.BULL},
    StrategyFamily.QUALITY: {MarketRegime.CORRECTION},
}


@dataclass
class SignalScore:
    symbol: str
    strategy_id: str
    family: StrategyFamily
    total: int
    regime_score: int
    rs_score: int
    sector_score: int
    setup_score: int
    confluence_bonus: int
    regime: MarketRegime
    rs_percentile: float | None
    sector_rank: int | None
    relative_volume: float
    families_triggered: list[str]
    notes: list[str]


def score_signal(
    symbol: str,
    strategy_id: str,
    family: StrategyFamily,
    regime: MarketRegime,
    rs_percentile: float | None,
    sector_rank: int | None,
    relative_volume: float,
    families_triggered: list[str] | None = None,
) -> SignalScore:
    """Compute a 0–100 quality score for a signal candidate.

    Parameters
    ----------
    symbol : NSE symbol
    strategy_id : e.g. "s01_rs_momentum"
    family : StrategyFamily enum
    regime : current MarketRegime
    rs_percentile : stock's RS percentile vs NIFTY 500 (0–100), or None
    sector_rank : sector rank among all sectors (1=strongest), or None
    relative_volume : today's volume / 20-day average volume
    families_triggered : list of family names that also triggered for this symbol
    """
    notes: list[str] = []
    families_triggered = families_triggered or []

    # ── Regime score (0–25) ──────────────────────────────────────────────────
    preferred = _FAMILY_PREFERRED_REGIMES.get(family, set())
    acceptable = _FAMILY_ACCEPTABLE_REGIMES.get(family, set())
    if regime in preferred:
        regime_score = 25
    elif regime in acceptable:
        regime_score = 12
    elif regime in (MarketRegime.BEAR, MarketRegime.UNKNOWN):
        regime_score = 0
        notes.append(f"Regime {regime.value} hostile for {family.value}")
    else:
        regime_score = 5

    # ── RS score (0–25) ──────────────────────────────────────────────────────
    if rs_percentile is None:
        rs_score = 0
        notes.append("RS percentile unavailable")
    elif rs_percentile >= 90:
        rs_score = 25
    elif rs_percentile >= 80:
        rs_score = 20
    elif rs_percentile >= 70:
        rs_score = 15
    elif rs_percentile >= 60:
        rs_score = 8
    else:
        rs_score = 0
        notes.append(f"RS percentile {rs_percentile:.0f} below 60")

    # ── Sector score (0–20) ──────────────────────────────────────────────────
    if sector_rank is None:
        sector_score = 10  # neutral if data unavailable
    elif sector_rank <= 3:
        sector_score = 20
    elif sector_rank <= 7:
        sector_score = 15
    elif sector_rank <= 15:
        sector_score = 8
    else:
        sector_score = 0

    # ── Setup quality score (0–20) ───────────────────────────────────────────
    if relative_volume >= 2.0:
        setup_score = 20
    elif relative_volume >= 1.5:
        setup_score = 15
    elif relative_volume >= 1.2:
        setup_score = 10
    elif relative_volume >= 0.8:
        setup_score = 5
    else:
        setup_score = 0
        notes.append(f"Low relative volume {relative_volume:.2f}x")

    # ── Confluence bonus (0–10) ──────────────────────────────────────────────
    own_family = family.value
    other_families = {f for f in families_triggered if f != own_family}
    confluence_bonus = 10 if other_families else 0
    if other_families:
        notes.append(f"Multi-family: {own_family} + {', '.join(other_families)}")

    total = regime_score + rs_score + sector_score + setup_score + confluence_bonus

    return SignalScore(
        symbol=symbol,
        strategy_id=strategy_id,
        family=family,
        total=total,
        regime_score=regime_score,
        rs_score=rs_score,
        sector_score=sector_score,
        setup_score=setup_score,
        confluence_bonus=confluence_bonus,
        regime=regime,
        rs_percentile=rs_percentile,
        sector_rank=sector_rank,
        relative_volume=relative_volume,
        families_triggered=families_triggered,
        notes=notes,
    )


def rank_candidates(scores: list[SignalScore]) -> list[SignalScore]:
    """Return candidates sorted by total score descending."""
    return sorted(scores, key=lambda s: s.total, reverse=True)
