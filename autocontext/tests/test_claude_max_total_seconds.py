"""AC-735 — claude-cli wall-clock budget settings + wiring.

Pins:
1. The new ``claude_max_total_seconds`` field defaults to 0 (disabled).
2. ``AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS`` env var sets the budget.
3. When > 0, the provider wires a ``RuntimeBudget`` into the runtime;
   when 0, no budget is attached.
"""

from __future__ import annotations

import pytest

from autocontext.config.settings import AppSettings, load_settings


class TestClaudeMaxTotalSecondsDefaults:
    def test_default_disabled(self) -> None:
        settings = AppSettings()
        assert settings.claude_max_total_seconds == 0.0

    def test_env_var_sets_budget(self, monkeypatch: pytest.MonkeyPatch) -> None:
        # AC-735 was originally caused by this env var being silently
        # ignored — pinning it now.
        monkeypatch.setenv("AUTOCONTEXT_CLAUDE_MAX_TOTAL_SECONDS", "28800")
        settings = load_settings()
        assert settings.claude_max_total_seconds == 28800.0

    def test_negative_budget_is_rejected(self) -> None:
        # Negative budgets are nonsensical; AppSettings should reject them
        # at construction time.
        from pydantic import ValidationError

        with pytest.raises(ValidationError):
            AppSettings(claude_max_total_seconds=-1.0)


class TestClaudeRuntimeWiring:
    def _build_provider(self, settings: AppSettings):
        # Lazy import — the provider builder pulls in many transitive deps.
        from autocontext.agents.llm_client import build_client_from_settings

        return build_client_from_settings(settings)

    def test_runtime_has_no_budget_when_disabled(self) -> None:
        settings = AppSettings(
            agent_provider="claude-cli",
            claude_max_total_seconds=0.0,
        )
        client = self._build_provider(settings)
        # Reach in to verify the runtime has no budget attached.
        runtime = client._runtime  # noqa: SLF001
        assert runtime._budget is None  # noqa: SLF001

    def test_runtime_has_budget_when_set(self) -> None:
        settings = AppSettings(
            agent_provider="claude-cli",
            claude_max_total_seconds=120.0,
        )
        client = self._build_provider(settings)
        runtime = client._runtime  # noqa: SLF001
        assert runtime._budget is not None  # noqa: SLF001
        assert runtime._budget.total_seconds == 120.0  # noqa: SLF001
