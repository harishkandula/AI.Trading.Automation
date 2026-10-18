"""Groww API authentication manager."""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import struct
import time as _time_mod

import boto3
import httpx

try:
    import pyotp as _pyotp  # type: ignore[import-untyped]

    _HAS_PYOTP = True
except ImportError:  # pragma: no cover — pyotp preferred in Lambda runtime
    _pyotp = None  # type: ignore[assignment]
    _HAS_PYOTP = False

log = logging.getLogger(__name__)

_GROWW_BASE = "https://api.groww.in/v1"
_LOGIN_URL = f"{_GROWW_BASE}/auth/login"


def _stdlib_totp(secret: str, digits: int = 6, interval: int = 30) -> str:
    """RFC 6238 TOTP over SHA-1 using stdlib only.  Used when pyotp is absent."""
    # Pad to valid base32 length.
    padded = secret.upper() + "=" * (-len(secret) % 8)
    key = base64.b32decode(padded)
    counter = int(_time_mod.time()) // interval
    msg = struct.pack(">Q", counter)
    digest = hmac.new(key, msg, hashlib.sha1).digest()
    offset = digest[-1] & 0x0F
    code = struct.unpack(">I", digest[offset : offset + 4])[0] & 0x7FFF_FFFF
    return str(code % (10**digits)).zfill(digits)


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

    # ------------------------------------------------------------------
    # Private helpers
    # ------------------------------------------------------------------

    def _load_secret(self) -> None:
        try:
            resp = self._sm.get_secret_value(SecretId=self._secret_id)
            self._secret = json.loads(resp["SecretString"])
        except Exception:
            log.warning("Failed to load secret %s", self._secret_id, exc_info=True)
            self._secret = {}

    def _update_secret(self, updates: dict) -> None:
        """Merge *updates* into the stored secret and push to Secrets Manager."""
        self._secret.update(updates)
        try:
            self._sm.put_secret_value(
                SecretId=self._secret_id,
                SecretString=json.dumps(self._secret),
            )
        except Exception:
            log.error(
                "Failed to persist secret updates to %s", self._secret_id, exc_info=True
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def generate_totp(self) -> str:
        """Return the current 6-digit TOTP string derived from *totp_secret*."""
        totp_secret = self._secret.get("totp_secret", "")
        if not totp_secret:
            raise ValueError("totp_secret is missing from the secret")
        if _HAS_PYOTP:
            totp = _pyotp.TOTP(totp_secret, digits=6, digest="sha1", interval=30)
            return totp.now()
        return _stdlib_totp(totp_secret)

    def refresh_token(self) -> str:
        """Authenticate with Groww, store the new token in Secrets Manager, and return it."""
        api_key = self._secret.get("api_key", "")
        if not api_key:
            raise ValueError("api_key is missing from the secret")

        totp_code = self.generate_totp()

        payload = {"api_key": api_key, "totp": totp_code}
        try:
            resp = httpx.post(
                _LOGIN_URL,
                json=payload,
                timeout=15,
            )
            resp.raise_for_status()
        except httpx.HTTPStatusError as exc:
            log.error(
                "Groww login HTTP error %s: %s", exc.response.status_code, exc.response.text
            )
            raise
        except Exception:
            log.error("Groww login request failed", exc_info=True)
            raise

        data = resp.json()
        token = data.get("access_token") or data.get("data", {}).get("access_token", "")
        if not token:
            raise ValueError(f"access_token missing in Groww login response: {data}")

        self._update_secret({"access_token": token})
        log.info("Groww access_token refreshed and stored in Secrets Manager")
        return token

    def get_token(self) -> str:
        """Return the current Bearer token from Secrets Manager.

        Raises
        ------
        ValueError
            If no access_token is found in the secret.
        """
        token = self._secret.get("access_token", "")
        if not token:
            # Try a fresh read in case the token was updated by another process.
            self._load_secret()
            token = self._secret.get("access_token", "")
        if not token:
            raise ValueError(
                f"access_token not found in secret {self._secret_id}. "
                "Run the token_refresh Lambda first."
            )
        return token
