"""
Sync NIFTY 500 constituent list from NSE and update nifty500_symbols.py.

Usage:
    python -m scripts.sync_nifty500

What it does:
1. Downloads the current NIFTY 500 constituent CSV from NSE index website
2. Compares against current symbols in nifty500_symbols.py
3. Prints additions and removals
4. Updates the LARGE_CAP, MID_CAP, SMALL_CAP lists in nifty500_symbols.py
5. Does NOT auto-commit — prints a summary for manual review first

The output staging file (scripts/nifty500_sync_output.py) contains the three
updated list definitions. Copy-paste the changed lists into
src/app/data/nifty500_symbols.py after reviewing.

Run this script after each semi-annual NSE index rebalance (March, September).
"""
from __future__ import annotations

import importlib.util
import logging
import sys
import time
from datetime import date
from pathlib import Path

log = logging.getLogger(__name__)

# ── Path setup ──────────────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent
_SRC = _REPO_ROOT / "src"
_NIFTY_SRC = _SRC / "app" / "data" / "nifty500_symbols.py"
_OUTPUT_FILE = Path(__file__).parent / "nifty500_sync_output.py"

_NSE_INDEX_URL = "https://www1.nseindia.com/content/indices/ind_nifty500list.csv"

# Headers that mimic a real browser — same as nse_bhavcopy.py
_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Referer": "https://www.nseindia.com/",
    "Connection": "keep-alive",
}

# NSE Industry column → our sector taxonomy
_INDUSTRY_TO_SECTOR: dict[str, str] = {
    "FINANCIAL SERVICES": "FinancialServices",
    "BANKS": "Banking",
    "BANKING": "Banking",
    "IT": "IT",
    "INFORMATION TECHNOLOGY": "IT",
    "PHARMACEUTICALS": "Pharma",
    "PHARMA": "Pharma",
    "CONSUMER GOODS": "FMCG",
    "FMCG": "FMCG",
    "OIL & GAS": "Energy",
    "OIL AND GAS": "Energy",
    "ENERGY": "Energy",
    "METALS": "Metals",
    "METALS & MINING": "Metals",
    "STEEL": "Metals",
    "CEMENT": "Cement",
    "AUTOMOBILES": "Auto",
    "AUTO": "Auto",
    "AUTOMOBILE": "Auto",
    "CAPITAL GOODS": "CapitalGoods",
    "INDUSTRIAL MANUFACTURING": "CapitalGoods",
    "CONSTRUCTION": "CapitalGoods",
    "INFRASTRUCTURE": "CapitalGoods",
    "CHEMICALS": "Chemicals",
    "TELECOM": "Telecom",
    "TELECOMMUNICATION": "Telecom",
    "POWER": "Power",
    "REALTY": "RealEstate",
    "REAL ESTATE": "RealEstate",
    "INSURANCE": "Insurance",
    "MEDIA & ENTERTAINMENT": "Media",
    "MEDIA": "Media",
    "TEXTILES": "Textiles",
    "TEXTILE": "Textiles",
    "AGRO CHEMICALS": "Agro",
    "AGRICULTURAL": "Agro",
    "AGRICULTURE": "Agro",
    "CONSUMER DURABLES": "ConsumerElectronics",
    "HEALTHCARE": "Healthcare",
    "HOSPITAL & DIAGNOSTIC": "Healthcare",
    "HOSPITALITY": "Hospitality",
    "HOTELS": "Hospitality",
    "LOGISTICS": "Logistics",
    "TRANSPORT": "Logistics",
    "MINING": "Mining",
    "DEFENCE": "Defence",
    "RETAILING": "Retail",
    "RETAIL": "Retail",
    "SERVICES": "Services",
    "DIVERSIFIED": "Diversified",
    "PAPER": "Packaging",
    "PACKAGING": "Packaging",
    "PAINTS": "Paints",
    "NBFC": "NBFC",
    "CONSUMER TECHNOLOGY": "ConsumerTech",
    "INTERNET": "ConsumerTech",
    "AVIATION": "Aviation",
    "TRAVEL": "Travel",
}


def _map_industry(industry: str) -> str:
    """Map NSE Industry string to our sector tag. Returns 'Unknown' on no match."""
    return _INDUSTRY_TO_SECTOR.get(industry.strip().upper(), "Unknown")


# ── NSE fetch ────────────────────────────────────────────────────────────────

def _acquire_nse_cookies() -> dict[str, str]:
    """Visit NSE homepage to acquire session cookies (required for index CSV download)."""
    try:
        import httpx
        with httpx.Client(headers=_HEADERS, timeout=15, follow_redirects=True) as client:
            resp = client.get("https://www.nseindia.com")
            cookies = dict(resp.cookies)
            log.debug("NSE session cookies: %s", list(cookies.keys()))
            return cookies
    except Exception as exc:
        log.warning("Failed to acquire NSE cookies: %s", exc)
        return {}


