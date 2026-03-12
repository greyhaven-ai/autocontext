"""Tests for AC-198: Staged validation runner with concrete stages and cost tracking.

Tests each concrete stage (Syntax, Contract, Deterministic, EdgeCase, EvaluationReady),
the ValidationRunner with early-exit, and ValidationMetrics with per-stage rejection counts.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.harness.validation import StageResult, StageStatus, ValidationPipeline
from autocontext.harness.validation.stages import (
    ContractStage,
    DeterministicStage,
    EdgeCaseStage,
    EvaluationReadyStage,
    SyntaxStage,
    ValidationMetrics,
    ValidationRunner,
    default_pipeline,
)

# ── SyntaxStage tests ────────────────────────────────────────────────────


class TestSyntaxStage:
    def test_valid_json_strategy_passes(self) -> None:
        stage = SyntaxStage(order=0)
        result = stage.run(candidate={"action": "move", "x": 1}, scenario=None)
        assert result.passed is True

    def test_valid_python_code_passes(self) -> None:
        stage = SyntaxStage(order=0)
        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        result = stage.run(candidate=code, scenario=None)
        assert result.passed is True

    def test_invalid_python_code_fails(self) -> None:
        stage = SyntaxStage(order=0)
        result = stage.run(candidate="def foo(:\n    pass", scenario=None)
        assert result.passed is False
        assert result.error_code == "syntax_error"

    def test_none_candidate_fails(self) -> None:
        stage = SyntaxStage(order=0)
        result = stage.run(candidate=None, scenario=None)
        assert result.passed is False
        assert result.error_code == "invalid_type"

    def test_empty_dict_passes(self) -> None:
        stage = SyntaxStage(order=0)
        result = stage.run(candidate={}, scenario=None)
        assert result.passed is True

    def test_empty_string_passes(self) -> None:
        """Empty string is valid Python (no-op)."""
        stage = SyntaxStage(order=0)
        result = stage.run(candidate="", scenario=None)
        assert result.passed is True

    def test_list_candidate_passes(self) -> None:
        """Lists are structurally valid."""
        stage = SyntaxStage(order=0)
        result = stage.run(candidate=[1, 2, 3], scenario=None)
        assert result.passed is True

    def test_ast_unsafe_code_fails(self) -> None:
        """Code with AST safety violations should fail at syntax stage."""
        stage = SyntaxStage(order=0)
        code = "import os\nos.system('rm -rf /')\n"
        result = stage.run(candidate=code, scenario=None)
        assert result.passed is False
        assert result.error_code == "ast_safety"


# ── ContractStage tests ──────────────────────────────────────────────────


class TestContractStage:
    def test_dict_strategy_with_matching_scenario_passes(self) -> None:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (True, "")
        scenario.initial_state.return_value = {"board": []}
        stage = ContractStage(order=1)
        result = stage.run(candidate={"action": "move"}, scenario=scenario)
        assert result.passed is True

    def test_dict_strategy_failing_scenario_validation(self) -> None:
        scenario = MagicMock()
        scenario.validate_actions.return_value = (False, "invalid move direction")
        scenario.initial_state.return_value = {"board": []}
        stage = ContractStage(order=1)
        result = stage.run(candidate={"action": "fly"}, scenario=scenario)
        assert result.passed is False
        assert result.error_code == "contract_violation"
        assert "invalid move direction" in (result.error or "")

    def test_code_candidate_must_define_choose_action(self) -> None:
        stage = ContractStage(order=1)
        code = "def helper(): pass\n"
        result = stage.run(candidate=code, scenario=None)
        assert result.passed is False
        assert result.error_code == "missing_entry_point"

    def test_code_candidate_with_choose_action_passes(self) -> None:
        stage = ContractStage(order=1)
        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        result = stage.run(candidate=code, scenario=None)
        assert result.passed is True

    def test_no_scenario_dict_candidate_passes(self) -> None:
        """Without a scenario, dict candidates pass contract stage."""
        stage = ContractStage(order=1)
        result = stage.run(candidate={"action": "move"}, scenario=None)
        assert result.passed is True


# ── DeterministicStage tests ─────────────────────────────────────────────


class TestDeterministicStage:
    def test_consistent_dict_strategy_passes(self) -> None:
        """Dict strategies are inherently deterministic."""
        stage = DeterministicStage(order=2)
        result = stage.run(candidate={"action": "move"}, scenario=None)
        assert result.passed is True

    def test_deterministic_code_passes(self) -> None:
        stage = DeterministicStage(order=2)
        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        scenario = MagicMock()
        scenario.initial_state.return_value = {"board": []}
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is True

    def test_nondeterministic_code_fails(self) -> None:
        stage = DeterministicStage(order=2)
        code = (
            "import random\n"
            "def choose_action(state):\n"
            "    return {'action': random.choice(['a', 'b'])}\n"
        )
        # Non-deterministic code should either fail AST safety or produce
        # inconsistent results — either way, it should not pass.
        # Since `random` import is blocked by AST safety, this tests
        # the stage's handling of execution failures.
        scenario = MagicMock()
        scenario.initial_state.return_value = {}
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is False

    def test_no_scenario_code_skips(self) -> None:
        """Without a scenario, code determinism check is skipped."""
        stage = DeterministicStage(order=2)
        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        result = stage.run(candidate=code, scenario=None)
        assert result.status is StageStatus.SKIPPED

    def test_timeout_while_executing_code_fails_fast(self) -> None:
        stage = DeterministicStage(order=2, timeout_seconds=0.01)
        code = "def choose_action(state):\n    while True:\n        pass\n"
        scenario = MagicMock()
        scenario.initial_state.return_value = {}
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is False
        assert result.error_code == "timeout"


# ── EdgeCaseStage tests ──────────────────────────────────────────────────


class TestEdgeCaseStage:
    def test_skipped_when_no_edge_fixtures(self) -> None:
        """Stage gracefully skips when scenario has no edge fixtures."""
        scenario = MagicMock(spec=[])  # no get_edge_fixtures attribute
        stage = EdgeCaseStage(order=3)
        result = stage.run(candidate={"action": "move"}, scenario=scenario)
        assert result.status is StageStatus.SKIPPED

    def test_skipped_when_no_scenario(self) -> None:
        stage = EdgeCaseStage(order=3)
        result = stage.run(candidate={"action": "move"}, scenario=None)
        assert result.status is StageStatus.SKIPPED

    def test_passes_all_edge_fixtures(self) -> None:
        scenario = MagicMock()
        scenario.get_edge_fixtures.return_value = [
            {"state": {"board": "empty"}, "expected_valid": True},
            {"state": {"board": "full"}, "expected_valid": True},
        ]
        scenario.validate_actions.return_value = (True, "")
        stage = EdgeCaseStage(order=3)
        result = stage.run(candidate={"action": "move"}, scenario=scenario)
        assert result.passed is True

    def test_fails_on_edge_fixture(self) -> None:
        scenario = MagicMock()
        scenario.get_edge_fixtures.return_value = [
            {"state": {"board": "impossible"}, "expected_valid": False},
        ]
        scenario.validate_actions.return_value = (True, "")  # incorrectly passes
        stage = EdgeCaseStage(order=3)
        result = stage.run(candidate={"action": "invalid"}, scenario=scenario)
        # If scenario says valid but fixture says expected_valid=False, that's a mismatch
        assert result.passed is False
        assert result.error_code == "edge_case_mismatch"

    def test_code_candidate_executes_against_edge_fixtures(self) -> None:
        scenario = MagicMock()
        scenario.get_edge_fixtures.return_value = [
            {"state": {"allowed": True}, "expected_valid": True},
            {"state": {"allowed": False}, "expected_valid": False},
        ]

        def validate_actions(state: dict[str, bool], _player_id: str, actions: dict[str, str]) -> tuple[bool, str]:
            if state["allowed"]:
                return (actions.get("action") == "move", "expected move")
            return (False, "blocked" if actions.get("action") == "invalid" else "expected invalid")

        scenario.validate_actions.side_effect = validate_actions
        stage = EdgeCaseStage(order=3, timeout_seconds=0.05)
        code = (
            "def choose_action(state):\n"
            "    if state['allowed']:\n"
            "        return {'action': 'move'}\n"
            "    return {'action': 'invalid'}\n"
        )
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is True


# ── EvaluationReadyStage tests ───────────────────────────────────────────


class TestEvaluationReadyStage:
    def test_dict_strategy_passes(self) -> None:
        stage = EvaluationReadyStage(order=4)
        result = stage.run(candidate={"action": "move"}, scenario=None)
        assert result.passed is True

    def test_code_with_choose_action_passes(self) -> None:
        stage = EvaluationReadyStage(order=4)
        code = "def choose_action(state):\n    return {'action': 'move'}\n"
        scenario = MagicMock()
        scenario.initial_state.return_value = {}
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is True

    def test_code_that_crashes_on_execution_fails(self) -> None:
        stage = EvaluationReadyStage(order=4)
        code = "def choose_action(state):\n    raise ValueError('boom')\n"
        scenario = MagicMock()
        scenario.initial_state.return_value = {}
        result = stage.run(candidate=code, scenario=scenario)
        assert result.passed is False
        assert result.error_code == "execution_error"


# ── ValidationMetrics tests ──────────────────────────────────────────────


class TestValidationMetrics:
    def test_empty_metrics(self) -> None:
        metrics = ValidationMetrics()
        assert metrics.total_candidates == 0
        assert metrics.total_rejected == 0
        assert metrics.rejections_by_stage == {}

    def test_record_pass(self) -> None:
        metrics = ValidationMetrics()
        results = [
            StageResult(stage=0, name="syntax", status=StageStatus.PASSED, duration_ms=1.0),
            StageResult(stage=1, name="contract", status=StageStatus.PASSED, duration_ms=2.0),
        ]
        metrics.record(results)
        assert metrics.total_candidates == 1
        assert metrics.total_rejected == 0

    def test_record_failure(self) -> None:
        metrics = ValidationMetrics()
        results = [
            StageResult(stage=0, name="syntax", status=StageStatus.PASSED, duration_ms=1.0),
            StageResult(stage=1, name="contract", status=StageStatus.FAILED, duration_ms=2.0, error="bad"),
        ]
        metrics.record(results)
        assert metrics.total_candidates == 1
        assert metrics.total_rejected == 1
        assert metrics.rejections_by_stage == {"contract": 1}

    def test_record_multiple(self) -> None:
        metrics = ValidationMetrics()
        # Two failures at syntax, one at contract
        for _ in range(2):
            metrics.record([
                StageResult(stage=0, name="syntax", status=StageStatus.FAILED, duration_ms=0.1, error="bad"),
            ])
        metrics.record([
            StageResult(stage=0, name="syntax", status=StageStatus.PASSED, duration_ms=0.1),
            StageResult(stage=1, name="contract", status=StageStatus.FAILED, duration_ms=0.2, error="bad"),
        ])
        assert metrics.total_candidates == 3
        assert metrics.total_rejected == 3
        assert metrics.rejections_by_stage == {"syntax": 2, "contract": 1}

    def test_estimated_evaluations_saved(self) -> None:
        metrics = ValidationMetrics()
        # 5 candidates rejected at syntax = 5 expensive evaluations saved
        for _ in range(5):
            metrics.record([
                StageResult(stage=0, name="syntax", status=StageStatus.FAILED, duration_ms=0.1, error="bad"),
            ])
        assert metrics.estimated_evaluations_saved == 5

    def test_to_event_payload(self) -> None:
        metrics = ValidationMetrics()
        metrics.record([
            StageResult(stage=0, name="syntax", status=StageStatus.FAILED, duration_ms=0.1, error="bad"),
        ])
        payload = metrics.to_event_payload()
        assert payload["total_candidates"] == 1
        assert payload["total_rejected"] == 1
        assert payload["rejections_by_stage"] == {"syntax": 1}
        assert "estimated_evaluations_saved" in payload


# ── ValidationRunner tests ───────────────────────────────────────────────


class TestValidationRunner:
    def test_runner_runs_pipeline_and_tracks_metrics(self) -> None:
        runner = ValidationRunner(pipeline=ValidationPipeline(stages=[
            SyntaxStage(order=0),
        ]))
        results = runner.validate(candidate={"action": "move"}, scenario=None)
        assert len(results) == 1
        assert results[0].passed is True
        assert runner.metrics.total_candidates == 1
        assert runner.metrics.total_rejected == 0

    def test_runner_tracks_rejections(self) -> None:
        runner = ValidationRunner(pipeline=ValidationPipeline(stages=[
            SyntaxStage(order=0),
        ]))
        runner.validate(candidate=None, scenario=None)
        assert runner.metrics.total_rejected == 1
        assert runner.metrics.rejections_by_stage.get("syntax") == 1

    def test_runner_multiple_validations(self) -> None:
        runner = ValidationRunner(pipeline=ValidationPipeline(stages=[
            SyntaxStage(order=0),
            ContractStage(order=1),
        ]))
        # Pass
        runner.validate(candidate={"action": "move"}, scenario=None)
        # Fail at syntax
        runner.validate(candidate=None, scenario=None)
        assert runner.metrics.total_candidates == 2
        assert runner.metrics.total_rejected == 1

    def test_runner_reset_metrics(self) -> None:
        runner = ValidationRunner(pipeline=ValidationPipeline(stages=[
            SyntaxStage(order=0),
        ]))
        runner.validate(candidate={"action": "move"}, scenario=None)
        runner.reset_metrics()
        assert runner.metrics.total_candidates == 0


# ── default_pipeline tests ───────────────────────────────────────────────


class TestDefaultPipeline:
    def test_default_pipeline_has_five_stages(self) -> None:
        pipeline = default_pipeline()
        assert len(pipeline._stages) == 5

    def test_default_pipeline_stage_order(self) -> None:
        pipeline = default_pipeline()
        names = [s.name for s in pipeline._stages]
        assert names == ["syntax", "contract", "deterministic", "edge_case", "evaluation_ready"]

    def test_default_pipeline_runs_valid_strategy(self) -> None:
        pipeline = default_pipeline()
        results = pipeline.run(candidate={"action": "move"}, scenario=None)
        # Should pass syntax and contract (no scenario), skip deterministic (dict),
        # skip edge_case (no scenario), pass evaluation_ready
        passed_or_skipped = all(r.status in (StageStatus.PASSED, StageStatus.SKIPPED) for r in results)
        assert passed_or_skipped
