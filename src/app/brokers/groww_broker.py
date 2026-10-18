"""Groww broker integration — holdings, positions, and order management."""
from __future__ import annotations

import logging
import os

import httpx

from app.auth.groww_auth import GrowwAuthManager

log = logging.getLogger(__name__)

_GROWW_BASE = "https://api.groww.in/v1"


class GrowwBroker:
    """Thin wrapper around the Groww Trade REST API for portfolio and order ops."""

    def __init__(self, auth: GrowwAuthManager | None = None) -> None:
        self._auth = auth or GrowwAuthManager()

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _headers(self) -> dict[str, str]:
        token = self._auth.get_token()
        return {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    def _get(self, path: str, **params: object) -> dict:
        url = f"{_GROWW_BASE}{path}"
        resp = httpx.get(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, payload: dict) -> dict:
        url = f"{_GROWW_BASE}{path}"
        resp = httpx.post(url, headers=self._headers(), json=payload, timeout=15)
        resp.raise_for_status()
        return resp.json()

    def _delete(self, path: str, **params: object) -> dict:
        url = f"{_GROWW_BASE}{path}"
        resp = httpx.delete(url, headers=self._headers(), params=params, timeout=15)
        resp.raise_for_status()
        return resp.json()

    # ------------------------------------------------------------------
    # Portfolio
    # ------------------------------------------------------------------

    def get_holdings(self) -> list[dict]:
        """Return equity holdings as a list of dicts.

        Each item has keys: ``symbol``, ``quantity``, ``avg_price``,
        ``ltp``, ``pnl``.
        """
        try:
            data = self._get("/portfolio/holdings")
            raw = data.get("data") or data.get("holdings") or []
            holdings: list[dict] = []
            for item in raw:
                holdings.append(
                    {
                        "symbol": item.get("trading_symbol") or item.get("symbol", ""),
                        "quantity": float(item.get("quantity", 0)),
                        "avg_price": float(item.get("average_price", 0)),
                        "ltp": float(item.get("ltp", 0)),
                        "pnl": float(item.get("pnl", 0)),
                    }
                )
            return holdings
        except Exception:
            log.warning("get_holdings failed", exc_info=True)
            return []

    def get_positions(self) -> list[dict]:
        """Return intraday/positional holdings as a list of dicts.

        Each item has keys: ``symbol``, ``quantity``, ``avg_price``,
        ``ltp``, ``pnl``, ``product``.
        """
        try:
            data = self._get("/portfolio/positions")
            raw = data.get("data") or data.get("positions") or []
            positions: list[dict] = []
            for item in raw:
                positions.append(
                    {
                        "symbol": item.get("trading_symbol") or item.get("symbol", ""),
                        "quantity": float(item.get("quantity", 0)),
                        "avg_price": float(item.get("average_price", 0)),
                        "ltp": float(item.get("ltp", 0)),
                        "pnl": float(item.get("pnl", 0)),
                        "product": item.get("product", ""),
                    }
                )
            return positions
        except Exception:
            log.warning("get_positions failed", exc_info=True)
            return []

    # ------------------------------------------------------------------
    # Order management
    # ------------------------------------------------------------------

    def place_order(
        self,
        symbol: str,
        qty: int,
        side: str,
        order_type: str = "MARKET",
        price: float | None = None,
    ) -> dict:
        """Place an equity order on Groww.

        Parameters
        ----------
        symbol:
            NSE trading symbol (e.g. ``"RELIANCE"``).
        qty:
            Number of shares.
        side:
            ``"BUY"`` or ``"SELL"``.
        order_type:
            ``"MARKET"`` (default) or ``"LIMIT"``.
        price:
            Limit price — required when *order_type* is ``"LIMIT"``.

        Returns
        -------
        dict
            Contains at minimum ``order_id`` and ``status``.

        Raises
        ------
        ValueError
            If ``ENABLE_LIVE_ORDERS`` environment variable is not ``"true"``.
        """
        if os.environ.get("ENABLE_LIVE_ORDERS", "false").lower() != "true":
            raise ValueError(
                "Live order placement is disabled. "
                "Set ENABLE_LIVE_ORDERS=true to enable."
            )

        payload: dict = {
            "trading_symbol": symbol,
            "exchange": "NSE",
            "transaction_type": side.upper(),
            "order_type": order_type.upper(),
            "quantity": qty,
            "product": "CNC",
        }
        if order_type.upper() == "LIMIT" and price is not None:
            payload["price"] = price

        try:
            data = self._post("/orders", payload)
            order_data = data.get("data") or data
            return {
                "order_id": order_data.get("order_id") or order_data.get("orderId", ""),
                "status": order_data.get("status", ""),
            }
        except Exception:
            log.error("place_order failed for %s %s %s", side, qty, symbol, exc_info=True)
            raise

    def get_order_status(self, order_id: str) -> dict:
        """Return the current status of an order by *order_id*."""
        try:
            data = self._get(f"/orders/{order_id}")
            order_data = data.get("data") or data
            return {
                "order_id": order_id,
                "status": order_data.get("status", ""),
                "filled_qty": float(order_data.get("filled_quantity", 0)),
                "avg_price": float(order_data.get("average_price", 0)),
            }
        except Exception:
            log.warning("get_order_status failed for %s", order_id, exc_info=True)
            return {"order_id": order_id, "status": "UNKNOWN"}

    def cancel_order(self, order_id: str) -> dict:
        """Cancel an open order by *order_id*."""
        try:
            data = self._delete(f"/orders/{order_id}")
            result = data.get("data") or data
            return {
                "order_id": order_id,
                "status": result.get("status", "CANCELLED"),
            }
        except Exception:
            log.warning("cancel_order failed for %s", order_id, exc_info=True)
            return {"order_id": order_id, "status": "CANCEL_FAILED"}
