import pytest
from market_ai.providers.mock import MockBrokerProvider
from market_ai.providers.base import DisabledTradingError
from market_ai.services.scoring import swing_candidate, compounder_candidate

def test_swing_score_range():
    bars = MockBrokerProvider().daily_bars()["DEMOALPHA"]
    result = swing_candidate("DEMOALPHA", bars)
    assert 0 <= result.score <= 100

def test_live_orders_disabled():
    with pytest.raises(DisabledTradingError):
        MockBrokerProvider().place_order({"symbol": "DEMOALPHA"})

def test_compounder_requires_data():
    with pytest.raises(ValueError):
        compounder_candidate("X", {}, "2026-01-01")
