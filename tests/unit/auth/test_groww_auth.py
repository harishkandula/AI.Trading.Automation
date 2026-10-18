"""Unit tests for GrowwAuthManager — all AWS and HTTP calls are mocked."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_sm_client(secret: dict) -> MagicMock:
    """Return a mock boto3 Secrets Manager client."""
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {
        "SecretString": json.dumps(secret)
    }
    return mock_sm


def _make_auth(secret: dict) -> object:
    """Instantiate GrowwAuthManager with a mocked SM client and given secret."""
    from app.auth.groww_auth import GrowwAuthManager

    with patch("app.auth.groww_auth.boto3") as mock_boto:
        mock_boto.client.return_value = _make_sm_client(secret)
        auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")
    return auth


# ---------------------------------------------------------------------------
# generate_totp
# ---------------------------------------------------------------------------

class TestGenerateTotp:
    def test_returns_six_digit_string(self):
        # Use a well-known base32 test secret that pyotp accepts.
        secret = {"totp_secret": "JBSWY3DPEHPK3PXP"}
        auth = _make_auth(secret)
        totp_code = auth.generate_totp()
        assert isinstance(totp_code, str)
        assert len(totp_code) == 6
        assert totp_code.isdigit()

    def test_raises_when_totp_secret_missing(self):
        auth = _make_auth({})
        with pytest.raises(ValueError, match="totp_secret"):
            auth.generate_totp()

    def test_uses_sha1_algorithm(self):
        """Verify the TOTP aligns with the reference implementation output."""
        from app.auth.groww_auth import _HAS_PYOTP, _stdlib_totp

        totp_secret = "JBSWY3DPEHPK3PXP"
        auth = _make_auth({"totp_secret": totp_secret})
        result = auth.generate_totp()
        # Verify against whichever implementation is available.
        if _HAS_PYOTP:
            import pyotp

            expected = pyotp.TOTP(totp_secret, digits=6, digest="sha1", interval=30).now()
        else:
            expected = _stdlib_totp(totp_secret)
        assert result == expected


# ---------------------------------------------------------------------------
# refresh_token
# ---------------------------------------------------------------------------

class TestRefreshToken:
    def test_stores_token_in_secrets_manager(self):
        from app.auth.groww_auth import GrowwAuthManager

        secret = {"api_key": "test-api-key", "totp_secret": "JBSWY3DPEHPK3PXP"}
        mock_sm = _make_sm_client(secret)

        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        login_resp = MagicMock()
        login_resp.status_code = 200
        login_resp.json.return_value = {"access_token": "Bearer-abc123"}
        login_resp.raise_for_status = MagicMock()

        with patch("app.auth.groww_auth.httpx.post", return_value=login_resp):
            token = auth.refresh_token()

        assert token == "Bearer-abc123"
        # Verify put_secret_value was called with the new token embedded.
        mock_sm.put_secret_value.assert_called_once()
        call_kwargs = mock_sm.put_secret_value.call_args
        stored = json.loads(call_kwargs.kwargs.get("SecretString", "{}"))
        assert stored["access_token"] == "Bearer-abc123"

    def test_raises_when_api_key_missing(self):
        auth = _make_auth({"totp_secret": "JBSWY3DPEHPK3PXP"})
        with pytest.raises(ValueError, match="api_key"):
            auth.refresh_token()

    def test_raises_on_http_error(self):
        import httpx

        secret = {"api_key": "k", "totp_secret": "JBSWY3DPEHPK3PXP"}

        from app.auth.groww_auth import GrowwAuthManager

        mock_sm = _make_sm_client(secret)

        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        err_resp = MagicMock()
        err_resp.status_code = 401
        err_resp.text = "Unauthorized"
        http_err = httpx.HTTPStatusError("401", request=MagicMock(), response=err_resp)
        err_resp.raise_for_status.side_effect = http_err

        with patch("app.auth.groww_auth.httpx.post", return_value=err_resp):
            with pytest.raises(httpx.HTTPStatusError):
                auth.refresh_token()

    def test_handles_nested_data_access_token(self):
        """Groww may nest token inside data.access_token."""
        from app.auth.groww_auth import GrowwAuthManager

        secret = {"api_key": "k", "totp_secret": "JBSWY3DPEHPK3PXP"}
        mock_sm = _make_sm_client(secret)

        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        nested_resp = MagicMock()
        nested_resp.json.return_value = {"data": {"access_token": "nested-token"}}
        nested_resp.raise_for_status = MagicMock()

        with patch("app.auth.groww_auth.httpx.post", return_value=nested_resp):
            token = auth.refresh_token()

        assert token == "nested-token"


# ---------------------------------------------------------------------------
# get_token
# ---------------------------------------------------------------------------

class TestGetToken:
    def test_returns_access_token_from_secret(self):
        secret = {"access_token": "my-live-token"}
        auth = _make_auth(secret)
        assert auth.get_token() == "my-live-token"

    def test_raises_when_token_absent(self):
        from app.auth.groww_auth import GrowwAuthManager

        mock_sm = _make_sm_client({})
        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        with pytest.raises(ValueError, match="access_token"):
            auth.get_token()

    def test_retries_fresh_load_before_raising(self):
        """get_token re-reads Secrets Manager when local cache is empty."""
        from app.auth.groww_auth import GrowwAuthManager

        # First call (in __init__) returns empty, second call (in get_token) returns token.
        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({})},
            {"SecretString": json.dumps({"access_token": "late-token"})},
        ]
        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        token = auth.get_token()
        assert token == "late-token"
