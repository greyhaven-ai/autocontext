"""Tests for first-class subscription-backed CLI provider surfaces.

Verifies that Claude CLI and Codex can be selected through the same top-level
live-provider paths that Pi already uses.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

from autocontext.agents.llm_client import build_client_from_settings
from autocontext.config.settings import AppSettings
from autocontext.providers.registry import get_provider


def _settings(**overrides: object) -> AppSettings:
    defaults = {
        "agent_provider": "deterministic",
        "knowledge_root": Path("/tmp/ac-test-knowledge"),
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


class TestTopLevelAgentProviderSurface:
    def test_build_client_accepts_claude_cli_provider(self) -> None:
        settings = _settings(agent_provider="claude-cli", claude_model="sonnet")
        with patch("autocontext.runtimes.claude_cli.ClaudeCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert client is not None

    def test_build_client_accepts_codex_provider(self) -> None:
        settings = _settings(agent_provider="codex", codex_model="o4-mini")
        with patch("autocontext.runtimes.codex_cli.CodexCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert client is not None

    def test_claude_cli_settings_flow_into_runtime(self) -> None:
        settings = _settings(
            agent_provider="claude-cli",
            claude_model="opus",
            claude_timeout=75.0,
            claude_tools="read,edit,bash",
            claude_permission_mode="acceptEdits",
            claude_session_persistence=True,
        )
        with patch("autocontext.runtimes.claude_cli.ClaudeCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        call_args = MockRuntime.call_args
        config = call_args[0][0] if call_args[0] else call_args[1].get("config")
        assert config.model == "opus"
        assert config.timeout == 75.0
        assert config.tools == "read,edit,bash"
        assert config.permission_mode == "acceptEdits"
        assert config.session_persistence is True

    def test_codex_settings_flow_into_runtime(self) -> None:
        settings = _settings(
            agent_provider="codex",
            codex_model="o3",
            codex_timeout=90.0,
            codex_workspace="/tmp/codex-workspace",
            codex_approval_mode="full-auto",
            codex_quiet=True,
        )
        with patch("autocontext.runtimes.codex_cli.CodexCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        call_args = MockRuntime.call_args
        config = call_args[0][0] if call_args[0] else call_args[1].get("config")
        assert config.model == "o3"
        assert config.timeout == 90.0
        assert config.workspace == "/tmp/codex-workspace"
        assert config.approval_mode == "full-auto"
        assert config.quiet is True


class TestJudgeProviderSurface:
    def test_get_provider_accepts_claude_cli(self) -> None:
        settings = _settings(judge_provider="claude-cli", claude_model="sonnet")
        with patch("autocontext.runtimes.claude_cli.ClaudeCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            provider = get_provider(settings)
        assert provider.name == "runtime-bridge"
        assert provider.default_model() == "sonnet"

    def test_get_provider_accepts_codex(self) -> None:
        settings = _settings(judge_provider="codex", codex_model="o4-mini")
        with patch("autocontext.runtimes.codex_cli.CodexCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            provider = get_provider(settings)
        assert provider.name == "runtime-bridge"
        assert provider.default_model() == "o4-mini"
