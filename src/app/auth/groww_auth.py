"""Groww API authentication — reads a static Bearer token from Secrets Manager.

The Groww Trade API uses a long-lived API token generated from the developer
dashboard (groww.in/trade-api/api-keys). There is no programmatic login or
TOTP flow — the token is stored once in Secrets Manager and read at runtime.
"""
from __future__ import annotations

import json
import logging

import boto3

log = logging.getLogger(__name__)


class GrowwAuthManager:
    """Reads the Groww Bearer token from AWS Secrets Manager."""

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

    def get_token(self) -> str:
        """Return the Bearer token from Secrets Manager.

        Raises ValueError if no token is found — store one with:
            aws secretsmanager put-secret-value \\
              --secret-id market-ai-dev/groww \\
              --secret-string '{"access_token":"<your-groww-api-token>"}'
        """
        token = self._secret.get("access_token", "")
        if not token:
            self._load_secret()
            token = self._secret.get("access_token", "")
        if not token:
            raise ValueError(
                f"access_token not found in secret {self._secret_id}. "
                "Store your Groww API token with: "
                "aws secretsmanager put-secret-value "
                "--secret-id market-ai-dev/groww "
                "--secret-string '{\"access_token\":\"<your-token>\"}'"
            )
        return token
