"""Tests for Gap 5: Multi-step improvement loop."""

from __future__ import annotations

import contextlib
import logging

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_designer import SPEC_END, SPEC_START, parse_agent_task_spec
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import validate_spec

# -- Spec tests --


class TestSpecPipelineFields:
    def test_defaults(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test")
        assert spec.max_rounds == 1
        assert spec.quality_threshold == 0.9
        assert spec.revision_prompt is None

    def test_custom_values(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            max_rounds=5,
            quality_threshold=0.85,
            revision_prompt="Fix factual errors based on judge feedback",
        )
        assert spec.max_rounds == 5
        assert spec.quality_threshold == 0.85
        assert spec.revision_prompt == "Fix factual errors based on judge feedback"


# -- Validator tests --


class TestValidatorPipelineFields:
    def test_invalid_max_rounds(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test", max_rounds=0)
        errors = validate_spec(spec)
        assert any("max_rounds" in e for e in errors)

    def test_invalid_threshold_zero(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test", quality_threshold=0.0)
        errors = validate_spec(spec)
        assert any("quality_threshold" in e for e in errors)

    def test_invalid_threshold_over_one(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test", quality_threshold=1.5)
        errors = validate_spec(spec)
        assert any("quality_threshold" in e for e in errors)

    def test_empty_revision_prompt(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test", revision_prompt="  ")
        errors = validate_spec(spec)
        assert any("revision_prompt" in e for e in errors)

    def test_valid_pipeline_fields(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            max_rounds=3,
            quality_threshold=0.85,
            revision_prompt="Revise based on feedback",
        )
        errors = validate_spec(spec)
        assert errors == []


# -- Interface tests --


class ImprovingTask(AgentTaskInterface):
    """Task that simulates improvement across rounds."""

    def __init__(self):
        self._call_count = 0

    def get_task_prompt(self, state: dict) -> str:
        return "test prompt"

    def evaluate_output(self, output, state, reference_context=None,
                        required_concepts=None, calibration_examples=None, **kwargs):
        # Score increases with each revision
        if "v3" in output:
            score = 0.95
        elif "v2" in output:
            score = 0.75
        elif "v1" in output:
            score = 0.50
        else:
            score = 0.30
        return AgentTaskResult(
            score=score,
            reasoning=f"Score {score} for output",
            dimension_scores={"quality": score},
        )

    def get_rubric(self) -> str:
        return "test rubric"

    def initial_state(self, seed=None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def revise_output(self, output, judge_result, state):
        # Simulate improvement
        if "v1" in output:
            return output.replace("v1", "v2")
        elif "v2" in output:
            return output.replace("v2", "v3")
        return output + " v1"


class NoRevisionTask(AgentTaskInterface):
    """Task that doesn't implement revision (default no-op)."""

    def get_task_prompt(self, state: dict) -> str:
        return "test"

    def evaluate_output(self, output, state, reference_context=None,
                        required_concepts=None, calibration_examples=None, **kwargs):
        return AgentTaskResult(score=0.5, reasoning="ok")

    def get_rubric(self) -> str:
        return "test"

    def initial_state(self, seed=None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"


# -- ImprovementLoop tests --


class TestImprovementLoop:
    def test_single_round(self):
        task = ImprovingTask()
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        result = loop.run("initial output", {})
        assert result.total_rounds == 1
        assert not result.met_threshold
        assert len(result.rounds) == 1
        assert result.rounds[0].round_number == 1
        assert not result.rounds[0].is_revision

    def test_improvement_across_rounds(self):
        task = ImprovingTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("initial output", {})
        # Should improve: initial(0.3) -> v1(0.5) -> v2(0.75) -> v3(0.95)
        assert result.total_rounds >= 3
        assert result.best_score >= 0.75
        assert result.improved

    def test_stops_at_threshold(self):
        task = ImprovingTask()
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("initial output", {})
        # Should stop when v3 scores 0.95 >= 0.9
        assert result.met_threshold
        assert result.best_score >= 0.9
        # Should not run all 10 rounds
        assert result.total_rounds < 10
        assert result.termination_reason == "threshold_met"

    def test_no_revision_stops_early(self):
        task = NoRevisionTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("some output", {})
        # revise_output returns same string, loop should stop
        assert result.total_rounds == 1
        assert result.termination_reason == "unchanged_output"

    def test_best_tracking(self):
        task = ImprovingTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.99)
        result = loop.run("initial output", {})
        # v3 should be best even if threshold not met
        assert result.best_score >= 0.75
        assert result.best_round > 1

    def test_passes_reference_context(self):
        """Verify reference context flows through to evaluate_output."""
        received = {}

        class ContextCapture(AgentTaskInterface):
            def get_task_prompt(self, state):
                return "test"
            def evaluate_output(self, output, state, reference_context=None,
                                required_concepts=None, calibration_examples=None, **kwargs):
                received["ref"] = reference_context
                received["concepts"] = required_concepts
                received["calibration"] = calibration_examples
                return AgentTaskResult(score=0.95, reasoning="ok")
            def get_rubric(self):
                return "test"
            def initial_state(self, seed=None):
                return {}
            def describe_task(self):
                return "test"

        task = ContextCapture()
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        loop.run(
            "output", {},
            reference_context="ref ctx",
            required_concepts=["concept1"],
            calibration_examples=[{"score": 0.5}],
        )
        assert received["ref"] == "ref ctx"
        assert received["concepts"] == ["concept1"]
        assert received["calibration"] == [{"score": 0.5}]

    def test_rounds_marked_as_revision(self):
        task = ImprovingTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.99)
        result = loop.run("initial output", {})
        assert not result.rounds[0].is_revision
        if len(result.rounds) > 1:
            assert result.rounds[1].is_revision

    def test_improved_property_false_single_round(self):
        task = NoRevisionTask()
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        result = loop.run("output", {})
        assert not result.improved

    def test_verify_facts_called_and_issues_appended(self):
        """MTS-50: verifyFacts callback appends issues to reasoning."""

        class VerifyTask(AgentTaskInterface):
            def get_task_prompt(self, state):
                return "test"
            def evaluate_output(self, output, state, reference_context=None,
                                required_concepts=None, calibration_examples=None, **kwargs):
                return AgentTaskResult(score=0.95, reasoning="good")
            def get_rubric(self):
                return "test"
            def initial_state(self, seed=None):
                return {}
            def describe_task(self):
                return "test"
            def verify_facts(self, output, state):
                return {"verified": False, "issues": ["Date is wrong", "Name misspelled"]}

        task = VerifyTask()
        loop = ImprovementLoop(task, max_rounds=1, quality_threshold=0.9)
        result = loop.run("test output", {})
        assert "Fact-check issues" in result.rounds[0].reasoning
        assert "Date is wrong" in result.rounds[0].reasoning
        assert "Name misspelled" in result.rounds[0].reasoning
        # Score should be penalized (10% reduction) when fact-check fails
        assert result.best_score < 0.95
        assert result.best_score == 0.95 * 0.9  # 0.855

    def test_threshold_sensitivity_near_threshold_continues(self):
        """MTS-53: Score 0.91 with threshold 0.90 does not stop immediately."""

        class StableTask(AgentTaskInterface):
            def __init__(self):
                self._count = 0
            def get_task_prompt(self, state):
                return "test"
            def evaluate_output(self, output, state, reference_context=None,
                                required_concepts=None, calibration_examples=None, **kwargs):
                self._count += 1
                return AgentTaskResult(score=0.91, reasoning=f"round {self._count}")
            def get_rubric(self):
                return "test"
            def initial_state(self, seed=None):
                return {}
            def describe_task(self):
                return "test"
            def revise_output(self, output, judge_result, state):
                return output + " revised"

        task = StableTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.90)
        result = loop.run("initial", {})
        assert result.met_threshold is True
        # Should run 2 rounds: near-miss then confirmed
        assert result.total_rounds == 2
        assert task._count == 2

    def test_threshold_sensitivity_clearly_above_stops_immediately(self):
        """MTS-53: Score 0.95 with threshold 0.90 stops on first round."""

        class ClearTask(AgentTaskInterface):
            def __init__(self):
                self._count = 0
            def get_task_prompt(self, state):
                return "test"
            def evaluate_output(self, output, state, reference_context=None,
                                required_concepts=None, calibration_examples=None, **kwargs):
                self._count += 1
                return AgentTaskResult(score=0.95, reasoning="great")
            def get_rubric(self):
                return "test"
            def initial_state(self, seed=None):
                return {}
            def describe_task(self):
                return "test"

        task = ClearTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.90)
        result = loop.run("initial", {})
        assert result.met_threshold is True
        assert result.total_rounds == 1
        assert task._count == 1

    def test_threshold_sensitivity_drop_then_recover(self):
        """MTS-53: Score drops below threshold after near-miss, then recovers."""

        class DropRecoverTask(AgentTaskInterface):
            def __init__(self):
                self._count = 0
                self._scores = [0.91, 0.85, 0.91, 0.91]
            def get_task_prompt(self, state):
                return "test"
            def evaluate_output(self, output, state, reference_context=None,
                                required_concepts=None, calibration_examples=None, **kwargs):
                idx = min(self._count, len(self._scores) - 1)
                score = self._scores[idx]
                self._count += 1
                return AgentTaskResult(score=score, reasoning=f"round {self._count}")
            def get_rubric(self):
                return "test"
            def initial_state(self, seed=None):
                return {}
            def describe_task(self):
                return "test"
            def revise_output(self, output, judge_result, state):
                return output + " revised"

        task = DropRecoverTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.90)
        result = loop.run("initial", {})
        assert result.met_threshold is True
        # near-miss(0.91), drop(0.85), near-miss(0.91), confirm(0.91)
        assert result.total_rounds == 4


# -- Codegen tests --


class TestCodegenPipelineFields:
    def test_generated_class_has_revise_output(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            max_rounds=3,
            quality_threshold=0.85,
            revision_prompt="Fix errors",
        )
        source = generate_agent_task_class(spec, name="pipeline_test")
        assert "revise_output" in source
        assert "_max_rounds" in source
        assert "_quality_threshold" in source
        assert "_revision_prompt" in source

    def test_generated_revise_noop_for_single_round(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test")
        source = generate_agent_task_class(spec, name="noop_test")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)
        cls = ns["NoopTestAgentTask"]
        instance = cls()
        result = AgentTaskResult(score=0.5, reasoning="ok")
        revised = instance.revise_output("original", result, {})
        assert revised == "original"


# -- Designer/parser tests --


class TestDesignerPipelineFields:
    def test_parse_with_pipeline_fields(self):
        raw = (
            f'{SPEC_START}\n'
            '{\n'
            '  "task_prompt": "Write a post",\n'
            '  "judge_rubric": "Evaluate quality",\n'
            '  "max_rounds": 5,\n'
            '  "quality_threshold": 0.85,\n'
            '  "revision_prompt": "Fix factual errors"\n'
            '}\n'
            f'{SPEC_END}'
        )
        spec = parse_agent_task_spec(raw)
        assert spec.max_rounds == 5
        assert spec.quality_threshold == 0.85
        assert spec.revision_prompt == "Fix factual errors"

    def test_parse_defaults(self):
        raw = (
            f'{SPEC_START}\n'
            '{"task_prompt": "test", "judge_rubric": "test"}\n'
            f'{SPEC_END}'
        )
        spec = parse_agent_task_spec(raw)
        assert spec.max_rounds == 1
        assert spec.quality_threshold == 0.9
        assert spec.revision_prompt is None


# -- Programmable fake task for new feature tests --


class ProgrammableTask(AgentTaskInterface):
    """Task returning pre-programmed results for each round."""

    def __init__(self, results: list[AgentTaskResult]) -> None:
        self._results = results
        self._call = 0

    def get_task_prompt(self, state: dict) -> str:
        return "test"

    def evaluate_output(
        self, output: str, state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        **kwargs: object,
    ) -> AgentTaskResult:
        idx = min(self._call, len(self._results) - 1)
        self._call += 1
        return self._results[idx]

    def get_rubric(self) -> str:
        return "test"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def revise_output(self, output: str, judge_result: AgentTaskResult, state: dict) -> str:
        return f"{output} [revised]"


# -- terminationReason tests --


class TestTerminationReason:
    def test_max_rounds(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.3, reasoning="low"),
            AgentTaskResult(score=0.5, reasoning="mid"),
            AgentTaskResult(score=0.6, reasoning="better"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "max_rounds"
        assert not result.met_threshold

    def test_consecutive_failures(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0, reasoning="Failed to parse judge response: no parseable score found"),
        ])
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "consecutive_failures"

    def test_threshold_met(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "threshold_met"

    def test_unchanged_output(self):
        class NoChangeTask(ProgrammableTask):
            def revise_output(self, output, judge_result, state):
                return output  # No change

        task = NoChangeTask([AgentTaskResult(score=0.5, reasoning="ok")])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "unchanged_output"


# -- Plateau detection tests --


class TestPlateauDetection:
    def test_plateau_after_two_consecutive(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok"),
            AgentTaskResult(score=0.505, reasoning="ok"),
            AgentTaskResult(score=0.508, reasoning="ok"),
        ])
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "plateau_stall"
        assert result.total_rounds == 3

    def test_plateau_resets_on_significant_change(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok"),
            AgentTaskResult(score=0.505, reasoning="ok"),   # plateau +1
            AgentTaskResult(score=0.7, reasoning="jump"),   # reset
            AgentTaskResult(score=0.705, reasoning="ok"),   # plateau +1
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "threshold_met"
        assert result.total_rounds == 5

    def test_single_plateau_not_enough(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok"),
            AgentTaskResult(score=0.505, reasoning="ok"),   # plateau +1 only
            AgentTaskResult(score=0.7, reasoning="jump"),   # reset
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=10, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.termination_reason == "threshold_met"


# -- Dimension trajectory tests --


class TestDimensionTrajectory:
    def test_builds_trajectory(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok", dimension_scores={"clarity": 0.4, "accuracy": 0.6}),
            AgentTaskResult(score=0.7, reasoning="better", dimension_scores={"clarity": 0.6, "accuracy": 0.8}),
            AgentTaskResult(score=0.95, reasoning="great", dimension_scores={"clarity": 0.9, "accuracy": 1.0}),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.dimension_trajectory == {
            "clarity": [0.4, 0.6, 0.9],
            "accuracy": [0.6, 0.8, 1.0],
        }

    def test_skips_failed_rounds(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok", dimension_scores={"quality": 0.5}),
            AgentTaskResult(score=0, reasoning="Failed to parse judge response: no parseable score found"),
            AgentTaskResult(score=0.95, reasoning="great", dimension_scores={"quality": 0.9}),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.dimension_trajectory == {"quality": [0.5, 0.9]}

    def test_empty_trajectory_no_dimensions(self):
        task = ProgrammableTask([AgentTaskResult(score=0.95, reasoning="great")])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.dimension_trajectory == {}


# -- Minimum revision rounds tests --


class TestMinRounds:
    def test_continues_past_threshold(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.95, reasoning="great"),
            AgentTaskResult(score=0.96, reasoning="better"),
            AgentTaskResult(score=0.97, reasoning="best"),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9, min_rounds=3)
        result = loop.run("test", {})
        assert result.met_threshold
        assert result.termination_reason == "threshold_met"
        assert result.total_rounds == 3
        assert result.best_score == 0.97

    def test_stops_at_threshold_when_min_met(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok"),
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9, min_rounds=1)
        result = loop.run("test", {})
        assert result.met_threshold
        assert result.total_rounds == 2

    def test_defaults_to_one(self):
        task = ProgrammableTask([AgentTaskResult(score=0.95, reasoning="great")])
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("test", {})
        assert result.total_rounds == 1


# -- Max score delta tests --


class TestMaxScoreDelta:
    def test_warns_on_large_jump(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.2, reasoning="low"),
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9, max_score_delta=0.5)
        log = logging.getLogger("autocontext.execution.improvement_loop")
        with self._capture_warnings(log) as warnings:
            result = loop.run("test", {})
        assert result.met_threshold
        assert any("Score jump" in w for w in warnings)

    @staticmethod
    def _capture_warnings(log: logging.Logger):  # noqa: ANN205
        """Context manager that captures WARNING-level messages."""
        @contextlib.contextmanager
        def _ctx():  # type: ignore[no-untyped-def]
            captured: list[str] = []

            class _Handler(logging.Handler):
                def emit(self, record: logging.LogRecord) -> None:
                    if record.levelno >= logging.WARNING:
                        captured.append(self.format(record))

            handler = _Handler()
            log.addHandler(handler)
            try:
                yield captured
            finally:
                log.removeHandler(handler)

        return _ctx()

    def test_caps_score_when_enabled(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.2, reasoning="low"),
            AgentTaskResult(score=0.9, reasoning="huge jump"),
        ])
        loop = ImprovementLoop(
            task, max_rounds=2, quality_threshold=0.99,
            max_score_delta=0.3, cap_score_jumps=True,
        )
        result = loop.run("test", {})
        # Round 2: 0.2 -> 0.9, capped to 0.2 + 0.3 = 0.5
        assert result.best_score == 0.5

    def test_no_cap_by_default(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.2, reasoning="low"),
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9, max_score_delta=0.3)
        result = loop.run("test", {})
        # Score should NOT be capped, even though delta > 0.3
        assert result.best_score == 0.95

    def test_no_warn_within_limit(self):
        task = ProgrammableTask([
            AgentTaskResult(score=0.5, reasoning="ok"),
            AgentTaskResult(score=0.95, reasoning="great"),
        ])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.9, max_score_delta=0.5)
        result = loop.run("test", {})
        assert result.met_threshold
