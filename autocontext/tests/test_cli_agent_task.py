"""Tests for AC-231: direct agent-task execution support in the Python CLI."""

from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext.cli import AgentTaskRunSummary, app
from autocontext.config.settings import AppSettings
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.storage.sqlite_store import SQLiteStore

runner = CliRunner()


class _MockAgentTask(AgentTaskInterface):
    def get_task_prompt(self, state: dict) -> str:
        return "Write a haiku about testing."

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        return AgentTaskResult(score=0.85, reasoning="Solid work", dimension_scores={"quality": 0.85})

    def get_rubric(self) -> str:
        return "Evaluate haiku quality."

    def initial_state(self, seed: int | None = None) -> dict:
        return {"topic": "testing"}

    def describe_task(self) -> str:
        return "Write a haiku about testing."

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return output


class _ContextTask(_MockAgentTask):
    def prepare_context(self, state: dict) -> dict:
        prepared = dict(state)
        prepared["prepared"] = True
        return prepared

    def validate_context(self, state: dict) -> list[str]:
        return [] if state.get("prepared") else ["missing prepared context"]


class _InvalidContextTask(_MockAgentTask):
    def validate_context(self, state: dict) -> list[str]:
        return ["missing required research brief"]


class _FakeProvider:
    def __init__(self, text: str = "initial output") -> None:
        self._text = text

    def complete(self, system_prompt: str, user_prompt: str, model: str | None = None, **_: object):
        return MagicMock(text=self._text, model=model)


class _FakeLoopResult:
    def __init__(self) -> None:
        self.best_score = 0.85
        self.best_output = "Tests pass in green\nCode refactored with care\nBugs fear the haiku"
        self.total_rounds = 3
        self.met_threshold = False
        self.termination_reason = "max_rounds"
        self.duration_ms = 1200
        self.judge_calls = 3
        self.judge_failures = 0
        self.best_round = 2
        self.rounds: list[object] = []
        self.dimension_trajectory = {"quality": [0.6, 0.75, 0.85]}
        self.total_internal_retries = 0


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        agent_provider="deterministic",
        judge_provider="anthropic",
        anthropic_api_key="test-key",
    )


