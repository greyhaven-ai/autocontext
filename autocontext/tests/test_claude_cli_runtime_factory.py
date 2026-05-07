"""AC-735 follow-up — centralized Claude CLI runtime factory.

Reviewer P1: ``RuntimeBudget`` was wired only in
``build_client_from_settings()``. Other production paths
(``create_role_client('claude-cli', ...)``, providers registry's
``get_provider('claude-cli', ...)``) constructed ``ClaudeCLIRuntime``
without a budget, so multi-role/multi-judge runs could exceed the
advertised wall-clock cap.

These tests pin the new ``build_claude_cli_runtime`` factory and the
three call sites that must use it.
"""

from __future__ import annotations

import pytest

from autocontext.config.settings import AppSettings


class TestBuildClaudeCliRuntime:
    """``build_claude_cli_runtime`` is the single source of truth."""

    def test_returns_runtime_with_no_budget_when_setting_is_zero(self) -> None:
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        settings = AppSettings(claude_max_total_seconds=0.0)
        runtime = build_claude_cli_runtime(settings)
        assert runtime._budget is None  # noqa: SLF001

    def test_returns_runtime_with_budget_when_setting_is_positive(self) -> None:
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        settings = AppSettings(claude_max_total_seconds=180.0)
        runtime = build_claude_cli_runtime(settings)
        assert runtime._budget is not None  # noqa: SLF001
        assert runtime._budget.total_seconds == 180.0  # noqa: SLF001

    def test_propagates_retry_settings_into_config(self) -> None:
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        settings = AppSettings(
            claude_max_retries=4,
            claude_retry_backoff_seconds=0.5,
            claude_retry_backoff_multiplier=3.0,
            claude_timeout=42.0,
        )
        runtime = build_claude_cli_runtime(settings)
        assert runtime._config.max_retries == 4  # noqa: SLF001
        assert runtime._config.retry_backoff_seconds == 0.5  # noqa: SLF001
        assert runtime._config.retry_backoff_multiplier == 3.0  # noqa: SLF001
        assert runtime._config.timeout == 42.0  # noqa: SLF001

    def test_model_override_takes_precedence_over_settings(self) -> None:
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        settings = AppSettings(claude_model="sonnet")
        runtime = build_claude_cli_runtime(settings, model_override="opus")
        assert runtime._config.model == "opus"  # noqa: SLF001

    def test_no_model_override_uses_settings(self) -> None:
        from autocontext.runtimes.claude_cli import build_claude_cli_runtime

        settings = AppSettings(claude_model="opus")
        runtime = build_claude_cli_runtime(settings)
        assert runtime._config.model == "opus"  # noqa: SLF001


class TestRoleClientWiresBudget:
    """``create_role_client('claude-cli', ...)`` must attach the budget."""

    def test_role_client_runtime_has_budget(self) -> None:
        from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client

        settings = AppSettings(claude_max_total_seconds=240.0)
        client = create_role_client("claude-cli", settings)
        assert isinstance(client, RuntimeBridgeClient)
        assert client._runtime._budget is not None  # noqa: SLF001
        assert client._runtime._budget.total_seconds == 240.0  # noqa: SLF001

    def test_role_client_runtime_has_no_budget_when_disabled(self) -> None:
        from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client

        settings = AppSettings(claude_max_total_seconds=0.0)
        client = create_role_client("claude-cli", settings)
        assert isinstance(client, RuntimeBridgeClient)
        assert client._runtime._budget is None  # noqa: SLF001


class TestProviderRegistryWiresBudget:
    """``providers.registry.get_provider`` claude-cli branch must attach the budget."""

    def test_provider_runtime_has_budget(self) -> None:
        from autocontext.providers.registry import get_provider

        settings = AppSettings(judge_provider="claude-cli", claude_max_total_seconds=120.0)
        provider = get_provider(settings)
        assert provider._runtime._budget is not None  # noqa: SLF001
        assert provider._runtime._budget.total_seconds == 120.0  # noqa: SLF001

    def test_provider_runtime_has_no_budget_when_disabled(self) -> None:
        from autocontext.providers.registry import get_provider

        settings = AppSettings(judge_provider="claude-cli", claude_max_total_seconds=0.0)
        provider = get_provider(settings)
        assert provider._runtime._budget is None  # noqa: SLF001


class TestBuildClientFromSettingsStillWiresBudget:
    """Regression: don't lose the existing wiring."""

    def test_build_client_from_settings_attaches_budget(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings

        settings = AppSettings(agent_provider="claude-cli", claude_max_total_seconds=300.0)
        client = build_client_from_settings(settings)
        assert client._runtime._budget is not None  # noqa: SLF001
        assert client._runtime._budget.total_seconds == 300.0  # noqa: SLF001

    @pytest.mark.parametrize("seconds", [0.0])
    def test_build_client_from_settings_skips_budget_when_disabled(self, seconds: float) -> None:
        from autocontext.agents.llm_client import build_client_from_settings

        settings = AppSettings(agent_provider="claude-cli", claude_max_total_seconds=seconds)
        client = build_client_from_settings(settings)
        assert client._runtime._budget is None  # noqa: SLF001
