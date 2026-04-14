"""Tests for AC-332: build_client_from_settings falls back to ANTHROPIC_API_KEY.

Verifies that the llm_client builder checks both AUTOCONTEXT_ANTHROPIC_API_KEY
and ANTHROPIC_API_KEY, matching the behavior of the provider registry.
"""

from __future__ import annotations

import os
from unittest.mock import patch


class TestApiKeyFallback:
    def test_uses_autocontext_key_when_set(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.config.settings import AppSettings

        settings = AppSettings(
            agent_provider="anthropic",
            anthropic_api_key="sk-autocontext-key",
        )
        client = build_client_from_settings(settings)
        assert client is not None

    def test_falls_back_to_anthropic_api_key_env(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.config.settings import AppSettings

        settings = AppSettings(
            agent_provider="anthropic",
            anthropic_api_key="",  # Not set via AUTOCONTEXT_ prefix
        )

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-fallback-key"}, clear=False):
            client = build_client_from_settings(settings)

        assert client is not None

    def test_load_settings_reads_anthropic_api_key_alias(self) -> None:
        from autocontext.config.settings import load_settings

        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "sk-standard-key"}, clear=False):
            settings = load_settings()

        assert settings.anthropic_api_key == "sk-standard-key"

    def test_load_settings_prefers_standard_anthropic_api_key(self) -> None:
        from autocontext.config.settings import load_settings

        with patch.dict(
            os.environ,
            {
                "ANTHROPIC_API_KEY": "sk-standard-key",
                "AUTOCONTEXT_ANTHROPIC_API_KEY": "sk-compat-key",
            },
            clear=False,
        ):
            settings = load_settings()

        assert settings.anthropic_api_key == "sk-standard-key"

    def test_raises_when_no_key_at_all(self) -> None:
        import pytest

        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.config.settings import AppSettings

        settings = AppSettings(
            agent_provider="anthropic",
            anthropic_api_key="",
        )

        with (
            patch.dict(os.environ, {}, clear=False),
            patch.object(
                os,
                "getenv",
                side_effect=lambda k, d=None: None
                if k in {"ANTHROPIC_API_KEY", "AUTOCONTEXT_ANTHROPIC_API_KEY"}
                else os.environ.get(k, d),
            ),
            pytest.raises(ValueError, match="ANTHROPIC_API_KEY"),
        ):
            build_client_from_settings(settings)

    def test_deterministic_doesnt_need_key(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.config.settings import AppSettings

        settings = AppSettings(agent_provider="deterministic")
        client = build_client_from_settings(settings)
        assert client is not None
