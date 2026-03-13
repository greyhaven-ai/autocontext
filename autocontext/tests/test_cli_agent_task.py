"""Tests for AC-231: direct agent-task execution support in the Python CLI."""
from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

from typer.testing import CliRunner

from autocontext.cli import app
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

runner = CliRunner()


# ---------------------------------------------------------------------------
# Helpers: mock agent-task scenario
# ---------------------------------------------------------------------------


class _MockAgentTask(AgentTaskInterface):
    """Minimal AgentTaskInterface for CLI routing tests."""

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
        return output  # No actual revision


def _mock_improvement_result() -> MagicMock:
    """Return a mock ImprovementResult with all expected fields."""
    result = MagicMock()
    result.best_score = 0.85
    result.best_output = "Tests pass in green\nCode refactored with care\nBugs fear the haiku"
    result.total_rounds = 3
    result.met_threshold = False
    result.termination_reason = "max_rounds"
    result.duration_ms = 1200
    result.judge_calls = 3
    result.judge_failures = 0
    result.best_round = 2
    result.rounds = []
    result.dimension_trajectory = {"quality": [0.6, 0.75, 0.85]}
    result.total_internal_retries = 0
    return result


# ---------------------------------------------------------------------------
# 1. Detection: agent-task scenarios route to improvement loop, not GenerationRunner
# ---------------------------------------------------------------------------


class TestAgentTaskDetection:
    def test_run_detects_agent_task_and_does_not_use_generation_runner(self) -> None:
        """When scenario is AgentTaskInterface, should NOT call GenerationRunner.run()."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
            patch("autocontext.cli._runner") as mock_runner_fn,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "3"])

        # GenerationRunner should NOT have been called
        mock_runner_fn.assert_not_called()
        assert result.exit_code == 0, result.output

    def test_game_scenario_still_uses_generation_runner(self) -> None:
        """Game scenarios (ScenarioInterface) should still route to GenerationRunner."""
        from autocontext.loop.generation_runner import RunSummary
        from autocontext.scenarios.base import ScenarioInterface

        class _MockGameScenario(ScenarioInterface):
            name = "mock_game"

            def execute_match(self, *a, **kw):  # type: ignore[override]
                pass

            def describe_rules(self) -> str:
                return "mock"

            def describe_strategy_format(self) -> str:
                return "{}"

            def default_strategy(self) -> dict:
                return {}

            def seed_tools(self) -> dict[str, str]:
                return {}

        mock_summary = RunSummary(
            run_id="game-run",
            scenario="mock_game",
            generations_executed=1,
            best_score=0.5,
            current_elo=1000.0,
        )
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.return_value = mock_summary

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_game": _MockGameScenario}),
            patch("autocontext.cli._runner", return_value=mock_runner_instance),
        ):
            result = runner.invoke(app, ["run", "--scenario", "mock_game", "--gens", "1"])

        assert result.exit_code == 0, result.output
        mock_runner_instance.run.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Successful execution
# ---------------------------------------------------------------------------


class TestAgentTaskExecution:
    def test_run_agent_task_produces_result(self) -> None:
        """autoctx run --scenario <agent-task> should produce a real result."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial haiku output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "3"])

        assert result.exit_code == 0, result.output
        assert "0.85" in result.output  # best_score should appear

    def test_run_agent_task_gens_maps_to_max_rounds(self) -> None:
        """--gens should be used as max_rounds for the ImprovementLoop."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "7"])

        assert result.exit_code == 0, result.output
        MockLoop.assert_called_once()
        call_kwargs = MockLoop.call_args
        assert call_kwargs.kwargs.get("max_rounds") == 7 or call_kwargs[1].get("max_rounds") == 7

    def test_run_agent_task_generates_initial_output(self) -> None:
        """Should generate initial output via provider before running improvement loop."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="generated initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "1"])

        assert result.exit_code == 0, result.output
        mock_provider.complete.assert_called_once()
        # The initial_output passed to loop.run should be "generated initial output"
        loop_run_call = MockLoop.return_value.run.call_args
        assert loop_run_call.kwargs.get("initial_output") == "generated initial output" or \
               loop_run_call[1].get("initial_output") == "generated initial output"

    def test_run_agent_task_with_run_id(self) -> None:
        """--run-id should be preserved in the output."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(
                app, ["run", "--scenario", "mock_task", "--gens", "1", "--run-id", "my-task-run"]
            )

        assert result.exit_code == 0, result.output
        assert "my-task-run" in result.output


# ---------------------------------------------------------------------------
# 3. JSON output contract
# ---------------------------------------------------------------------------


class TestAgentTaskJsonOutput:
    def test_json_output_has_required_fields(self) -> None:
        """--json output for agent tasks should have all required fields."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--gens", "3"])

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        required_keys = {"run_id", "scenario", "best_score", "best_output", "total_rounds", "met_threshold"}
        assert required_keys <= set(data.keys()), f"Missing keys: {required_keys - set(data.keys())}"

    def test_json_output_values_correct(self) -> None:
        """--json output values should match the ImprovementResult."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(
                app, ["run", "--json", "--scenario", "mock_task", "--gens", "3", "--run-id", "task-json-001"]
            )

        assert result.exit_code == 0, result.output
        data = json.loads(result.output.strip())
        assert data["run_id"] == "task-json-001"
        assert data["scenario"] == "mock_task"
        assert data["best_score"] == 0.85
        assert data["total_rounds"] == 3
        assert data["met_threshold"] is False
        assert data["termination_reason"] == "max_rounds"

    def test_json_output_includes_best_output(self) -> None:
        """--json should include the best_output text."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--gens", "1"])

        data = json.loads(result.output.strip())
        assert "best_output" in data
        assert "haiku" in data["best_output"].lower()


