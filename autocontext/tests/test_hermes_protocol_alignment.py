"""Tests for AC-425: Hermes native runtime parity and override semantics.

Verifies that autocontext's Hermes CLI integration matches Hermes's
documented interface for toolsets, skills, worktree, quiet mode,
and provider override behavior.
"""

from __future__ import annotations


class TestHermesCLIFlags:
    """Verify Hermes CLI flags match documented interface."""

    def test_uses_chat_query_for_one_shot(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        runtime = HermesCLIRuntime(HermesCLIConfig())
        args = runtime._build_args("test prompt")
        assert "chat" in args
        assert "--query" in args

    def test_model_flag(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        runtime = HermesCLIRuntime(HermesCLIConfig(model="anthropic/claude-sonnet-4"))
        args = runtime._build_args("test")
        assert "--model" in args
        assert "anthropic/claude-sonnet-4" in args

    def test_toolsets_flag(self) -> None:
        """Hermes supports -t/--toolsets for tool selection."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(toolsets="web,terminal")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--toolsets" in args
        assert "web,terminal" in args

    def test_skills_flag(self) -> None:
        """Hermes supports -s/--skills for skill preloading."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(skills="github-pr-workflow")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--skills" in args
        assert "github-pr-workflow" in args

    def test_worktree_flag(self) -> None:
        """Hermes supports --worktree for isolated git worktree."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(worktree=True)
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--worktree" in args

    def test_quiet_flag(self) -> None:
        """Hermes supports --quiet to suppress UI chrome."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(quiet=True)
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--quiet" in args

    def test_provider_flag(self) -> None:
        """Hermes supports --provider for backend override."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(provider="anthropic")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--provider" in args
        assert "anthropic" in args

    def test_codex_provider_alias(self) -> None:
        """Legacy codex alias should map to Hermes's openai-codex provider flag."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(provider="codex")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        assert "--provider" in args
        assert "openai-codex" in args

    def test_no_flags_when_defaults(self) -> None:
        """Default config should not add optional flags."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        runtime = HermesCLIRuntime(HermesCLIConfig())
        args = runtime._build_args("test")
        assert "--toolsets" not in args
        assert "--skills" not in args
        assert "--worktree" not in args
        assert "--quiet" not in args
        assert "--provider" not in args


class TestHermesConfigSettings:
    """Verify Hermes config fields in AppSettings."""

    def test_new_hermes_settings_exist(self) -> None:
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        assert hasattr(settings, "hermes_toolsets")
        assert hasattr(settings, "hermes_skills")
        assert hasattr(settings, "hermes_worktree")
        assert hasattr(settings, "hermes_quiet")
        assert hasattr(settings, "hermes_provider")

    def test_new_hermes_settings_defaults(self) -> None:
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        assert settings.hermes_toolsets == ""
        assert settings.hermes_skills == ""
        assert settings.hermes_worktree is False
        assert settings.hermes_quiet is False
        assert settings.hermes_provider == ""


class TestHermesOverrideSemantics:
    """Verify env-based override behavior is documented accurately."""

    def test_custom_endpoint_skips_provider_flag(self) -> None:
        """Hermes v0.5.0+: custom endpoints use env vars, not --provider main."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(base_url="http://custom:8080/v1", api_key="token")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        # --provider main was removed in Hermes v0.5.0; custom endpoints
        # are auto-detected from OPENAI_BASE_URL env var
        assert "--provider" not in args
        env = runtime._build_env()
        assert env["OPENAI_BASE_URL"] == "http://custom:8080/v1"
        assert env["OPENAI_API_KEY"] == "token"

    def test_base_url_passed_via_env_not_flag(self) -> None:
        """OPENAI_BASE_URL is an env var, not a CLI flag for Hermes."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(base_url="http://custom:8080/v1")
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        # base_url should NOT appear as a CLI flag
        assert "--base-url" not in args
        assert "http://custom:8080/v1" not in args

    def test_base_url_set_in_env(self) -> None:
        """OPENAI_BASE_URL should be in the subprocess environment."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(base_url="http://custom:8080/v1")
        runtime = HermesCLIRuntime(config)
        env = runtime._build_env()
        assert env.get("OPENAI_BASE_URL") == "http://custom:8080/v1"

    def test_explicit_provider_suppresses_custom_endpoint_env(self) -> None:
        """base_url should override an explicit provider for custom endpoints."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            provider="anthropic",
            base_url="http://custom:8080/v1",
            api_key="token",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        env = runtime._build_env()
        assert "--provider" not in args
        assert env["OPENAI_BASE_URL"] == "http://custom:8080/v1"
        assert env["OPENAI_API_KEY"] == "token"

    def test_no_provider_keeps_custom_endpoint_env(self) -> None:
        """No explicit provider + custom endpoint → env vars set for auto-detect."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            base_url="http://custom:8080/v1",
            api_key="token",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        env = runtime._build_env()
        # No --provider flag, Hermes auto-detects from env
        assert "--provider" not in args
        assert env.get("OPENAI_BASE_URL") == "http://custom:8080/v1"
        assert env.get("OPENAI_API_KEY") == "token"

    def test_explicit_auto_provider_keeps_custom_endpoint_env(self) -> None:
        """base_url should also override an explicit auto provider."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            provider="auto",
            base_url="http://custom:8080/v1",
            api_key="token",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        env = runtime._build_env()
        assert "--provider" not in args
        assert env["OPENAI_BASE_URL"] == "http://custom:8080/v1"
        assert env["OPENAI_API_KEY"] == "token"

    def test_legacy_main_provider_keeps_custom_endpoint_env(self) -> None:
        """Legacy provider main should keep working for custom endpoints."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            provider="main",
            base_url="http://custom:8080/v1",
            api_key="token",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        env = runtime._build_env()
        assert "--provider" not in args
        assert env["OPENAI_BASE_URL"] == "http://custom:8080/v1"
        assert env["OPENAI_API_KEY"] == "token"

    def test_legacy_openai_provider_with_custom_endpoint_uses_env(self) -> None:
        """Legacy provider strings should not black-hole a custom endpoint."""
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            provider="openai",
            base_url="http://custom:8080/v1",
            api_key="token",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("test")
        env = runtime._build_env()
        assert "--provider" not in args
        assert env["OPENAI_BASE_URL"] == "http://custom:8080/v1"
        assert env["OPENAI_API_KEY"] == "token"
