#!/usr/bin/env python3
"""
Validate that the environment is correctly configured.

Usage:
    python -m scripts.validate_environment
"""
from __future__ import annotations

import sys

sys.path.insert(0, "src")


def check(label: str, ok: bool, detail: str = "") -> bool:
    status = "PASS" if ok else "FAIL"
    print(f"  [{status}] {label}" + (f": {detail}" if detail else ""))
    return ok


def main() -> None:
    import platform
    print(f"\nMarket AI Environment Validation")
    print(f"Python {sys.version}")
    print(f"Platform: {platform.system()}\n")

    results = []

    # Python version
    results.append(check("Python >= 3.10", sys.version_info >= (3, 10), f"{sys.version_info.major}.{sys.version_info.minor}"))

    # Core imports
    for pkg in ["pydantic", "pydantic_settings", "httpx", "tenacity"]:
        try:
            __import__(pkg)
            results.append(check(f"Import {pkg}", True))
        except ImportError as e:
            results.append(check(f"Import {pkg}", False, str(e)))

    # Optional imports (not counted in pass/fail)
    for pkg in ["pandas", "numpy", "pyarrow"]:
        try:
            __import__(pkg)
            print(f"  [INFO] {pkg} available")
        except ImportError:
            print(f"  [WARN] {pkg} not installed (optional) – run: pip install {pkg}")

    # App config
    try:
        from app.core.config import get_settings
        s = get_settings()
        results.append(check("Settings load", True))
        results.append(check("ENABLE_LIVE_ORDERS=false", not s.enable_live_orders))
        results.append(check("TRADING_MODE=RESEARCH_ONLY", s.trading_mode == "RESEARCH_ONLY"))
        if s.telegram_configured:
            results.append(check("Telegram configured", True))
        else:
            print("  [INFO] Telegram not configured (optional)")
        if s.groww_configured:
            results.append(check("Groww configured", True))
        else:
            print("  [INFO] Groww not configured (mock data will be used)")
    except Exception as e:
        results.append(check("Settings load", False, str(e)))

    # Strategy registry
    try:
        import app.strategies.rsi_supertrend_volume  # noqa
        import app.strategies.morning_star          # noqa
        import app.strategies.gap_up_volume        # noqa
        import app.strategies.intraday_support_supertrend  # noqa
        from app.strategies.registry import all_ids
        ids = all_ids()
        results.append(check(f"Strategies registered ({len(ids)})", len(ids) >= 4, str(ids)))
    except Exception as e:
        results.append(check("Strategy registry", False, str(e)))

    print()
    passed = sum(results)
    total = len(results)
    print(f"Result: {passed}/{total} checks passed")

    if not all(results):
        sys.exit(1)


if __name__ == "__main__":
    main()
