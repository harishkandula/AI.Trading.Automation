"""Lambda handler — daily Groww Bearer token refresh.

Scheduled at 00:30 UTC (06:00 IST) Mon-Fri via EventBridge.
"""
from __future__ import annotations

import json
import logging

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def handler(event: dict, context: object) -> dict:  # noqa: ARG001
    """Refresh the Groww access token and store it in Secrets Manager."""
    try:
        from app.auth.groww_auth import GrowwAuthManager

        auth = GrowwAuthManager()
        token = auth.refresh_token()
        # Log a redacted prefix for auditability.
        log.info("Groww token refreshed successfully (prefix=%s…)", token[:8])
        return {"statusCode": 200, "body": "Token refreshed"}
    except Exception as exc:
        log.error("Token refresh failed: %s", exc, exc_info=True)
        return {"statusCode": 500, "body": json.dumps({"error": str(exc)})}
