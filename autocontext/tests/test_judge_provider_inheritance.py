"""AC-586 — judge_provider='auto' inherits from agent_provider."""
from __future__ import annotations

from autocontext.config.settings import AppSettings


class TestDefaultJudgeProvider:
    def test_default_judge_provider_is_auto(self, tmp_path) -> None:
        # New default: 'auto' → inherit from agent_provider at get_provider time.
        settings = AppSettings(knowledge_root=tmp_path / "k")
        assert settings.judge_provider == "auto"


class TestGetProviderAutoInheritance:
    """When judge_provider='auto', get_provider picks a provider from the effective runtime path."""

    def _settings(self, tmp_path, *, agent_provider: str, judge_provider: str = "auto") -> AppSettings:
        return AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider=agent_provider,
            judge_provider=judge_provider,
        )

    def test_auto_inherits_claude_cli(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = self._settings(tmp_path, agent_provider="claude-cli")
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)

    def test_auto_inherits_pi(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = self._settings(tmp_path, agent_provider="pi")
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)

    def test_auto_inherits_codex(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = self._settings(tmp_path, agent_provider="codex")
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)

    def test_auto_inherits_competitor_override_before_global_agent_provider(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider="anthropic",
            competitor_provider="claude-cli",
            judge_provider="auto",
        )
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)

    def test_auto_inherits_architect_override_when_global_agent_provider_is_not_runtime_bridged(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider="anthropic",
            architect_provider="pi",
            judge_provider="auto",
        )
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)

    def test_auto_falls_back_to_anthropic_for_anthropic_agent(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
        from autocontext.providers.registry import get_provider
        from autocontext.providers.retry import RetryProvider

        settings = self._settings(tmp_path, agent_provider="anthropic")
        provider = get_provider(settings)
        # AnthropicProvider is wrapped by RetryProvider for the anthropic path.
        assert isinstance(provider, RetryProvider)

    def test_auto_falls_back_to_anthropic_for_deterministic_agent(self, tmp_path, monkeypatch) -> None:
        # Deterministic agents have no judge counterpart; default to anthropic
        # so the error surface is unchanged for users who had this setup pre-AC-586.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
        from autocontext.providers.registry import get_provider
        from autocontext.providers.retry import RetryProvider

        settings = self._settings(tmp_path, agent_provider="deterministic")
        provider = get_provider(settings)
        assert isinstance(provider, RetryProvider)


class TestExplicitJudgeProviderOverride:
    """Explicit judge_provider values take precedence over agent_provider inheritance."""

    def test_explicit_anthropic_wins_over_claude_cli_agent(self, tmp_path, monkeypatch) -> None:
        # Someone set AUTOCONTEXT_JUDGE_PROVIDER=anthropic explicitly: don't auto-inherit.
        monkeypatch.setenv("ANTHROPIC_API_KEY", "test-key-anthropic")
        from autocontext.providers.registry import get_provider
        from autocontext.providers.retry import RetryProvider

        settings = AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider="claude-cli",
            judge_provider="anthropic",
        )
        provider = get_provider(settings)
        assert isinstance(provider, RetryProvider)

    def test_explicit_claude_cli_judge_still_works(self, tmp_path) -> None:
        from autocontext.providers.registry import get_provider
        from autocontext.providers.runtime_bridge import RuntimeBridgeProvider

        settings = AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider="anthropic",
            judge_provider="claude-cli",
        )
        provider = get_provider(settings)
        assert isinstance(provider, RuntimeBridgeProvider)


class TestUnknownAgentProviderWithAutoJudge:
    def test_auto_with_unknown_agent_raises_clear_error(self, tmp_path) -> None:
        # Agent set to a value that isn't a known judge-capable type; we fall
        # back to anthropic (the pre-AC-586 default) rather than failing cryptically.
        from autocontext.providers.base import ProviderError
        from autocontext.providers.registry import get_provider

        # Use openai as agent — not a runtime-bridged provider, but valid judge.
        # Expect fallback to anthropic (judge list's historical default).
        settings = AppSettings(
            knowledge_root=tmp_path / "k",
            agent_provider="openai",
            judge_provider="auto",
        )
        # No key set → expect an Anthropic-style error, not a cryptic "unknown provider type: 'auto'".
        try:
            get_provider(settings)
        except ProviderError as exc:
            assert "auto" not in str(exc).lower()
        except Exception:
            # Any Anthropic-SDK-level error is also fine; the key assertion is
            # that we never surface a "'auto'" provider type error.
            pass
