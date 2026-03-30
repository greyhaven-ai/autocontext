"""Tests for AC-351: First-class Hermes runtime/provider support.

Covers the HermesCLIRuntime, config surface, build_client_from_settings
wiring, create_role_client wiring, and failure modes.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autocontext.config.settings import AppSettings, load_settings


def _settings(**overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "agent_provider": "deterministic",
        "knowledge_root": Path("/tmp/ac-hermes-rt-test"),
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# HermesCLIRuntime
# ---------------------------------------------------------------------------

class TestHermesCLIRuntime:
    def test_importable(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIRuntime

        assert HermesCLIRuntime is not None

    def test_config_defaults(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig

        config = HermesCLIConfig()
        assert config.hermes_command == "hermes"
        assert config.model == ""
        assert config.timeout == 120.0
        assert config.workspace == ""

    def test_generate_builds_correct_args(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            hermes_command="/usr/local/bin/hermes",
            model="hermes-3-llama-3.1-8b",
            timeout=60.0,
            workspace="/my/ws",
        )
        runtime = HermesCLIRuntime(config)
        args = runtime._build_args("Plan a strategy")
        assert "/usr/local/bin/hermes" in args[0] or args[0] == "/usr/local/bin/hermes"
        assert args[1] == "chat"
        assert "--query" in args
        assert "Plan a strategy" in args
        assert "--model" in args
        assert "hermes-3-llama-3.1-8b" in args

    def test_generate_uses_workspace_and_env_overrides(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(
            hermes_command="/usr/local/bin/hermes",
            model="hermes-3-llama-3.1-8b",
            workspace="/my/ws",
            base_url="http://localhost:8080/v1",
            api_key="no-key",
        )
        runtime = HermesCLIRuntime(config)

        completed = MagicMock(returncode=0, stdout="Hello from Hermes", stderr="")
        with patch("subprocess.run", return_value=completed) as mock_run:
            output = runtime.generate("Plan a strategy")

        assert output.text == "Hello from Hermes"
        call_args = mock_run.call_args
        args = call_args.args[0]
        assert args[:2] == ["/usr/local/bin/hermes", "chat"]
        assert "--query" in args
        # Hermes v0.5.0+: custom endpoints auto-detected via env vars,
        # --provider main was removed
        assert "--provider" not in args
        assert call_args.kwargs["cwd"] == "/my/ws"
        env = call_args.kwargs["env"]
        assert env["OPENAI_BASE_URL"] == "http://localhost:8080/v1"
        assert env["OPENAI_API_KEY"] == "no-key"

    def test_parse_output_plain_text(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIRuntime

        runtime = HermesCLIRuntime()
        output = runtime._parse_output("Hello from Hermes")
        assert output.text == "Hello from Hermes"

    def test_parse_output_json_response(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIRuntime

        runtime = HermesCLIRuntime()
        response = json.dumps({"response": "Strategy analysis complete.", "model": "hermes-3"})
        output = runtime._parse_output(response)
        assert "Strategy analysis" in output.text

    def test_parse_output_empty(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIRuntime

        runtime = HermesCLIRuntime()
        output = runtime._parse_output("")
        assert output.text == ""

    def test_available_when_binary_missing(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIConfig, HermesCLIRuntime

        config = HermesCLIConfig(hermes_command="nonexistent-hermes-binary-xyz")
        runtime = HermesCLIRuntime(config)
        assert runtime.available is False

    def test_revise_includes_feedback(self) -> None:
        from autocontext.runtimes.hermes_cli import HermesCLIRuntime

        runtime = HermesCLIRuntime()
        with patch.object(runtime, "_invoke", return_value=MagicMock(text="revised")) as mock_invoke:
            runtime.revise("original prompt", "old output", "do better")
        prompt_arg = mock_invoke.call_args[0][0]
        assert "old output" in prompt_arg
        assert "do better" in prompt_arg


# ---------------------------------------------------------------------------
# Config settings surface
# ---------------------------------------------------------------------------

class TestHermesConfigSettings:
    def test_hermes_settings_exist_on_app_settings(self) -> None:
        settings = _settings()
        assert hasattr(settings, "hermes_command")
        assert hasattr(settings, "hermes_model")
        assert hasattr(settings, "hermes_timeout")
        assert hasattr(settings, "hermes_workspace")
        assert hasattr(settings, "hermes_base_url")
        assert hasattr(settings, "hermes_api_key")

    def test_hermes_settings_defaults(self) -> None:
        settings = _settings()
        assert settings.hermes_command == "hermes"
        assert settings.hermes_model == ""
        assert settings.hermes_timeout == 120.0
        assert settings.hermes_workspace == ""
        assert settings.hermes_base_url == ""
        assert settings.hermes_api_key == ""

    def test_hermes_env_vars_load(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "hermes")
        monkeypatch.setenv("AUTOCONTEXT_HERMES_COMMAND", "/opt/hermes/bin/hermes")
        monkeypatch.setenv("AUTOCONTEXT_HERMES_MODEL", "hermes-3-llama-3.1-70b")
        monkeypatch.setenv("AUTOCONTEXT_HERMES_TIMEOUT", "90")
        monkeypatch.setenv("AUTOCONTEXT_HERMES_BASE_URL", "http://hermes.local:8080")
        settings = load_settings()
        assert settings.agent_provider == "hermes"
        assert settings.hermes_command == "/opt/hermes/bin/hermes"
        assert settings.hermes_model == "hermes-3-llama-3.1-70b"
        assert settings.hermes_timeout == 90.0
        assert settings.hermes_base_url == "http://hermes.local:8080"


# ---------------------------------------------------------------------------
# build_client_from_settings wiring
# ---------------------------------------------------------------------------

class TestHermesBuildClient:
    def test_build_client_accepts_hermes(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings

        settings = _settings(agent_provider="hermes")
        with patch("autocontext.runtimes.hermes_cli.HermesCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert client is not None

    def test_build_client_hermes_is_runtime_bridge(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings
        from autocontext.agents.provider_bridge import RuntimeBridgeClient

        settings = _settings(agent_provider="hermes")
        with patch("autocontext.runtimes.hermes_cli.HermesCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert isinstance(client, RuntimeBridgeClient)

    def test_build_client_hermes_passes_config(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings

        settings = _settings(
            agent_provider="hermes",
            hermes_command="/opt/hermes",
            hermes_model="hermes-3",
            hermes_timeout=45.0,
            hermes_workspace="/ws",
        )
        with patch("autocontext.runtimes.hermes_cli.HermesCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        config = MockRuntime.call_args[0][0]
        assert config.hermes_command == "/opt/hermes"
        assert config.model == "hermes-3"
        assert config.timeout == 45.0


# ---------------------------------------------------------------------------
# create_role_client wiring
# ---------------------------------------------------------------------------

class TestHermesRoleClient:
    def test_create_role_client_hermes(self) -> None:
        from autocontext.agents.provider_bridge import RuntimeBridgeClient, create_role_client

        settings = _settings(hermes_command="hermes")
        with patch("autocontext.runtimes.hermes_cli.HermesCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = create_role_client("hermes", settings)
        assert isinstance(client, RuntimeBridgeClient)


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

class TestHermesFailureModes:
    def test_unknown_provider_still_raises(self) -> None:
        from autocontext.agents.llm_client import build_client_from_settings

        settings = _settings(agent_provider="hermes-nonexistent")
        with pytest.raises(ValueError, match="unsupported agent provider"):
            build_client_from_settings(settings)

    def test_hermes_construction_succeeds_without_binary(self) -> None:
        """Client construction should not fail even if hermes binary is missing."""
        from autocontext.agents.llm_client import build_client_from_settings

        settings = _settings(agent_provider="hermes", hermes_command="nonexistent-hermes")
        client = build_client_from_settings(settings)
        assert client is not None
