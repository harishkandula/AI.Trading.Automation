"""Unit tests for GrowwBroker — all HTTP calls are mocked."""
from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_broker(token: str = "test-token") -> object:
    """Return a GrowwBroker with a stubbed auth manager."""
    from app.brokers.groww_broker import GrowwBroker

    mock_auth = MagicMock()
    mock_auth.get_token.return_value = token
    return GrowwBroker(auth=mock_auth)


def _mock_get_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


def _mock_post_response(payload: dict) -> MagicMock:
    resp = MagicMock()
    resp.json.return_value = payload
    resp.raise_for_status = MagicMock()
    return resp


# ---------------------------------------------------------------------------
# place_order – safety guard
# ---------------------------------------------------------------------------

class TestPlaceOrderSafety:
    def test_raises_when_live_orders_not_enabled(self):
        """place_order must raise ValueError when ENABLE_LIVE_ORDERS != 'true'."""
        broker = _make_broker()
        # Ensure env var is not set to 'true'.
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_LIVE_ORDERS"}
        env["ENABLE_LIVE_ORDERS"] = "false"
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ENABLE_LIVE_ORDERS"):
                broker.place_order("RELIANCE", 1, "BUY")

    def test_raises_when_env_var_missing_entirely(self):
        """Default (missing env var) must also block live orders."""
        broker = _make_broker()
        env = {k: v for k, v in os.environ.items() if k != "ENABLE_LIVE_ORDERS"}
        with patch.dict(os.environ, env, clear=True):
            with pytest.raises(ValueError, match="ENABLE_LIVE_ORDERS"):
                broker.place_order("TCS", 5, "SELL")

    def test_succeeds_when_live_orders_enabled(self):
        """When ENABLE_LIVE_ORDERS=true, the order API is called."""
        broker = _make_broker()
        post_resp = _mock_post_response(
            {"data": {"order_id": "ord-001", "status": "ACCEPTED"}}
        )
        with patch.dict(os.environ, {"ENABLE_LIVE_ORDERS": "true"}):
            with patch("app.brokers.groww_broker.httpx.post", return_value=post_resp):
                result = broker.place_order("RELIANCE", 10, "BUY")
        assert result["order_id"] == "ord-001"
        assert result["status"] == "ACCEPTED"


# ---------------------------------------------------------------------------
# get_holdings
# ---------------------------------------------------------------------------

class TestGetHoldings:
    def test_returns_correctly_mapped_list(self):
        raw_payload = {
            "data": [
                {
                    "trading_symbol": "RELIANCE",
                    "quantity": 5,
                    "average_price": 2400.0,
                    "ltp": 2450.0,
                    "pnl": 250.0,
                }
            ]
        }
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get",
            return_value=_mock_get_response(raw_payload),
        ):
            holdings = broker.get_holdings()

        assert len(holdings) == 1
        h = holdings[0]
        assert h["symbol"] == "RELIANCE"
        assert h["quantity"] == pytest.approx(5.0)
        assert h["avg_price"] == pytest.approx(2400.0)
        assert h["ltp"] == pytest.approx(2450.0)
        assert h["pnl"] == pytest.approx(250.0)

    def test_returns_empty_list_on_error(self):
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get", side_effect=Exception("network error")
        ):
            result = broker.get_holdings()
        assert result == []

    def test_handles_holdings_key(self):
        """Some Groww responses use 'holdings' instead of 'data'."""
        raw_payload = {
            "holdings": [
                {
                    "symbol": "TCS",
                    "quantity": 2,
                    "average_price": 3500.0,
                    "ltp": 3600.0,
                    "pnl": 200.0,
                }
            ]
        }
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get",
            return_value=_mock_get_response(raw_payload),
        ):
            holdings = broker.get_holdings()
        assert len(holdings) == 1
        assert holdings[0]["symbol"] == "TCS"


# ---------------------------------------------------------------------------
# get_positions
# ---------------------------------------------------------------------------

class TestGetPositions:
    def test_returns_correctly_mapped_list(self):
        raw_payload = {
            "data": [
                {
                    "trading_symbol": "INFY",
                    "quantity": 10,
                    "average_price": 1500.0,
                    "ltp": 1520.0,
                    "pnl": 200.0,
                    "product": "MIS",
                }
            ]
        }
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get",
            return_value=_mock_get_response(raw_payload),
        ):
            positions = broker.get_positions()

        assert len(positions) == 1
        pos = positions[0]
        assert pos["symbol"] == "INFY"
        assert pos["product"] == "MIS"

    def test_returns_empty_list_on_error(self):
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get", side_effect=RuntimeError("timeout")
        ):
            result = broker.get_positions()
        assert result == []


# ---------------------------------------------------------------------------
# get_order_status / cancel_order
# ---------------------------------------------------------------------------

class TestOrderStatus:
    def test_get_order_status_returns_dict(self):
        payload = {
            "data": {
                "status": "COMPLETE",
                "filled_quantity": 10,
                "average_price": 2450.0,
            }
        }
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get",
            return_value=_mock_get_response(payload),
        ):
            result = broker.get_order_status("ord-999")
        assert result["order_id"] == "ord-999"
        assert result["status"] == "COMPLETE"
        assert result["filled_qty"] == pytest.approx(10.0)

    def test_get_order_status_returns_unknown_on_error(self):
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.get", side_effect=Exception("fail")
        ):
            result = broker.get_order_status("ord-xyz")
        assert result["status"] == "UNKNOWN"

    def test_cancel_order_returns_status(self):
        payload = {"data": {"status": "CANCELLED"}}
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.delete",
            return_value=_mock_get_response(payload),
        ):
            result = broker.cancel_order("ord-123")
        assert result["order_id"] == "ord-123"
        assert result["status"] == "CANCELLED"

    def test_cancel_order_returns_cancel_failed_on_error(self):
        broker = _make_broker()
        with patch(
            "app.brokers.groww_broker.httpx.delete", side_effect=Exception("fail")
        ):
            result = broker.cancel_order("ord-abc")
        assert result["status"] == "CANCEL_FAILED"
