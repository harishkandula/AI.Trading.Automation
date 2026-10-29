"""SSM Parameter Store helpers for runtime configuration.

All functions are best-effort: any boto3/SSM error is silently swallowed and
the caller receives a safe default.  This lets the app run locally (no SSM
access) and survive transient AWS errors without crashing.

Parameter layout (see infrastructure/main.tf):
    /{name}/universe                        — symbol universe name
    /{name}/portfolio_equity                — portfolio equity in INR
    /{name}/risk/max_open_positions         — int
    /{name}/risk/max_sector_exposure_pct    — float (e.g. "0.30")

Usage:
    from app.core.ssm_config import get_portfolio_equity, get_risk_config_from_ssm
"""
from __future__ import annotations

import logging
import os

log = logging.getLogger(__name__)

# SSM path prefix matches Terraform local.name = "${project}-${environment}"
# e.g. "market-ai-dev"
_NAME = os.environ.get("APP_NAME", "") or (
    f"{os.environ.get('PROJECT', 'market-ai')}-{os.environ.get('APP_ENV', 'dev')}"
)


def _ssm_prefix() -> str:
    return f"/{_NAME}"


def get_ssm_param(name: str, default: str = "") -> str:
    """Read a single SSM parameter by full path.

    Returns *default* on any error (permission denied, not found, no network,
    boto3 not installed, etc).  Never raises.
    """
    try:
        import boto3  # type: ignore[import-untyped]
        client = boto3.client("ssm")
        resp = client.get_parameter(Name=name, WithDecryption=True)
        value = resp["Parameter"]["Value"]
        log.debug("SSM %s = %r", name, value)
        return value
    except Exception as exc:
        log.debug("SSM get_parameter(%r) failed: %s — using default %r", name, exc, default)
        return default


def _get_param_path(relative: str, default: str = "") -> str:
    """Read a parameter by its relative path under the app prefix."""
    full_path = f"{_ssm_prefix()}/{relative.lstrip('/')}"
    return get_ssm_param(full_path, default)


def get_portfolio_equity() -> float:
    """Return portfolio equity (INR).

    Precedence:
      1. SSM /{name}/portfolio_equity
      2. PORTFOLIO_EQUITY env var
      3. Hard-coded default 500000
    """
    env_val = os.environ.get("PORTFOLIO_EQUITY", "")
    ssm_val = _get_param_path("portfolio_equity", default="")

    # SSM takes precedence over env var (SSM = live override, env = deployment default)
    raw = ssm_val or env_val or "500000"
    try:
        equity = float(raw)
        if equity <= 0:
            raise ValueError("equity must be positive")
        return equity
    except (ValueError, TypeError) as exc:
        log.warning("Invalid portfolio equity value %r: %s — using 500000", raw, exc)
        return 500000.0


def get_risk_config_from_ssm() -> dict:
    """Read risk configuration overrides from SSM.

    Returns a dict of keyword arguments suitable for ``RiskConfig(**overrides)``.
    Only parameters that are present and parseable are included — missing or
    malformed parameters are silently skipped so RiskConfig defaults apply.
    """
    overrides: dict = {}

    _int_params = [
        ("risk/max_open_positions", "max_open_positions"),
    ]
    _float_params = [
        ("risk/max_sector_exposure_pct", "max_sector_exposure_pct"),
    ]

    for rel_path, kwarg_name in _int_params:
        raw = _get_param_path(rel_path, default="")
        if raw:
            try:
                overrides[kwarg_name] = int(raw)
            except (ValueError, TypeError) as exc:
                log.warning(
                    "SSM %r has non-integer value %r: %s — skipping override",
                    rel_path, raw, exc,
                )

    for rel_path, kwarg_name in _float_params:
        raw = _get_param_path(rel_path, default="")
        if raw:
            try:
                overrides[kwarg_name] = float(raw)
            except (ValueError, TypeError) as exc:
                log.warning(
                    "SSM %r has non-float value %r: %s — skipping override",
                    rel_path, raw, exc,
                )

    if overrides:
        log.info("SSM risk config overrides: %s", overrides)
    return overrides
