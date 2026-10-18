"""Lambda handler — validate the Groww Bearer token is present and readable.

The Groww Trade API token is static (generated from the dashboard) and stored
manually in Secrets Manager. This handler simply verifies it is accessible —
no HTTP login or TOTP generation occurs.
"""
from __future__ import annotations

import logging

log = logging.getLogger(__name__)
log.setLevel(logging.INFO)


def handler(event: dict, context: object) -> dict:  # noqa: ARG001
    """Check that the Groww access token is readable from Secrets Manager."""
    try:
        from app.auth.groww_auth import GrowwAuthManager

        auth = GrowwAuthManager()
        token = auth.get_token()
        log.info("Groww token is present (prefix=%s…)", token[:8])
        return {"statusCode": 200, "body": "Groww token OK"}
    except Exception as exc:
        log.error("Groww token check failed: %s", exc)
        return {
            "statusCode": 500,
            "body": (
                "Groww token missing. Store it with: "
                "aws secretsmanager put-secret-value "
                "--secret-id market-ai-dev/groww "
                "--secret-string '{\"access_token\":\"<your-token>\"}'"
            ),
        }
