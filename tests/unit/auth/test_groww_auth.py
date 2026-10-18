"""Unit tests for GrowwAuthManager — all AWS and SDK calls are mocked."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _make_sm_client(secret: dict) -> MagicMock:
    mock_sm = MagicMock()
    mock_sm.get_secret_value.return_value = {"SecretString": json.dumps(secret)}
    return mock_sm


def _make_auth(secret: dict) -> object:
    from app.auth.groww_auth import GrowwAuthManager

    with patch("app.auth.groww_auth.boto3") as mock_boto:
        mock_boto.client.return_value = _make_sm_client(secret)
        auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")
    return auth


class TestGenerateTotp:
    def test_returns_six_digit_string(self):
        secret = {"totp_secret": "JBSWY3DPEHPK3PXP"}
        auth = _make_auth(secret)
        mock_pyotp = MagicMock()
        mock_totp = MagicMock()
        mock_totp.now.return_value = "123456"
        mock_pyotp.TOTP.return_value = mock_totp
        with patch.dict("sys.modules", {"pyotp": mock_pyotp}):
            code = auth.generate_totp()
        assert code == "123456"

    def test_raises_when_totp_secret_missing(self):
        auth = _make_auth({})
        with pytest.raises(ValueError, match="totp_secret"):
            auth.generate_totp()


class TestRefreshToken:
    def test_calls_growwapi_and_stores_token(self):
        from app.auth.groww_auth import GrowwAuthManager

        secret = {"totp_token": "my-totp-api-key", "totp_secret": "JBSWY3DPEHPK3PXP"}
        mock_sm = _make_sm_client(secret)

        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        mock_pyotp = MagicMock()
        mock_totp_obj = MagicMock()
        mock_totp_obj.now.return_value = "654321"
        mock_pyotp.TOTP.return_value = mock_totp_obj

        mock_groww_mod = MagicMock()
        mock_groww_cls = MagicMock()
        mock_groww_cls.get_access_token.return_value = "fresh-access-token"
        mock_groww_mod.GrowwAPI = mock_groww_cls

        with patch.dict("sys.modules", {"pyotp": mock_pyotp, "growwapi": mock_groww_mod}):
            token = auth.refresh_token()

        assert token == "fresh-access-token"
        mock_groww_cls.get_access_token.assert_called_once_with(
            api_key="my-totp-api-key", totp="654321"
        )
        mock_sm.put_secret_value.assert_called_once()

    def test_raises_when_totp_token_missing(self):
        auth = _make_auth({"totp_secret": "JBSWY3DPEHPK3PXP"})
        with pytest.raises(ValueError, match="totp_token"):
            auth.refresh_token()


class TestGetToken:
    def test_returns_access_token_from_secret(self):
        auth = _make_auth({"access_token": "my-live-token"})
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
        from app.auth.groww_auth import GrowwAuthManager

        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = [
            {"SecretString": json.dumps({})},
            {"SecretString": json.dumps({"access_token": "late-token"})},
        ]
        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        assert auth.get_token() == "late-token"
