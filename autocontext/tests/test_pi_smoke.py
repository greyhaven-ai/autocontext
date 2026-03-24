"""Smoke tests for AC-359: top-level Pi provider paths and documented examples.

Exercises the documented ``AUTOCONTEXT_AGENT_PROVIDER=pi`` and
``AUTOCONTEXT_AGENT_PROVIDER=pi-rpc`` paths through both construction-level
surfaces and a lightweight ``autoctx run`` path, so regressions in the live
CLI/runner/orchestrator entrypoint are caught as well.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from typer.testing import CliRunner

from autocontext.agents.llm_client import build_client_from_settings
from autocontext.agents.provider_bridge import RuntimeBridgeClient
from autocontext.cli import app
from autocontext.config.settings import AppSettings, load_settings

runner = CliRunner()


def _settings(**overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "agent_provider": "deterministic",
        "knowledge_root": Path("/tmp/ac-smoke-test"),
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


def _runner_settings(tmp_path: Path, **overrides: object) -> AppSettings:
    defaults: dict[str, object] = {
        "db_path": tmp_path / "runs" / "autocontext.sqlite3",
        "runs_root": tmp_path / "runs",
        "knowledge_root": tmp_path / "knowledge",
        "skills_root": tmp_path / "skills",
        "claude_skills_path": tmp_path / ".claude" / "skills",
        "event_stream_path": tmp_path / "runs" / "events.ndjson",
        "agent_provider": "deterministic",
        "judge_provider": "anthropic",
        "anthropic_api_key": "test-key",
        "session_reports_enabled": False,
        "cross_run_inheritance": False,
    }
    defaults.update(overrides)
    return AppSettings(**defaults)  # type: ignore[arg-type]


def _complete_smoke_generation(
    pipeline: Any,
    ctx: Any,
    *,
    resolved_clients: dict[str, Any],
) -> Any:
    orchestrator = pipeline._orchestrator
    sqlite = pipeline._sqlite
    competitor_client, _ = orchestrator.resolve_role_execution(
        "competitor",
        generation=ctx.generation,
        scenario_name=ctx.scenario_name,
    )
    analyst_client, _ = orchestrator.resolve_role_execution(
        "analyst",
        generation=ctx.generation,
        scenario_name=ctx.scenario_name,
    )
    resolved_clients["competitor"] = competitor_client
    resolved_clients["analyst"] = analyst_client
    sqlite.upsert_generation(
        ctx.run_id,
        ctx.generation,
        mean_score=0.72,
        best_score=0.72,
        elo=1012.0,
        wins=1,
        losses=0,
        gate_decision="advance",
        status="completed",
        scoring_backend=ctx.settings.scoring_backend,
        rating_uncertainty=55.0,
    )
    ctx.previous_best = 0.72
    ctx.challenger_elo = 1012.0
    ctx.challenger_uncertainty = 55.0
    ctx.gate_decision = "advance"
    return ctx


# ---------------------------------------------------------------------------
# Documented env var → load_settings → build_client round-trip
# ---------------------------------------------------------------------------

class TestPiEnvVarRoundTrip:
    """Verify the documented env var combinations load and produce valid clients."""

    def test_pi_cli_env_vars_load_and_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Documented: AUTOCONTEXT_AGENT_PROVIDER=pi + PI_COMMAND + PI_TIMEOUT."""
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "pi")
        monkeypatch.setenv("AUTOCONTEXT_PI_COMMAND", "pi")
        monkeypatch.setenv("AUTOCONTEXT_PI_TIMEOUT", "120")
        settings = load_settings()
        assert settings.agent_provider == "pi"
        assert settings.pi_command == "pi"
        assert settings.pi_timeout == 120.0

        with patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert isinstance(client, RuntimeBridgeClient)

    def test_pi_rpc_env_vars_load_and_build(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Documented: AUTOCONTEXT_AGENT_PROVIDER=pi-rpc + PI_RPC_ENDPOINT + PI_RPC_API_KEY."""
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "pi-rpc")
        monkeypatch.setenv("AUTOCONTEXT_PI_RPC_ENDPOINT", "http://localhost:3284")
        monkeypatch.setenv("AUTOCONTEXT_PI_RPC_API_KEY", "test-key")
        settings = load_settings()
        assert settings.agent_provider == "pi-rpc"
        assert settings.pi_rpc_endpoint == "http://localhost:3284"
        assert settings.pi_rpc_api_key == "test-key"

        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert isinstance(client, RuntimeBridgeClient)

    def test_pi_workspace_and_model_env_vars(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify optional Pi env vars are preserved through load_settings."""
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "pi")
        monkeypatch.setenv("AUTOCONTEXT_PI_WORKSPACE", "/my/workspace")
        monkeypatch.setenv("AUTOCONTEXT_PI_MODEL", "distilled-v2")
        settings = load_settings()
        assert settings.pi_workspace == "/my/workspace"
        assert settings.pi_model == "distilled-v2"

    def test_pi_rpc_session_persistence_env_var(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Verify PI_RPC_SESSION_PERSISTENCE coerces string to bool."""
        monkeypatch.setenv("AUTOCONTEXT_AGENT_PROVIDER", "pi-rpc")
        monkeypatch.setenv("AUTOCONTEXT_PI_RPC_SESSION_PERSISTENCE", "false")
        settings = load_settings()
        assert settings.pi_rpc_session_persistence is False


# ---------------------------------------------------------------------------
# Scenario-aware Pi model handoff
# ---------------------------------------------------------------------------

class TestPiScenarioHandoff:
    """Verify scenario-aware routing through the public entrypoint."""

    def test_scenario_context_triggers_registry_lookup(self) -> None:
        """When scenario_name is passed, resolve_pi_model should receive it."""
        settings = _settings(agent_provider="pi")
        with (
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime,
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
                return_value=SimpleNamespace(checkpoint_path="/models/grid-ctf/pi-v4"),
            ) as mock_resolve,
        ):
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings, scenario_name="grid_ctf")
        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["scenario"] == "grid_ctf"

    def test_manual_pi_model_overrides_registry(self) -> None:
        """When pi_model is set manually, it should be passed as manual_override."""
        settings = _settings(agent_provider="pi", pi_model="my-local-ckpt")
        with (
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime,
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
                return_value=None,
            ) as mock_resolve,
        ):
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.kwargs["manual_override"] == "my-local-ckpt"

    def test_no_scenario_no_model_skips_handoff(self) -> None:
        """Without scenario_name or pi_model, model handoff should not run."""
        settings = _settings(agent_provider="pi", pi_model="")
        with (
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime,
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
            ) as mock_resolve,
        ):
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings, scenario_name="")
        mock_resolve.assert_not_called()

    def test_handoff_failure_falls_back_gracefully(self) -> None:
        """If resolve_pi_model raises, client construction still succeeds."""
        settings = _settings(agent_provider="pi", pi_model="broken-ckpt")
        with (
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime,
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
                side_effect=FileNotFoundError("registry missing"),
            ),
        ):
            MockRuntime.return_value = MagicMock()
            client = build_client_from_settings(settings)
        assert isinstance(client, RuntimeBridgeClient)


# ---------------------------------------------------------------------------
# Per-role Pi overrides through create_role_client
# ---------------------------------------------------------------------------

class TestPiRoleOverride:
    """Verify Pi works as a per-role provider override (competitor_provider=pi)."""

    def test_create_role_client_pi(self) -> None:
        """create_role_client('pi', ...) should return a valid bridge client."""
        from autocontext.agents.provider_bridge import create_role_client

        settings = _settings(pi_command="pi")
        with patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = create_role_client("pi", settings)
        assert client is not None
        assert isinstance(client, RuntimeBridgeClient)

    def test_create_role_client_pi_rpc(self) -> None:
        """create_role_client('pi-rpc', ...) should return a valid bridge client."""
        from autocontext.agents.provider_bridge import create_role_client

        settings = _settings(pi_rpc_endpoint="http://localhost:3284")
        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            client = create_role_client("pi-rpc", settings)
        assert client is not None
        assert isinstance(client, RuntimeBridgeClient)


# ---------------------------------------------------------------------------
# Live autoctx run smoke path
# ---------------------------------------------------------------------------

class TestPiRunSmoke:
    """Verify the documented Pi setup survives the real CLI/runner entrypoint."""

    def test_autoctx_run_pi_cli_resolves_scenario_handoff(self, tmp_path: Path) -> None:
        settings = _runner_settings(
            tmp_path,
            agent_provider="pi",
            pi_command="pi",
        )
        resolved_clients: dict[str, Any] = {}

        def _run_generation(pipeline: Any, ctx: Any) -> Any:
            return _complete_smoke_generation(
                pipeline,
                ctx,
                resolved_clients=resolved_clients,
            )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch(
                "autocontext.providers.scenario_routing.resolve_pi_model",
                return_value=SimpleNamespace(checkpoint_path="/models/grid-ctf/pi-v4"),
            ) as mock_resolve,
            patch("autocontext.runtimes.pi_cli.PiCLIRuntime") as mock_runtime,
            patch("autocontext.loop.generation_pipeline.GenerationPipeline.run_generation", new=_run_generation),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_progress_report"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_aggregate_analytics"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_run_trace_artifacts"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_trace_grounded_reports"),
        ):
            mock_runtime.return_value = MagicMock()
            result = runner.invoke(app, ["run", "--scenario", "grid_ctf", "--gens", "1"])

        assert result.exit_code == 0, result.output
        assert "competitor" in resolved_clients
        assert "analyst" in resolved_clients
        assert any(
            call.kwargs.get("scenario") == "grid_ctf"
            for call in mock_resolve.call_args_list
        )
        assert any(
            (call.args[0].model if call.args else call.kwargs["config"].model) == "/models/grid-ctf/pi-v4"
            for call in mock_runtime.call_args_list
        )

    def test_autoctx_run_pi_rpc_uses_distinct_role_clients(self, tmp_path: Path) -> None:
        settings = _runner_settings(
            tmp_path,
            agent_provider="pi-rpc",
            pi_rpc_endpoint="http://localhost:3284",
        )
        resolved_clients: dict[str, Any] = {}
        runtime_instances = [
            MagicMock(name="shared-runtime"),
            MagicMock(name="competitor-runtime"),
            MagicMock(name="analyst-runtime"),
        ]

        def _run_generation(pipeline: Any, ctx: Any) -> Any:
            return _complete_smoke_generation(
                pipeline,
                ctx,
                resolved_clients=resolved_clients,
            )

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.runtimes.pi_rpc.PiRPCRuntime", side_effect=runtime_instances) as mock_runtime,
            patch("autocontext.loop.generation_pipeline.GenerationPipeline.run_generation", new=_run_generation),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_progress_report"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_aggregate_analytics"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_run_trace_artifacts"),
            patch("autocontext.loop.generation_runner.GenerationRunner._generate_trace_grounded_reports"),
        ):
            result = runner.invoke(app, ["run", "--scenario", "grid_ctf", "--gens", "1"])

        assert result.exit_code == 0, result.output
        competitor_client = resolved_clients["competitor"]
        analyst_client = resolved_clients["analyst"]
        assert competitor_client is not analyst_client
        assert competitor_client._runtime is runtime_instances[1]
        assert analyst_client._runtime is runtime_instances[2]
        assert mock_runtime.call_count >= 3


# ---------------------------------------------------------------------------
# Failure modes
# ---------------------------------------------------------------------------

class TestPiFailureModes:
    """Verify broken Pi setups produce intelligible errors."""

    def test_unknown_provider_error_message_is_useful(self) -> None:
        """Error message should list supported providers including pi."""
        settings = _settings(agent_provider="pipi")
        with pytest.raises(ValueError, match="unsupported agent provider"):
            build_client_from_settings(settings)

    def test_pi_rpc_uses_subprocess_not_http(self) -> None:
        """Pi RPC should use subprocess (stdin/stdout JSONL), not HTTP."""
        settings = _settings(agent_provider="pi-rpc")
        with patch("autocontext.runtimes.pi_rpc.PiRPCRuntime") as MockRuntime:
            MockRuntime.return_value = MagicMock()
            build_client_from_settings(settings)
        config = MockRuntime.call_args[0][0]
        assert config.pi_command == "pi"
        assert not hasattr(config, "endpoint") or getattr(config, "endpoint", "") == ""

    def test_pi_cli_runtime_unavailable_does_not_crash_construction(self) -> None:
        """Client construction should succeed even if pi binary is not on PATH."""
        settings = _settings(agent_provider="pi", pi_command="nonexistent-pi-binary")
        # Don't mock PiCLIRuntime — let it construct with missing binary
        # Construction should succeed; failure happens at generate() time
        client = build_client_from_settings(settings)
        assert isinstance(client, RuntimeBridgeClient)
