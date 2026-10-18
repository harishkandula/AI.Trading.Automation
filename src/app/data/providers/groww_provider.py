"""Groww-backed market data provider for NSE equities."""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from app.auth.groww_auth import GrowwAuthManager
from app.core.time_utils import IST
from app.data.providers.base import MarketDataProvider
from app.data.universe import NIFTY50_SYMBOLS
from app.models.candle import Candle, Timeframe
from app.models.instrument import Instrument

log = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

_GROWW_BASE = "https://api.groww.in/v1"

_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)

# Map Timeframe → Groww interval string (best-effort; adjust to docs)
_TF_TO_GROWW: dict[Timeframe, str] = {
    Timeframe.M1: "1",
    Timeframe.M5: "5",
    Timeframe.M15: "15",
    Timeframe.M30: "30",
    Timeframe.H1: "60",
    Timeframe.D1: "1D",
}


def _strip_ns(symbol: str) -> str:
    """Return the bare NSE symbol without the .NS yfinance suffix."""
    return symbol.removesuffix(".NS")


class GrowwDataProvider(MarketDataProvider):
    """Market data provider backed by the Groww Trade API."""

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

    # ------------------------------------------------------------------
    # MarketDataProvider interface
    # ------------------------------------------------------------------

    def get_instruments(self) -> list[Instrument]:
        """Return Instrument objects for the NIFTY 50 universe."""
        instruments: list[Instrument] = []
        for sym in NIFTY50_SYMBOLS:
            bare = _strip_ns(sym)
            instruments.append(
                Instrument(
                    instrument_id=bare,
                    trading_symbol=bare,
                    exchange="NSE",
                    segment="CASH",
                    instrument_type="EQ",
                    tradable=True,
                )
            )
        return instruments

    def get_historical_candles(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Fetch OHLCV bars from Groww historical API.

        Falls back to yfinance on any Groww error.
        """
        bare = _strip_ns(symbol)
        interval = _TF_TO_GROWW.get(timeframe)
        if interval is None:
            log.warning("Unsupported timeframe %s for Groww; returning []", timeframe)
            return []

        try:
            data = self._get(
                "/historical-data/candle",
                exchange="NSE",
                trading_symbol=bare,
                interval=interval,
                start_time=start.isoformat(),
                end_time=end.isoformat(),
            )
            raw_candles: list[dict] = (
                data.get("data") or data.get("candles") or []
            )
        except Exception:
            log.warning(
                "Groww historical-data failed for %s — falling back to yfinance",
                symbol,
                exc_info=True,
            )
            return self._yfinance_fallback(symbol, timeframe, start, end)

        if not raw_candles:
            log.warning("Groww returned no candles for %s (%s – %s)", symbol, start, end)
            return []

        candles: list[Candle] = []
        for raw in raw_candles:
            try:
                candle = self._parse_candle(raw, bare, timeframe)
                if candle is not None:
                    candles.append(candle)
            except Exception:
                log.debug("Skipping malformed candle for %s: %s", symbol, raw, exc_info=True)
        return candles

    def get_live_quote(self, symbol: str) -> dict:
        """Return latest price snapshot for *symbol* from Groww LTP API."""
        bare = _strip_ns(symbol)
        try:
            data = self._get("/market-data/ltp", exchange="NSE", trading_symbol=bare)
            payload = data.get("data") or data
            return {
                "symbol": symbol,
                "ltp": float(payload.get("ltp", 0)),
                "open": float(payload.get("open", 0)),
                "high": float(payload.get("high", 0)),
                "low": float(payload.get("low", 0)),
                "volume": float(payload.get("volume", 0)),
                "timestamp": datetime.now(tz=IST),
            }
        except Exception:
            log.warning("get_live_quote failed for %s", symbol, exc_info=True)
            return {}

    def get_market_status(self) -> dict:
        """Return whether NSE is currently open based on IST wall-clock time."""
        now_ist = datetime.now(tz=IST)
        weekday = now_ist.weekday()  # 0=Mon … 4=Fri
        current_time = now_ist.time().replace(second=0, microsecond=0)
        is_open = weekday < 5 and _NSE_OPEN <= current_time < _NSE_CLOSE
        return {
            "is_open": is_open,
            "exchange": "NSE",
            "timestamp": now_ist,
        }

    # ------------------------------------------------------------------
    # Private conversion helpers
    # ------------------------------------------------------------------

    def _parse_candle(
        self, raw: dict, bare_symbol: str, timeframe: Timeframe
    ) -> Candle | None:
        """Convert a raw Groww candle dict to a Candle model."""
        # Groww returns epoch ms or ISO string; handle both.
        ts_raw = raw.get("timestamp") or raw.get("time")
        if ts_raw is None:
            return None

        if isinstance(ts_raw, (int, float)):
            # Epoch milliseconds
            ts_utc = datetime.fromtimestamp(ts_raw / 1000.0, tz=UTC)
        else:
            ts_utc = datetime.fromisoformat(str(ts_raw)).astimezone(UTC)

        open_ = float(raw.get("open", 0))
        high_ = float(raw.get("high", 0))
        lo_ = float(raw.get("low", 0))
        close_ = float(raw.get("close", 0))
        volume_ = float(raw.get("volume", 0))

        if open_ <= 0 or high_ <= 0 or lo_ <= 0 or close_ <= 0:
            return None

        return Candle(
            instrument_id=bare_symbol,
            exchange="NSE",
            trading_symbol=bare_symbol,
            timeframe=timeframe,
            timestamp=ts_utc.astimezone(IST),
            open=open_,
            high=high_,
            low=lo_,
            close=close_,
            volume=volume_,
            source="groww",
        )

    def _yfinance_fallback(
        self,
        symbol: str,
        timeframe: Timeframe,
        start: date,
        end: date,
    ) -> list[Candle]:
        """Attempt a yfinance fetch as a fallback when Groww is unavailable."""
        try:
            from app.data.providers.yfinance_provider import YFinanceProvider

            # yfinance expects SYMBOL.NS format
            yf_sym = symbol if symbol.endswith(".NS") else f"{symbol}.NS"
            return YFinanceProvider().get_historical_candles(yf_sym, timeframe, start, end)
        except Exception:
            log.warning("yfinance fallback also failed for %s", symbol, exc_info=True)
            return []
