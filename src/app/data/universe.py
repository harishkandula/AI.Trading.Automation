"""Universe definitions — NIFTY 50, NIFTY Next 50, and helper utilities."""
from __future__ import annotations

# ---------------------------------------------------------------------------
# NIFTY 50 — current constituents (NSE symbols with .NS suffix for yfinance)
# ---------------------------------------------------------------------------
NIFTY50_SYMBOLS: list[str] = [
    "ADANIENT.NS",
    "ADANIPORTS.NS",
    "APOLLOHOSP.NS",
    "ASIANPAINT.NS",
    "AXISBANK.NS",
    "BAJAJ-AUTO.NS",
    "BAJAJFINSV.NS",
    "BAJFINANCE.NS",
    "BHARTIARTL.NS",
    "BPCL.NS",
    "BRITANNIA.NS",
    "CIPLA.NS",
    "COALINDIA.NS",
    "DIVISLAB.NS",
    "DRREDDY.NS",
    "EICHERMOT.NS",
    "GRASIM.NS",
    "HCLTECH.NS",
    "HDFCBANK.NS",
    "HDFCLIFE.NS",
    "HEROMOTOCO.NS",
    "HINDALCO.NS",
    "HINDUNILVR.NS",
    "ICICIBANK.NS",
    "INDUSINDBK.NS",
    "INFY.NS",
    "ITC.NS",
    "JSWSTEEL.NS",
    "KOTAKBANK.NS",
    "LT.NS",
    "M&M.NS",  # yfinance accepts M&M.NS directly
    "MARUTI.NS",
    "NESTLEIND.NS",
    "NTPC.NS",
    "ONGC.NS",
    "POWERGRID.NS",
    "RELIANCE.NS",
    "SBILIFE.NS",
    "SBIN.NS",
    "SHRIRAMFIN.NS",
    "SUNPHARMA.NS",
    "TATACONSUM.NS",
    "TATASTEEL.NS",
    "TCS.NS",
    "TECHM.NS",
    "TITAN.NS",
    "ULTRACEMCO.NS",
    "WIPRO.NS",
]

# ---------------------------------------------------------------------------
# NIFTY Next 50 — current constituents
# ---------------------------------------------------------------------------
NIFTY_NEXT50_SYMBOLS: list[str] = [
    "ABB.NS",
    "ADANIGREEN.NS",
    "ADANITRANS.NS",
    "AMBUJACEM.NS",
    "AUROPHARMA.NS",
    "BAJAJHLDNG.NS",
    "BANKBARODA.NS",
    "BERGEPAINT.NS",
    "BEL.NS",
    "BOSCHLTD.NS",
    "CANBK.NS",
    "CHOLAFIN.NS",
    "COLPAL.NS",
    "DABUR.NS",
    "DLF.NS",
    "GAIL.NS",
    "GODREJCP.NS",
    "HAVELLS.NS",
    "HAL.NS",
    "ICICIPRULI.NS",
    "ICICIGI.NS",
    "INDIGO.NS",
    "IOC.NS",
    "IRCTC.NS",
    "JINDALSTEL.NS",
    "LICI.NS",
    "LUPIN.NS",
    "MARICO.NS",
    "MCDOWELL-N.NS",
    "MOTHERSON.NS",
    "MUTHOOTFIN.NS",
    "NAUKRI.NS",
    "NHPC.NS",
    "NMDC.NS",
    "OBEROIRLTY.NS",
    "OFSS.NS",
    "PAGEIND.NS",
    "PIDILITIND.NS",
    "PNB.NS",
    "RECLTD.NS",
    "SAIL.NS",
    "SIEMENS.NS",
    "SRF.NS",
    "TATAPOWER.NS",
    "TORNTPHARM.NS",
    "TRENT.NS",
    "VEDL.NS",
    "VOLTAS.NS",
    "ZYDUSLIFE.NS",
    "UPL.NS",
]

# ---------------------------------------------------------------------------
# Mock / demo symbols used in tests (no .NS suffix — synthetic)
# ---------------------------------------------------------------------------
_MOCK_SYMBOLS: list[str] = ["DEMOALPHA", "DEMOBETA", "DEMOGAMMA"]

_UNIVERSES: dict[str, list[str]] = {
    "nifty50": NIFTY50_SYMBOLS,
    "nifty_next50": NIFTY_NEXT50_SYMBOLS,
    "mock": _MOCK_SYMBOLS,
}


def get_universe(name: str) -> list[str]:
    """Return the symbol list for a named universe.

    Supported names: ``"nifty50"``, ``"nifty_next50"``, ``"mock"``.

    Raises
    ------
    ValueError
        If *name* is not a recognised universe identifier.
    """
    key = name.lower()
    if key not in _UNIVERSES:
        raise ValueError(
            f"Unknown universe {name!r}. "
            f"Valid choices: {sorted(_UNIVERSES)}"
        )
    return list(_UNIVERSES[key])