def fetch_nifty500_csv() -> bytes:
    """Download the NIFTY 500 constituent CSV from NSE. Returns raw CSV bytes."""
    try:
        import httpx
    except ImportError:
        sys.exit(
            "ERROR: httpx is required. Install with: pip install httpx\n"
            "       (It should already be in your project venv.)"
        )

    print(f"Acquiring NSE session cookies from https://www.nseindia.com ...")
    cookies = _acquire_nse_cookies()
    time.sleep(1)  # polite pause between NSE requests

    print(f"Fetching NIFTY 500 constituents from {_NSE_INDEX_URL} ...")
    with httpx.Client(
        headers=_HEADERS,
        cookies=cookies,
        timeout=30,
        follow_redirects=True,
    ) as client:
        resp = client.get(_NSE_INDEX_URL)
        resp.raise_for_status()
        if len(resp.content) < 1000:
            raise ValueError(
                f"Response suspiciously small ({len(resp.content)} bytes). "
                "NSE may have blocked the request — try again from a non-AWS IP."
            )
        return resp.content


def parse_nse_csv(content: bytes) -> dict[str, str]:
    """Parse the NIFTY 500 CSV. Returns {symbol: industry} for Series=EQ rows."""
    try:
        import pandas as pd
    except ImportError:
        sys.exit("ERROR: pandas is required. Install with: pip install pandas")

    import io
    df = pd.read_csv(io.BytesIO(content))
    df.columns = [c.strip() for c in df.columns]

    # Expected columns: Company Name, Industry, Symbol, Series, ISIN Code
    required = {"Symbol", "Series"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(
            f"NIFTY 500 CSV is missing expected columns: {missing}\n"
            f"Available columns: {list(df.columns)}"
        )

    # Filter EQ series only
    df = df[df["Series"].astype(str).str.strip() == "EQ"].copy()
    df["Symbol"] = df["Symbol"].astype(str).str.strip()

    industry_col = "Industry" if "Industry" in df.columns else None
    result: dict[str, str] = {}
    for _, row in df.iterrows():
        sym = row["Symbol"]
        industry = row[industry_col].strip() if industry_col else "Unknown"
        result[sym] = industry

    return result


# ── Load current symbols ─────────────────────────────────────────────────────

def load_current_module():
    """Load nifty500_symbols.py via importlib and return the module."""
    spec = importlib.util.spec_from_file_location("nifty500_symbols", _NIFTY_SRC)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot load {_NIFTY_SRC}")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return mod


# ── Diff logic ───────────────────────────────────────────────────────────────

def compute_diff(
    nse_symbols: dict[str, str],
    current_symbols: set[str],
) -> tuple[list[str], list[str]]:
    """Return (added, removed) symbol lists."""
    nse_set = set(nse_symbols.keys())
    added = sorted(nse_set - current_symbols)
    removed = sorted(current_symbols - nse_set)
    return added, removed


# ── Output file generation ───────────────────────────────────────────────────

def _format_symbol_meta(sm: object, note: str = "") -> str:
    """Format a SymbolMeta as a Python source line."""
    sym = sm.symbol  # type: ignore[attr-defined]
    seg = sm.segment  # type: ignore[attr-defined]
    sec = sm.sector  # type: ignore[attr-defined]
    line = f'    SymbolMeta("{sym}", "{seg}", "{sec}"),'
    if note:
        line += f"  # {note}"
    return line


def _format_new_symbol(symbol: str, industry: str) -> str:
    """Format a brand-new symbol (unknown segment → SMALL default)."""
    sector = _map_industry(industry)
    seg = "SMALL"
    line = f'    SymbolMeta("{symbol}", "{seg}", "{sector}"),'
    line += f"  # NEW - added {date.today()}, NSE Industry: {industry}"
    if sector == "Unknown":
        line += " [REVIEW: sector not mapped]"
    return line


def write_output_file(
    large_cap: list,
    mid_cap: list,
    small_cap: list,
    added: list[str],
    removed: list[str],
    nse_symbols: dict[str, str],
) -> None:
    """Write the staged output Python file with the three updated lists."""
    today = date.today().isoformat()
    added_set = set(added)
    removed_set = set(removed)

    lines: list[str] = [
        f'"""',
        f"AUTO-GENERATED by scripts/sync_nifty500.py on {today}",
        f"",
        f"SUMMARY:",
        f"  Added   : {len(added)} symbols  → {', '.join(added) or 'none'}",
        f"  Removed : {len(removed)} symbols  → {', '.join(removed) or 'none'}",
        f"",
        f"INSTRUCTIONS:",
        f"  1. Review each list below (additions marked NEW, removals omitted).",
        f"  2. Reclassify any NEW symbols that are not SMALL-cap.",
        f"  3. Verify sectors for NEW symbols marked [REVIEW].",
        f"  4. Copy the updated _LARGE_CAP, _MID_CAP, _SMALL_CAP into",
        f"     src/app/data/nifty500_symbols.py (replacing the existing lists).",
        f"  5. Update the docstring date at the top of nifty500_symbols.py.",
        f'"""',
        f"from app.data.nifty500_symbols import SymbolMeta  # for IDE support only",
        f"",
    ]

    # _LARGE_CAP
    lines.append("_LARGE_CAP: list[SymbolMeta] = [")
    for sm in large_cap:
        if sm.symbol not in removed_set:
            lines.append(_format_symbol_meta(sm))
    lines.append("]")
    lines.append("")

    # _MID_CAP
    lines.append("_MID_CAP: list[SymbolMeta] = [")
    for sm in mid_cap:
        if sm.symbol not in removed_set:
            lines.append(_format_symbol_meta(sm))
    lines.append("]")
    lines.append("")

    # _SMALL_CAP (existing + new additions at the end)
    lines.append("_SMALL_CAP: list[SymbolMeta] = [")
    for sm in small_cap:
        if sm.symbol not in removed_set:
            lines.append(_format_symbol_meta(sm))
    # Append new symbols (default to SMALL)
    for sym in sorted(added_set):
        industry = nse_symbols.get(sym, "Unknown")
        lines.append(_format_new_symbol(sym, industry))
    lines.append("]")
    lines.append("")

    _OUTPUT_FILE.write_text("\n".join(lines), encoding="utf-8")
    print(f"\nStaging file written: {_OUTPUT_FILE}")


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(message)s")

    print("\n=== NIFTY 500 Sync Script ===\n")
    print(f"Source : {_NSE_INDEX_URL}")
    print(f"Target : {_NIFTY_SRC}")
    print()

    # 1. Fetch from NSE
    try:
        raw_csv = fetch_nifty500_csv()
    except Exception as exc:
        sys.exit(f"ERROR fetching NSE CSV: {exc}")

    # 2. Parse CSV
    try:
        nse_symbols = parse_nse_csv(raw_csv)
    except Exception as exc:
        sys.exit(f"ERROR parsing NSE CSV: {exc}")

    print(f"NSE CSV: {len(nse_symbols)} EQ symbols found\n")

    # 3. Load current symbols
    try:
        mod = load_current_module()
    except Exception as exc:
        sys.exit(f"ERROR loading {_NIFTY_SRC}: {exc}")

    large_cap = list(mod._LARGE_CAP)
    mid_cap = list(mod._MID_CAP)
    small_cap = list(mod._SMALL_CAP)

    current_all = {sm.symbol for sm in large_cap + mid_cap + small_cap}
    print(
        f"Current file: {len(large_cap)} LARGE + {len(mid_cap)} MID + "
        f"{len(small_cap)} SMALL = {len(current_all)} total\n"
    )

    # 4. Diff
    added, removed = compute_diff(nse_symbols, current_all)

    print(f"Additions ({len(added)}):")
    if added:
        for sym in added:
            industry = nse_symbols.get(sym, "Unknown")
            sector_guess = _map_industry(industry)
            print(f"  + {sym:<20} NSE Industry: {industry:<35} → sector guess: {sector_guess}")
    else:
        print("  (none)")

    print()
    print(f"Removals ({len(removed)}):")
    if removed:
        for sym in removed:
            # Find which segment it's in
            seg = next(
                (sm.segment for sm in large_cap + mid_cap + small_cap if sm.symbol == sym),
                "?",
            )
            print(f"  - {sym:<20} (was {seg})")
    else:
        print("  (none)")

    print()
    new_total = len(current_all) + len(added) - len(removed)
    print(f"Count change: {len(current_all)} → {new_total} symbols")
    print()

    if not added and not removed:
        print("No changes detected. NIFTY 500 list is up-to-date.")
        return

    # 5. Write staging output
    write_output_file(large_cap, mid_cap, small_cap, added, removed, nse_symbols)

    print(
        f"\nNext steps:\n"
        f"  1. Review {_OUTPUT_FILE.name} (check NEW symbols, reclassify segments if needed)\n"
        f"  2. Copy the updated _LARGE_CAP / _MID_CAP / _SMALL_CAP lists into\n"
        f"     src/app/data/nifty500_symbols.py\n"
        f"  3. Update the 'as of' date in that file's docstring\n"
        f"  4. Run: python -m scripts.validate_environment  (smoke test)\n"
        f"  5. Commit with message: 'data: sync NIFTY 500 constituents {date.today().strftime(\"%b %Y\")}'\n"
    )


if __name__ == "__main__":
    main()
