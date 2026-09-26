"""AC-1047: ``autoctx run --run-id`` on the agent-task path.

Agent-task runs are one-shot. A requested run id that already exists is refused
before the first provider call and before any write, so another run's
generation 1, agent outputs and status survive. Every agent-task run the path
starts ends ``completed`` or ``failed`` instead of staying ``running``.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import patch

import pytest
from click.testing import Result
from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.config.settings import AppSettings
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"
runner = CliRunner()


class _StubTask(AgentTaskInterface):
    """Scores every output 0.95, so a one-round loop meets the 0.9 threshold."""

    def __init__(self) -> None:
        self.evaluations = 0

    def get_task_prompt(self, state: dict) -> str:
        return "Write a haiku about testing."

    def evaluate_output(self, output: str, state: dict, **_: Any) -> AgentTaskResult:
        self.evaluations += 1
        return AgentTaskResult(score=0.95, reasoning="solid", dimension_scores={"quality": 0.95})

    def get_rubric(self) -> str:
        return "Evaluate haiku quality."

    def initial_state(self, seed: int | None = None) -> dict:
        return {"topic": "testing"}

    def describe_task(self) -> str:
        return "Write a haiku about testing."

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return output


class _FailingEvaluationTask(_StubTask):
    def evaluate_output(self, output: str, state: dict, **_: Any) -> AgentTaskResult:
        raise RuntimeError("judge exploded")


class _InterruptedEvaluationTask(_StubTask):
    def evaluate_output(self, output: str, state: dict, **_: Any) -> AgentTaskResult:
        raise KeyboardInterrupt


class _FailingGuardrailTask(_StubTask):
    """The loop's evaluation succeeds; the guardrail's re-evaluation raises."""

    def evaluate_output(self, output: str, state: dict, **kwargs: Any) -> AgentTaskResult:
        if self.evaluations >= 1:
            raise RuntimeError("guardrail judge exploded")
        return super().evaluate_output(output, state, **kwargs)


class _CountingProvider:
    """Stands in for the external LLM provider and counts completions."""

    def __init__(self) -> None:
        self.calls = 0

    def complete(self, system_prompt: str, user_prompt: str, model: str | None = None, **_: object) -> SimpleNamespace:
        self.calls += 1
        return SimpleNamespace(text="initial output", model=model)


def _settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        audit_log_path=tmp_path / "runs" / "audit.ndjson",
        agent_provider="deterministic",
        **overrides,
    )


def _store(settings: AppSettings) -> SQLiteStore:
    store = SQLiteStore(settings.db_path)
    store.migrate(MIGRATIONS)
    return store


def _invoke(settings: AppSettings, task_cls: type[AgentTaskInterface], provider: _CountingProvider, run_id: str) -> Result:
    with (
        patch.dict("autocontext.cli.SCENARIO_REGISTRY", {"stub_task": task_cls}),
        patch("autocontext.cli.load_settings", return_value=settings),
        patch("autocontext.cli._resolve_agent_task_runtime", return_value=(provider, "stub-model")),
    ):
        return runner.invoke(app, ["run", "stub_task", "--iterations", "1", "--run-id", run_id, "--json", "--skip-preflight"])


def _snapshot(store: SQLiteStore, run_id: str) -> tuple[dict[str, Any] | None, list[dict[str, Any]], list[tuple[Any, ...]]]:
    """Every row the agent-task path could write for ``run_id``."""
    with store.connection() as conn:
        run = conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        generations = conn.execute("SELECT * FROM generations WHERE run_id = ? ORDER BY generation_index", (run_id,)).fetchall()
        outputs = conn.execute(
            "SELECT generation_index, role, content FROM agent_outputs WHERE run_id = ? ORDER BY rowid", (run_id,)
        ).fetchall()
    return (dict(run) if run else None, [dict(row) for row in generations], [tuple(row) for row in outputs])


def _error(result: Result) -> str:
    return str(json.loads(result.stderr.strip())["error"])


def _run_status(store: SQLiteStore, run_id: str) -> str:
    run = store.get_run(run_id)
    assert run is not None
    return str(run["status"])


def _generation_status(store: SQLiteStore, run_id: str) -> str:
    generation = store.get_generation(run_id, 1)
    assert generation is not None
    return str(generation["status"])


class TestAgentTaskRunIdRefusal:
    def test_refuses_generation_loop_run_of_another_scenario(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches `run <agent-task> --run-id X` overwriting generation 1 of a completed grid_ctf run and exiting 0."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)
        store = _store(settings)
        store.create_run("shared_id", "grid_ctf", 2, "local")
        store.upsert_generation(
            "shared_id",
            1,
            mean_score=0.7345,
            best_score=0.7588,
            elo=1021.46,
            wins=3,
            losses=0,
            gate_decision="advance",
            status="completed",
        )
        store.append_agent_output("shared_id", 1, "competitor", '{"aggression": 0.5}')
        store.mark_run_completed("shared_id")
        before = _snapshot(store, "shared_id")
        provider = _CountingProvider()

        result = _invoke(settings, _StubTask, provider, "shared_id")

        assert result.exit_code == 1, result.output
        assert "grid_ctf" in _error(result)
        assert _snapshot(store, "shared_id") == before
        assert provider.calls == 0

    def test_refuses_same_scenario_run_from_another_executor_mode(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches the agent-task path writing into a package-import run that shares its scenario name."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)
        store = _store(settings)
        store.create_run("imported_id", "stub_task", 1, "import", agent_provider="package")
        store.upsert_generation(
            "imported_id",
            1,
            mean_score=0.8,
            best_score=0.8,
            elo=1500.0,
            wins=0,
            losses=0,
            gate_decision="accepted",
            status="completed",
        )
        store.mark_run_completed("imported_id")
        before = _snapshot(store, "imported_id")
        provider = _CountingProvider()

        result = _invoke(settings, _StubTask, provider, "imported_id")

        assert result.exit_code == 1, result.output
        assert "agent-task" in _error(result)
        assert _snapshot(store, "imported_id") == before
        assert provider.calls == 0

    def test_refuses_stopped_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a stopped (terminal) run being written into and completed by an agent-task run."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)
        store = _store(settings)
        store.create_run("stopped_id", "stub_task", 1, "agent_task")
        assert store.mark_run_stopped("stopped_id")
        before = _snapshot(store, "stopped_id")
        provider = _CountingProvider()

        result = _invoke(settings, _StubTask, provider, "stopped_id")

        assert result.exit_code == 1, result.output
        assert "stopped" in _error(result)
        assert _snapshot(store, "stopped_id") == before
        assert provider.calls == 0

    def test_refuses_rerun_of_a_completed_agent_task_run(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a same-scenario rerun overwriting its own generation 1 and appending duplicate agent outputs."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)
        first = _invoke(settings, _StubTask, _CountingProvider(), "fresh_task")
        assert first.exit_code == 0, first.output
        store = _store(settings)
        before = _snapshot(store, "fresh_task")
        provider = _CountingProvider()

        result = _invoke(settings, _StubTask, provider, "fresh_task")

        assert result.exit_code == 1, result.output
        assert "already exists" in _error(result)
        assert _snapshot(store, "fresh_task") == before
        assert provider.calls == 0

    @pytest.mark.parametrize("status", ["running", "failed"])
    def test_refuses_rerun_of_an_unfinished_agent_task_run(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, status: str
    ) -> None:
        """Pins the one-shot decision: a failed or still-running agent-task run is not re-executed under its id."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)
        store = _store(settings)
        store.create_run("task_id", "stub_task", 1, "agent_task")
        store.upsert_generation(
            "task_id",
            1,
            mean_score=0.0,
            best_score=0.0,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision=status,
            status=status,
        )
        if status == "failed":
            store.mark_run_failed("task_id")
        before = _snapshot(store, "task_id")
        provider = _CountingProvider()

        result = _invoke(settings, _StubTask, provider, "task_id")

        assert result.exit_code == 1, result.output
        assert "already exists" in _error(result)
        assert _snapshot(store, "task_id") == before
        assert provider.calls == 0


