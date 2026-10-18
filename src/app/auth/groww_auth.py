"""Groww API authentication — TOTP flow via growwapi SDK.

The Groww Trade API supports two flows:
  1. API key + secret (requires daily approval in dashboard — NOT used here)
  2. TOTP token + TOTP code (no expiry — used here for automation)

The access_token returned by get_access_token() is stored in Secrets Manager
and refreshed daily by the token_refresh Lambda so every scan starts with a
fresh token.

Secret layout in market-ai-dev/groww:
  {
    "totp_token":   "<API key for TOTP flow from dashboard>",
    "totp_secret":  "<base32 secret from QR code>",
    "access_token": "<refreshed daily by token_refresh Lambda>"
  }
"""
from __future__ import annotations

import json
import logging

import boto3

log = logging.getLogger(__name__)


class GrowwAuthManager:
    """Manages Groww Bearer token lifecycle using AWS Secrets Manager."""

    def __init__(
        self,
        secret_id: str = "market-ai-dev/groww",
        region: str = "ap-south-1",
    ) -> None:
        self._secret_id = secret_id
        self._sm = boto3.client("secretsmanager", region_name=region)
        self._secret: dict = {}
        self._load_secret()

    def _load_secret(self) -> None:
        try:
            resp = self._sm.get_secret_value(SecretId=self._secret_id)
            self._secret = json.loads(resp["SecretString"])
        except Exception:
            log.warning("Failed to load secret %s", self._secret_id, exc_info=True)
            self._secret = {}

    def _update_secret(self, updates: dict) -> None:
        self._secret.update(updates)
        try:
            self._sm.put_secret_value(
                SecretId=self._secret_id,
                SecretString=json.dumps(self._secret),
            )
        except Exception:
            log.error("Failed to persist secret updates to %s", self._secret_id, exc_info=True)

    def generate_totp(self) -> str:
        """Return the current 6-digit TOTP code from totp_secret."""
        totp_secret = self._secret.get("totp_secret", "")
        if not totp_secret:
            raise ValueError("totp_secret missing from Groww secret in Secrets Manager")
        import pyotp  # type: ignore[import-untyped]  # lazy — not available locally
        return pyotp.TOTP(totp_secret).now()

    def refresh_token(self) -> str:
        """Generate a fresh Groww access token via TOTP and store it in Secrets Manager."""
        totp_token = self._secret.get("totp_token") or self._secret.get("api_key", "")
        if not totp_token:
            raise ValueError(
                "api_key missing from secret — store your Groww TOTP API key "
                "(from groww.in/trade-api/api-keys → Generate TOTP token)"
            )

        from growwapi import GrowwAPI  # type: ignore[import-untyped]  # lazy — installed in Lambda

        # Generate TOTP immediately before the API call to minimise window-expiry risk
        totp_code = self.generate_totp()
        try:
            access_token = GrowwAPI.get_access_token(api_key=totp_token, totp=totp_code)
        except Exception as exc:
            log.error("get_access_token failed: %s", exc)
            raise
        if not access_token:
            raise ValueError("GrowwAPI.get_access_token returned empty token")

        self._update_secret({"access_token": access_token})
        log.info("Groww access_token refreshed (prefix=%s…)", str(access_token)[:8])
        return access_token

    def get_token(self) -> str:
        """Return the current Bearer token from Secrets Manager.

        Raises ValueError if absent — run the token_refresh Lambda first.
        """
        token = self._secret.get("access_token", "")
        if not token:
            self._load_secret()
            token = self._secret.get("access_token", "")
        if not token:
            raise ValueError(
                f"access_token not found in secret {self._secret_id}. "
                "Invoke the token_refresh Lambda to generate one."
            )
        return token
