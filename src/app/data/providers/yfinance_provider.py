"""YFinance-backed market data provider for NSE equities."""
from __future__ import annotations

import logging
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

try:
    import yfinance as yf  # type: ignore[import-untyped]
except ImportError:  # pragma: no cover
    yf = None  # type: ignore[assignment]

from app.core.time_utils import IST
from app.data.providers.base import MarketDataProvider
from app.data.universe import NIFTY50_SYMBOLS
from app.models.candle import Candle, Timeframe
from app.models.instrument import Instrument

log = logging.getLogger(__name__)

UTC = ZoneInfo("UTC")

# NSE session times (IST)
_NSE_OPEN = time(9, 15)
_NSE_CLOSE = time(15, 30)

# yfinance interval strings for supported Timeframe values
_TF_TO_INTERVAL: dict[Timeframe, str] = {
    Timeframe.M1: "1m",
    Timeframe.M5: "5m",
    Timeframe.M15: "15m",
    Timeframe.M30: "30m",
    Timeframe.H1: "1h",
    Timeframe.D1: "1d",
}


def _symbol_to_instrument_id(symbol: str) -> str:
    """Strip exchange suffix to form a stable instrument_id."""
    return symbol.split(".")[0]


class YFinanceProvider(MarketDataProvider):
    """Real-data provider that fetches from Yahoo Finance via yfinance."""

    def get_instruments(self) -> list[Instrument]:
        """Return Instrument objects for the NIFTY 50 universe."""
        instruments: list[Instrument] = []
        for sym in NIFTY50_SYMBOLS:
            inst_id = _symbol_to_instrument_id(sym)
            instruments.append(
                Instrument(
                    instrument_id=inst_id,
                    trading_symbol=sym,
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
        """Fetch OHLCV bars from yfinance and return as Candle objects.

        Parameters
        ----------
        symbol:
            NSE symbol, e.g. ``"RELIANCE.NS"``.
        timeframe:
            Requested bar granularity.  Only ``D1`` is fully supported for
            long lookback windows; intraday intervals are subject to
            yfinance's rolling-window limits.
        start / end:
            Inclusive date range.
        """
        if yf is None:
            log.warning("yfinance is not installed; returning []")
            return []

        interval = _TF_TO_INTERVAL.get(timeframe)
        if interval is None:
            log.warning("Unsupported timeframe %s for yfinance; returning []", timeframe)
            return []

        try:
            df = yf.download(
                tickers=symbol,
                start=start.isoformat(),
                end=end.isoformat(),
                interval=interval,
                auto_adjust=True,
                progress=False,
                multi_level_column=False,
            )
        except Exception:
            log.warning("yfinance download failed for %s", symbol, exc_info=True)
            return []

        if df is None or df.empty:
            log.warning("yfinance returned no data for %s (%s – %s)", symbol, start, end)
            return []

        inst_id = _symbol_to_instrument_id(symbol)
        candles: list[Candle] = []

        for ts_raw, row in df.iterrows():
            # ts_raw may be a tz-aware UTC Timestamp or tz-naive
            if hasattr(ts_raw, "tzinfo") and ts_raw.tzinfo is not None:
                ts_utc = ts_raw.to_pydatetime().astimezone(UTC)
            else:
                ts_utc = ts_raw.to_pydatetime().replace(tzinfo=UTC)

            ts_ist = ts_utc.astimezone(IST)

            open_ = float(row["Open"])
            high_ = float(row["High"])
            low_ = float(row["Low"])
            close_ = float(row["Close"])
            volume_ = float(row["Volume"])

            # Skip rows with zero / NaN prices (delisted / bad data)
            if open_ <= 0 or high_ <= 0 or low_ <= 0 or close_ <= 0:
                log.debug("Skipping row with non-positive price for %s at %s", symbol, ts_ist)
                continue

            try:
                candle = Candle(
                    instrument_id=inst_id,
                    exchange="NSE",
                    trading_symbol=symbol,
                    timeframe=timeframe,
                    timestamp=ts_ist,
                    open=open_,
                    high=high_,
                    low=low_,
                    close=close_,
                    volume=volume_,
                    source="yfinance",
                )
                candles.append(candle)
            except Exception:
                log.debug(
                    "Skipping invalid candle for %s at %s", symbol, ts_ist, exc_info=True
                )

        return candles

    def get_live_quote(self, symbol: str) -> dict:
        """Return latest price snapshot for *symbol* using yfinance fast_info.

        Returns a dict with keys:
        ``symbol``, ``ltp``, ``open``, ``high``, ``low``, ``volume``,
        ``timestamp``.
        """
        try:
            if yf is None:
                return {}
            ticker = yf.Ticker(symbol)
            fi = ticker.fast_info
            ts_now = datetime.now(tz=IST)
            return {
                "symbol": symbol,
                "ltp": float(fi.last_price),
                "open": float(fi.open),
                "high": float(fi.day_high),
                "low": float(fi.day_low),
                "volume": float(fi.three_month_average_volume or 0),
                "timestamp": ts_now,
            }
        except Exception:
            log.warning("get_live_quote failed for %s", symbol, exc_info=True)
            return {}

    def get_market_status(self) -> dict:
        """Return whether NSE is currently open based on IST wall-clock time.

        NSE cash segment trades Mon–Fri 09:15–15:30 IST.
        Exchange holidays are *not* checked here; use a dedicated holiday
        calendar for production use.
        """
        now_ist = datetime.now(tz=IST)
        weekday = now_ist.weekday()  # 0=Mon … 4=Fri
        current_time = now_ist.time().replace(second=0, microsecond=0)
        is_open = (
            weekday < 5
            and _NSE_OPEN <= current_time < _NSE_CLOSE
        )
        return {
            "is_open": is_open,
            "exchange": "NSE",
            "timestamp": now_ist,
        }