class TestAgentTaskTerminalStatus:
    def test_successful_run_is_marked_completed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a finished agent-task run reporting `running` forever in the runs row and `autoctx status`."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)

        result = _invoke(settings, _StubTask, _CountingProvider(), "fresh_task")

        assert result.exit_code == 0, result.output
        store = _store(settings)
        assert _run_status(store, "fresh_task") == "completed"
        assert _generation_status(store, "fresh_task") == "completed"
        with patch("autocontext.cli.load_settings", return_value=settings):
            status = runner.invoke(app, ["status", "fresh_task", "--json"])
        assert status.exit_code == 0, status.output
        assert json.loads(status.stdout)["run"]["status"] == "completed"

    def test_failed_evaluation_marks_run_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches a run whose evaluation raised staying `running` while only generation 1 is marked failed."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)

        result = _invoke(settings, _FailingEvaluationTask, _CountingProvider(), "fail_task")

        assert result.exit_code == 1, result.output
        assert "judge exploded" in _error(result)
        store = _store(settings)
        assert _run_status(store, "fail_task") == "failed"
        assert _generation_status(store, "fail_task") == "failed"

    def test_failed_guardrail_marks_run_and_generation_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches the post-loop guardrail raising outside the try and leaving generation 1 and the run `running`."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path, judge_bias_probes_enabled=True)

        result = _invoke(settings, _FailingGuardrailTask, _CountingProvider(), "guardrail_task")

        assert result.exit_code == 1, result.output
        assert "guardrail judge exploded" in _error(result)
        store = _store(settings)
        assert _run_status(store, "guardrail_task") == "failed"
        assert _generation_status(store, "guardrail_task") == "failed"

    def test_interrupted_run_is_marked_failed(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Catches Ctrl-C leaving an agent-task run `running` forever, since `autoctx resume` refuses agent-task runs."""
        monkeypatch.chdir(tmp_path)
        settings = _settings(tmp_path)

        result = _invoke(settings, _InterruptedEvaluationTask, _CountingProvider(), "interrupted_task")

        assert result.exit_code == 1, result.output
        assert _error(result) == "run interrupted"
        store = _store(settings)
        assert _run_status(store, "interrupted_task") == "failed"
        assert _generation_status(store, "interrupted_task") == "failed"
