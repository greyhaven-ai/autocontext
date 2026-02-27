"""Tests for Gap 5: Multi-step improvement loop."""

from __future__ import annotations

from mts.execution.improvement_loop import ImprovementLoop
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from mts.scenarios.custom.agent_task_codegen import generate_agent_task_class
from mts.scenarios.custom.agent_task_designer import SPEC_END, SPEC_START, parse_agent_task_spec
from mts.scenarios.custom.agent_task_spec import AgentTaskSpec
from mts.scenarios.custom.agent_task_validator import validate_spec

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
                        required_concepts=None, calibration_examples=None):
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
                        required_concepts=None, calibration_examples=None):
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

    def test_no_revision_stops_early(self):
        task = NoRevisionTask()
        loop = ImprovementLoop(task, max_rounds=5, quality_threshold=0.9)
        result = loop.run("some output", {})
        # revise_output returns same string, loop should stop
        assert result.total_rounds == 1

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
                                required_concepts=None, calibration_examples=None):
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
