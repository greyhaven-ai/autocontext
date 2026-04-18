"""AC-570 — claude-cli timeout defaults and observability.

Pins the raised default (300s) and the existing override paths
(--timeout flag, AUTOCONTEXT_CLAUDE_TIMEOUT env var).
"""
from __future__ import annotations

import logging
import subprocess
from unittest.mock import patch

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.runtimes.claude_cli import ClaudeCLIConfig, ClaudeCLIRuntime


class TestClaudeTimeoutDefaults:
    def test_app_settings_claude_timeout_default_is_300s(self) -> None:
        settings = AppSettings()
        assert settings.claude_timeout == 300.0

    def test_claude_cli_config_default_is_300s(self) -> None:
        cfg = ClaudeCLIConfig()
        assert cfg.timeout == 300.0

    def test_env_var_overrides_default(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """AUTOCONTEXT_CLAUDE_TIMEOUT has always overridden the default; pin it."""
        monkeypatch.setenv("AUTOCONTEXT_CLAUDE_TIMEOUT", "45")

        settings = load_settings()

        assert settings.claude_timeout == 45.0

    def test_cli_timeout_flag_overrides_default_for_claude_cli(self) -> None:
        """--timeout flag routes through apply_judge_runtime_overrides and wins
        over the default for CLI-backed providers."""
        from autocontext.cli_runtime_overrides import apply_judge_runtime_overrides

        base = AppSettings()  # claude_timeout defaults to 300
        resolved = apply_judge_runtime_overrides(
            base, provider_name="claude-cli", timeout=90.0
        )

        assert resolved.claude_timeout == 90.0
