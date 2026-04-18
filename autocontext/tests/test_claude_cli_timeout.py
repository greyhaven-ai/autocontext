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
