"""Unit tests for GrowwAuthManager — all AWS calls are mocked."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch


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


class TestGetToken:
    def test_returns_token_from_secret(self):
        auth = _make_auth({"access_token": "my-live-token"})
        assert auth.get_token() == "my-live-token"

    def test_raises_when_token_absent(self):
        import pytest

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

    def test_loads_secret_on_init_failure_gracefully(self):
        from app.auth.groww_auth import GrowwAuthManager

        mock_sm = MagicMock()
        mock_sm.get_secret_value.side_effect = Exception("network error")
        with patch("app.auth.groww_auth.boto3") as mock_boto:
            mock_boto.client.return_value = mock_sm
            auth = GrowwAuthManager(secret_id="test/groww", region="ap-south-1")

        assert auth._secret == {}