class TestAgentTaskDetection:
    def test_run_detects_agent_task_and_skips_generation_runner(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "test-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
            patch("autocontext.cli._runner") as mock_runner,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "3"])

        assert result.exit_code == 0, result.output
        mock_runner.assert_not_called()

    def test_run_accepts_positional_scenario_and_iterations_alias(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "test-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
            patch("autocontext.cli._runner") as mock_runner,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(app, ["run", "mock_task", "--iterations", "3"])

        assert result.exit_code == 0, result.output
        mock_runner.assert_not_called()


class TestAgentTaskPersistence:
    def test_run_persists_agent_task_as_canonical_run_state(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _ContextTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch(
                "autocontext.cli._resolve_agent_task_runtime",
                return_value=(_FakeProvider("generated initial output"), "runtime-model"),
            ),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--run-id", "task-run-001", "--gens", "3"])

        assert result.exit_code == 0, result.output

        store = SQLiteStore(settings.db_path)
        with store.connect() as conn:
            run_row = conn.execute("SELECT * FROM runs WHERE run_id = ?", ("task-run-001",)).fetchone()
            gen_row = conn.execute(
                "SELECT * FROM generations WHERE run_id = ? AND generation_index = 1",
                ("task-run-001",),
            ).fetchone()
            outputs = conn.execute(
                "SELECT role, content FROM agent_outputs WHERE run_id = ? ORDER BY rowid ASC",
                ("task-run-001",),
            ).fetchall()

        assert run_row is not None
        assert run_row["scenario"] == "mock_task"
        assert run_row["executor_mode"] == "agent_task"
        assert run_row["agent_provider"] == "deterministic"
        assert gen_row is not None
        assert gen_row["status"] == "completed"
        assert gen_row["best_score"] == 0.85
        assert gen_row["gate_decision"] == "max_rounds"
        assert gen_row["duration_seconds"] is not None
        assert [(row["role"], row["content"]) for row in outputs] == [
            ("competitor_initial", "generated initial output"),
            ("competitor", _FakeLoopResult().best_output),
        ]

    def test_failure_marks_generation_failed(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.side_effect = RuntimeError("judge exploded")
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--run-id", "task-fail-001"])

        assert result.exit_code == 1
        err = json.loads(result.stderr.strip())
        assert "judge exploded" in err["error"]

        store = SQLiteStore(settings.db_path)
        with store.connect() as conn:
            gen_row = conn.execute(
                "SELECT status, gate_decision FROM generations WHERE run_id = ? AND generation_index = 1",
                ("task-fail-001",),
            ).fetchone()
        assert gen_row is not None
        assert gen_row["status"] == "failed"
        assert gen_row["gate_decision"] == "failed"


class TestAgentTaskRuntimeSelection:
    def test_run_uses_resolved_runtime_not_judge_provider(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")) as mock_runtime,
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(app, ["run", "--scenario", "mock_task"])

        assert result.exit_code == 0, result.output
        mock_runtime.assert_called_once_with(settings, "mock_task")


class TestAgentTaskJsonOutput:
    def test_json_output_is_structured(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = _FakeLoopResult()
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--run-id", "task-json-001"])

        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout.strip())
        assert data["run_id"] == "task-json-001"
        assert data["scenario"] == "mock_task"
        assert data["best_score"] == 0.85
        assert data["total_rounds"] == 3
        assert data["termination_reason"] == "max_rounds"
        assert "best_output" in data


class TestAgentTaskContextValidation:
    def test_invalid_context_fails_cleanly(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _InvalidContextTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
        ):
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task"])

        assert result.exit_code == 1
        data = json.loads(result.stderr.strip())
        assert "Context validation failed" in data["error"]


class TestAgentTaskServeRejection:
    def test_serve_mode_rejected_for_agent_tasks(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path)
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
        ):
            result = runner.invoke(app, ["run", "--serve", "--scenario", "mock_task"])

        assert result.exit_code == 2


class _EvaluatorGuardrailFailTask(_MockAgentTask):
    """Task whose judge reports a failed evaluator guardrail (AC-848).

    Mirrors what execution/task_runner.py::_process_task sees when
    ``config.judge_bias_probes_enabled`` (or ``judge_samples > 1``) triggers
    a live evaluator guardrail check on the best output.
    """

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        return AgentTaskResult(
            score=0.95,
            reasoning="judge liked it",
            dimension_scores={"quality": 0.95},
            evaluator_guardrail={
                "passed": False,
                "reason": "judge disagreement exceeded threshold",
                "violations": ["judge disagreement exceeded threshold"],
            },
        )


class _PreparedStateGuardrailTask(_ContextTask):
    """Guardrail evaluation must receive the prepared context state (AC-848).

    ``_ContextTask.prepare_context`` injects ``prepared=True``; a guardrail
    evaluated against the raw ``initial_state()`` would not see it. This task
    passes the guardrail only when the prepared state reaches it, so the test
    distinguishes prepared-state wiring from an ``initial_state()`` fallback.
    """

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        prepared = bool(state.get("prepared"))
        return AgentTaskResult(
            score=0.95,
            reasoning="checked state",
            dimension_scores={"quality": 0.95},
            evaluator_guardrail={
                "passed": prepared,
                "reason": "prepared context present" if prepared else "guardrail saw stale initial state",
                "violations": [] if prepared else ["guardrail saw stale initial state"],
            },
        )


class TestAgentTaskGuardrailParity:
    """AC-848: CLI path must apply the same guardrail-adjusted
    effective_met_threshold as execution/task_runner.py::_process_task."""

    def test_evaluator_guardrail_forces_met_threshold_false(self, tmp_path: Path) -> None:
        settings = _settings(tmp_path).model_copy(update={"judge_bias_probes_enabled": True})
        loop_result = _FakeLoopResult()
        loop_result.met_threshold = True
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _EvaluatorGuardrailFailTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = loop_result
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--run-id", "task-guardrail-001"])

        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout.strip())
        # The loop itself reported met_threshold=True, but the evaluator
        # guardrail failed, so the effective (guardrail-adjusted) result
        # must be False -- the same gate execution/task_runner.py applies.
        assert data["met_threshold"] is False

    def test_guardrail_veto_does_not_persist_threshold_met(self, tmp_path: Path) -> None:
        """A veto that flips met_threshold True->False must not leave the
        termination_reason/gate_decision claiming "threshold_met" (mirrors the
        ``"threshold_met" if effective else "max_rounds"`` derivation in
        execution/task_runner.py::_run_task_multi_generation)."""
        settings = _settings(tmp_path).model_copy(update={"judge_bias_probes_enabled": True})
        loop_result = _FakeLoopResult()
        loop_result.met_threshold = True
        loop_result.termination_reason = "threshold_met"
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _EvaluatorGuardrailFailTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = loop_result
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--run-id", "task-guardrail-veto-001"])

        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout.strip())
        assert data["met_threshold"] is False
        # Coherent termination: not still "threshold_met".
        assert data["termination_reason"] != "threshold_met"
        assert data["termination_reason"] == "max_rounds"

        # The persisted gate_decision must match the coherent termination.
        store = SQLiteStore(settings.db_path)
        with store.connect() as conn:
            gen_row = conn.execute(
                "SELECT gate_decision FROM generations WHERE run_id = ? AND generation_index = 1",
                ("task-guardrail-veto-001",),
            ).fetchone()
        assert gen_row is not None
        assert gen_row["gate_decision"] == "max_rounds"

    def test_guardrail_receives_prepared_state_not_initial_state(self, tmp_path: Path) -> None:
        """The evaluator guardrail must re-evaluate against the same prepared
        state the ImprovementLoop used, not a fresh ``task.initial_state()``."""
        settings = _settings(tmp_path).model_copy(update={"judge_bias_probes_enabled": True})
        loop_result = _FakeLoopResult()
        loop_result.met_threshold = True
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _PreparedStateGuardrailTask}),
            patch("autocontext.cli.load_settings", return_value=settings),
            patch("autocontext.cli._resolve_agent_task_runtime", return_value=(_FakeProvider(), "runtime-model")),
            patch("autocontext.cli.ImprovementLoop") as mock_loop,
        ):
            mock_loop.return_value.run.return_value = loop_result
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--run-id", "task-guardrail-prep-001"])

        assert result.exit_code == 0, result.stderr
        data = json.loads(result.stdout.strip())
        # The guardrail only passes when it received the prepared state; a
        # fallback to initial_state() would veto and flip this to False.
        assert data["met_threshold"] is True


class TestAgentTaskRunSummary:
    def test_summary_json_serializable(self) -> None:
        summary = AgentTaskRunSummary(
            run_id="task-001",
            scenario="mock_task",
            best_score=0.85,
            best_output="output",
            total_rounds=3,
            met_threshold=False,
            termination_reason="max_rounds",
        )
        data = json.loads(json.dumps(dataclasses.asdict(summary)))
        assert data["run_id"] == "task-001"
        assert data["best_score"] == 0.85
