from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings

runner = CliRunner()


class _RecordingClient:
    def __init__(self, text: str) -> None:
        self._text = text
        self.calls: list[dict[str, object]] = []

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> SimpleNamespace:
        self.calls.append({
            "model": model,
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature,
            "role": role,
        })
        return SimpleNamespace(text=self._text)


class _FakeSimulationEngine:
    def __init__(self, llm_fn, knowledge_root: Path) -> None:  # noqa: ANN001
        self._llm_fn = llm_fn
        self._knowledge_root = knowledge_root

    def run(self, **_: object) -> dict[str, object]:
        return {
            "status": "completed",
            "provider_text": self._llm_fn("architect-system", "architect-user"),
            "knowledge_root": str(self._knowledge_root),
        }


class _FakeOrchestrator:
    def __init__(self, client: _RecordingClient, model: str) -> None:
        self._client = client
        self._model = model
        self.calls: list[dict[str, object]] = []

    def resolve_role_execution(
        self,
        role: str,
        *,
        generation: int,
        retry_count: int = 0,
        is_plateau: bool = False,
        scenario_name: str = "",
    ) -> tuple[_RecordingClient, str]:
        self.calls.append({
            "role": role,
            "generation": generation,
            "retry_count": retry_count,
            "is_plateau": is_plateau,
            "scenario_name": scenario_name,
        })
        return self._client, self._model


def _settings(tmp_path: Path, **overrides: object) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider=str(overrides.get("agent_provider", "pi")),
        architect_provider=str(overrides.get("architect_provider", "")),
        judge_provider=str(overrides.get("judge_provider", "anthropic")),
        model_architect=str(overrides.get("model_architect", "claude-opus-4-6")),
        agent_default_model=str(overrides.get("agent_default_model", "gpt-4o")),
        pi_model=str(overrides.get("pi_model", "")),
    )


class TestSimulateRuntimeResolution:
    def test_simulate_uses_architect_role_runtime_instead_of_judge_provider(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path, agent_provider="anthropic", architect_provider="pi", judge_provider="anthropic")
        client = _RecordingClient(text='{"spec": "from-runtime"}')
        orchestrator = _FakeOrchestrator(client, "pi-architect")

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=object()),
            patch("autocontext.cli._artifacts_from_settings", return_value=object()),
            patch("autocontext.cli.AgentOrchestrator.from_settings", return_value=orchestrator) as mock_from_settings,
            patch(
                "autocontext.agents.llm_client.build_client_from_settings",
                side_effect=AssertionError("simulate should resolve through architect role routing"),
            ),
            patch("autocontext.simulation.engine.SimulationEngine", _FakeSimulationEngine),
            patch(
                "autocontext.providers.registry.get_provider",
                side_effect=AssertionError("simulate should not resolve the judge provider"),
            ),
        ):
            result = runner.invoke(app, ["simulate", "--description", "runtime test", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["provider_text"] == '{"spec": "from-runtime"}'
        mock_from_settings.assert_called_once()
        assert orchestrator.calls == [{
            "role": "architect",
            "generation": 1,
            "retry_count": 0,
            "is_plateau": False,
            "scenario_name": "",
        }]
        assert client.calls == [{
            "model": "pi-architect",
            "prompt": "architect-system\n\narchitect-user",
            "max_tokens": 4096,
            "temperature": 0.0,
            "role": "architect",
        }]

    def test_simulate_uses_resolved_architect_model(self, tmp_path: Path) -> None:
        settings = _settings(
            tmp_path,
            agent_provider="ollama",
            agent_default_model="llama3.1",
            model_architect="claude-opus-4-6",
        )
        client = _RecordingClient(text='{"spec": "ollama-runtime"}')
        orchestrator = _FakeOrchestrator(client, "llama3.1")

        with (
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._sqlite_from_settings", return_value=object()),
            patch("autocontext.cli._artifacts_from_settings", return_value=object()),
            patch("autocontext.cli.AgentOrchestrator.from_settings", return_value=orchestrator),
            patch("autocontext.simulation.engine.SimulationEngine", _FakeSimulationEngine),
            patch(
                "autocontext.providers.registry.get_provider",
                side_effect=AssertionError("simulate should not use judge-provider model selection"),
            ),
        ):
            result = runner.invoke(app, ["simulate", "--description", "ollama test", "--json"])

        assert result.exit_code == 0, result.output
        payload = json.loads(result.stdout)
        assert payload["provider_text"] == '{"spec": "ollama-runtime"}'
        assert client.calls[0]["model"] == "llama3.1"
        assert client.calls[0]["role"] == "architect"
