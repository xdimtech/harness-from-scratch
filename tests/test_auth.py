"""tests/test_auth.py - Unit tests for agents/auth.py make_client()."""

from __future__ import annotations

import os
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _base_env(monkeypatch, *, base_url: str | None = "http://localhost:9090") -> None:
    """Set the minimum env vars needed for a successful make_client() call."""
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
    monkeypatch.setenv("MODEL_ID", "claude-test-model")
    if base_url is not None:
        monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    else:
        monkeypatch.delenv("ANTHROPIC_BASE_URL", raising=False)


# ---------------------------------------------------------------------------
# Test cases
# ---------------------------------------------------------------------------

class TestMakeClientHappyPath:
    """1. Normal path: all three env vars set → returns (Anthropic, str) tuple."""

    def test_returns_tuple_of_client_and_model_string(self, monkeypatch):
        _base_env(monkeypatch, base_url="http://localhost:9090")
        # Prevent load_dotenv from overwriting our monkeypatched values
        with patch("agents.auth.load_dotenv"):
            mock_anthropic_cls = MagicMock()
            mock_client_instance = MagicMock()
            mock_anthropic_cls.return_value = mock_client_instance

            with patch("agents.auth.Anthropic", mock_anthropic_cls):
                from agents.auth import make_client  # noqa: PLC0415

                client, model = make_client()

        assert client is mock_client_instance
        assert isinstance(model, str)
        assert model == "claude-test-model"

    def test_anthropic_constructor_called_with_env_values(self, monkeypatch):
        _base_env(monkeypatch, base_url="http://localhost:9090")
        with patch("agents.auth.load_dotenv"):
            mock_anthropic_cls = MagicMock()

            with patch("agents.auth.Anthropic", mock_anthropic_cls):
                from agents.auth import make_client  # noqa: PLC0415

                make_client()

        mock_anthropic_cls.assert_called_once_with(
            api_key="test-api-key",
            base_url="http://localhost:9090",
        )


class TestAuthTokenStrippedWhenBaseUrlSet:
    """2. When ANTHROPIC_BASE_URL is present, ANTHROPIC_AUTH_TOKEN must be removed."""

    def test_auth_token_removed_from_environ(self, monkeypatch):
        _base_env(monkeypatch, base_url="http://localhost:9090")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "should-be-removed")

        with patch("agents.auth.load_dotenv"):
            with patch("agents.auth.Anthropic", MagicMock()):
                from agents.auth import make_client  # noqa: PLC0415

                make_client()

        assert "ANTHROPIC_AUTH_TOKEN" not in os.environ


class TestAuthTokenKeptWhenNoBaseUrl:
    """3. When ANTHROPIC_BASE_URL is NOT set, ANTHROPIC_AUTH_TOKEN should remain."""

    def test_auth_token_preserved_in_environ(self, monkeypatch):
        _base_env(monkeypatch, base_url=None)  # no BASE_URL
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "keep-this-token")

        with patch("agents.auth.load_dotenv"):
            with patch("agents.auth.Anthropic", MagicMock()):
                from agents.auth import make_client  # noqa: PLC0415

                make_client()

        assert os.environ.get("ANTHROPIC_AUTH_TOKEN") == "keep-this-token"


class TestMissingModelIdRaisesKeyError:
    """4. Missing MODEL_ID → make_client() must raise KeyError."""

    def test_raises_key_error_when_model_id_missing(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-api-key")
        monkeypatch.setenv("ANTHROPIC_BASE_URL", "http://localhost:9090")
        monkeypatch.delenv("MODEL_ID", raising=False)

        with patch("agents.auth.load_dotenv"):
            with patch("agents.auth.Anthropic", MagicMock()):
                from agents.auth import make_client  # noqa: PLC0415

                with pytest.raises(KeyError):
                    make_client()
