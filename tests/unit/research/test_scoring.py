"""Tests for the signal scoring engine."""
from __future__ import annotations

from app.research.regime import MarketRegime
from app.research.scoring import StrategyFamily, score_signal, rank_candidates


def test_max_score_strong_bull_momentum():
    score = score_signal(
        symbol="RELIANCE",
        strategy_id="s01_rs_momentum",
        family=StrategyFamily.MOMENTUM,
        regime=MarketRegime.STRONG_BULL,
        rs_percentile=95.0,
        sector_rank=2,
        relative_volume=2.5,
        families_triggered=["BREAKOUT"],
    )
    assert score.total == 100
    assert score.confluence_bonus == 10


def test_bear_regime_gives_zero_momentum_regime_score():
    score = score_signal(
        symbol="RELIANCE",
        strategy_id="s01_rs_momentum",
        family=StrategyFamily.MOMENTUM,
        regime=MarketRegime.BEAR,
        rs_percentile=85.0,
        sector_rank=1,
        relative_volume=2.0,
    )
    assert score.regime_score == 0


def test_low_rs_gives_zero_rs_score():
    score = score_signal(
        symbol="RELIANCE",
        strategy_id="s01_rs_momentum",
        family=StrategyFamily.MOMENTUM,
        regime=MarketRegime.BULL,
        rs_percentile=45.0,
        sector_rank=5,
        relative_volume=1.5,
    )
    assert score.rs_score == 0


def test_mean_reversion_preferred_in_sideways():
    score = score_signal(
        symbol="INFY",
        strategy_id="s_mean_rev",
        family=StrategyFamily.MEAN_REVERSION,
        regime=MarketRegime.SIDEWAYS,
        rs_percentile=70.0,
        sector_rank=3,
        relative_volume=1.8,
    )
    assert score.regime_score == 25


def test_rank_candidates_descending():
    scores = [
        score_signal("A", "s1", StrategyFamily.MOMENTUM, MarketRegime.BULL, 85.0, 2, 1.5),
        score_signal("B", "s1", StrategyFamily.MOMENTUM, MarketRegime.STRONG_BULL, 95.0, 1, 2.5),
        score_signal("C", "s1", StrategyFamily.MOMENTUM, MarketRegime.SIDEWAYS, 60.0, 10, 1.0),
    ]
    ranked = rank_candidates(scores)
    assert ranked[0].total >= ranked[1].total >= ranked[2].total


def test_confluence_bonus_only_for_different_family():
    score_with = score_signal(
        "X", "s1", StrategyFamily.MOMENTUM, MarketRegime.BULL,
        75.0, 4, 1.5, families_triggered=["BREAKOUT"]
    )
    score_without = score_signal(
        "X", "s1", StrategyFamily.MOMENTUM, MarketRegime.BULL,
        75.0, 4, 1.5, families_triggered=["MOMENTUM"]
    )
    assert score_with.confluence_bonus == 10
    assert score_without.confluence_bonus == 0


def test_sector_rank_none_gives_neutral_score():
    score = score_signal(
        "X", "s1", StrategyFamily.MOMENTUM, MarketRegime.BULL,
        80.0, None, 1.5
    )
    assert score.sector_score == 10


def test_total_is_bounded_0_to_100():
    for rs in (None, 10.0, 50.0, 95.0):
        for rv in (0.0, 1.0, 3.0):
            score = score_signal(
                "X", "s1", StrategyFamily.MOMENTUM, MarketRegime.BEAR, rs, 1, rv
            )
            assert 0 <= score.total <= 100