# ---------------------------------------------------------------------------
# 4. Human-readable output
# ---------------------------------------------------------------------------


class TestAgentTaskHumanOutput:
    def test_human_output_shows_table(self) -> None:
        """Non-JSON output should display a summary table."""
        mock_provider = MagicMock()
        mock_provider.complete.return_value = MagicMock(text="initial output")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
            patch("autocontext.cli.ImprovementLoop") as MockLoop,
        ):
            MockLoop.return_value.run.return_value = _mock_improvement_result()
            result = runner.invoke(app, ["run", "--scenario", "mock_task", "--gens", "3"])

        assert result.exit_code == 0, result.output
        # Should contain key info in human-readable form
        assert "mock_task" in result.output
        assert "0.85" in result.output


# ---------------------------------------------------------------------------
# 5. Error handling
# ---------------------------------------------------------------------------


class TestAgentTaskErrors:
    def test_json_error_on_agent_task_failure(self) -> None:
        """--json errors in agent-task path should emit structured stderr."""
        mock_provider = MagicMock()
        mock_provider.complete.side_effect = RuntimeError("provider unavailable")

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
        ):
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--gens", "1"])

        assert result.exit_code == 1
        error_data = json.loads(result.stderr.strip())
        assert "error" in error_data

    def test_keyboard_interrupt_handled(self) -> None:
        """KeyboardInterrupt during agent-task should exit cleanly."""
        mock_provider = MagicMock()
        mock_provider.complete.side_effect = KeyboardInterrupt()

        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
            patch("autocontext.cli._get_provider_for_agent_task", return_value=mock_provider),
        ):
            result = runner.invoke(app, ["run", "--json", "--scenario", "mock_task", "--gens", "1"])

        assert result.exit_code == 1

    def test_unknown_scenario_errors_clearly(self) -> None:
        """Unknown scenario should fail with actionable guidance."""
        mock_runner_instance = MagicMock()
        mock_runner_instance.run.side_effect = ValueError(
            "Unknown scenario 'nonexistent'. Supported: grid_ctf, othello"
        )

        with patch("autocontext.cli._runner", return_value=mock_runner_instance):
            result = runner.invoke(app, ["run", "--json", "--scenario", "nonexistent", "--gens", "1"])

        assert result.exit_code == 1

    def test_serve_mode_rejected_for_agent_tasks(self) -> None:
        """--serve is not supported for agent-task scenarios."""
        with (
            patch("autocontext.cli.SCENARIO_REGISTRY", {"mock_task": _MockAgentTask}),
            patch("autocontext.cli.load_settings", return_value=MagicMock()),
        ):
            result = runner.invoke(app, ["run", "--serve", "--scenario", "mock_task", "--gens", "1"])

        # Should reject --serve for agent tasks
        assert result.exit_code != 0


# ---------------------------------------------------------------------------
# 6. AgentTaskRunSummary dataclass
# ---------------------------------------------------------------------------


class TestAgentTaskRunSummary:
    def test_summary_dataclass_fields(self) -> None:
        """AgentTaskRunSummary should have the expected fields."""
        from autocontext.cli import AgentTaskRunSummary

        summary = AgentTaskRunSummary(
            run_id="test-001",
            scenario="my_task",
            best_score=0.92,
            best_output="some output",
            total_rounds=5,
            met_threshold=True,
            termination_reason="threshold_met",
        )
        assert summary.run_id == "test-001"
        assert summary.scenario == "my_task"
        assert summary.best_score == 0.92
        assert summary.best_output == "some output"
        assert summary.total_rounds == 5
        assert summary.met_threshold is True
        assert summary.termination_reason == "threshold_met"

    def test_summary_json_serializable(self) -> None:
        """AgentTaskRunSummary should be serializable via dataclasses.asdict."""
        import dataclasses

        from autocontext.cli import AgentTaskRunSummary

        summary = AgentTaskRunSummary(
            run_id="test-002",
            scenario="my_task",
            best_score=0.75,
            best_output="output text",
            total_rounds=3,
            met_threshold=False,
            termination_reason="max_rounds",
        )
        data = dataclasses.asdict(summary)
        # Should be JSON-serializable
        serialized = json.dumps(data)
        parsed = json.loads(serialized)
        assert parsed["run_id"] == "test-002"
        assert parsed["best_score"] == 0.75
