"""Tests for Gap 3: Context Preparation Stage."""

from __future__ import annotations

from mts.execution.judge_executor import JudgeExecutor
from mts.knowledge.export import SkillPackage, export_agent_task_skill
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult
from mts.scenarios.custom.agent_task_codegen import generate_agent_task_class
from mts.scenarios.custom.agent_task_designer import SPEC_END, SPEC_START, parse_agent_task_spec
from mts.scenarios.custom.agent_task_spec import AgentTaskSpec
from mts.scenarios.custom.agent_task_validator import validate_execution, validate_spec

# -- Spec tests --

class TestAgentTaskSpecContextFields:
    def test_defaults_are_none(self):
        spec = AgentTaskSpec(task_prompt="test", judge_rubric="test")
        assert spec.context_preparation is None
        assert spec.required_context_keys is None

    def test_fields_set(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            context_preparation="Load the reference document from /docs/spec.md",
            required_context_keys=["reference_doc", "topic_summary"],
        )
        assert spec.context_preparation == "Load the reference document from /docs/spec.md"
        assert spec.required_context_keys == ["reference_doc", "topic_summary"]


# -- Interface default behavior tests --

class ConcreteTask(AgentTaskInterface):
    """Minimal concrete implementation for testing defaults."""

    def get_task_prompt(self, state: dict) -> str:
        return "test prompt"

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
    ) -> AgentTaskResult:
        return AgentTaskResult(score=0.5, reasoning="ok")

    def get_rubric(self) -> str:
        return "test rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {"task": "test"}

    def describe_task(self) -> str:
        return "test"


class TestInterfaceDefaults:
    def test_prepare_context_is_noop(self):
        task = ConcreteTask()
        state = {"key": "value"}
        result = task.prepare_context(state)
        assert result == {"key": "value"}

    def test_validate_context_returns_empty(self):
        task = ConcreteTask()
        errors = task.validate_context({"key": "value"})
        assert errors == []


# -- Codegen tests --

class TestCodegenContextPreparation:
    def test_generated_class_has_prepare_context(self):
        spec = AgentTaskSpec(
            task_prompt="Write about X",
            judge_rubric="Evaluate accuracy",
            context_preparation="Load reference docs",
            required_context_keys=["reference_context"],
        )
        source = generate_agent_task_class(spec, name="ctx_test")
        assert "prepare_context" in source
        assert "validate_context" in source
        assert "_context_preparation" in source
        assert "_required_context_keys" in source

    def test_generated_prepare_context_adds_to_state(self):
        spec = AgentTaskSpec(
            task_prompt="Write about X",
            judge_rubric="Evaluate accuracy",
            context_preparation="Research the topic thoroughly",
            reference_context="X is a specific technology",
            reference_sources=["https://example.com"],
        )
        source = generate_agent_task_class(spec, name="ctx_prep")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)
        cls = ns["CtxPrepAgentTask"]
        instance = cls()
        state = instance.prepare_context({})
        assert state["context_preparation"] == "Research the topic thoroughly"
        assert state["reference_context"] == "X is a specific technology"
        assert state["reference_sources"] == ["https://example.com"]

    def test_generated_validate_context_catches_missing_keys(self):
        spec = AgentTaskSpec(
            task_prompt="Write about X",
            judge_rubric="Evaluate accuracy",
            required_context_keys=["research_brief", "source_list"],
        )
        source = generate_agent_task_class(spec, name="ctx_val")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)
        cls = ns["CtxValAgentTask"]
        instance = cls()
        errors = instance.validate_context({})
        assert len(errors) == 2
        assert "research_brief" in errors[0]
        assert "source_list" in errors[1]

    def test_generated_validate_context_passes_with_keys(self):
        spec = AgentTaskSpec(
            task_prompt="Write about X",
            judge_rubric="Evaluate accuracy",
            required_context_keys=["research_brief"],
        )
        source = generate_agent_task_class(spec, name="ctx_ok")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)
        cls = ns["CtxOkAgentTask"]
        instance = cls()
        errors = instance.validate_context({"research_brief": "some content"})
        assert errors == []

    def test_no_context_prep_is_noop(self):
        spec = AgentTaskSpec(
            task_prompt="Simple task",
            judge_rubric="Evaluate",
        )
        source = generate_agent_task_class(spec, name="no_ctx")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)
        cls = ns["NoCtxAgentTask"]
        instance = cls()
        state = instance.prepare_context({"existing": "data"})
        assert state == {"existing": "data"}
        errors = instance.validate_context(state)
        assert errors == []


# -- Validator tests --

class TestValidatorContextFields:
    def test_empty_context_preparation_rejected(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            context_preparation="   ",
        )
        errors = validate_spec(spec)
        assert any("context_preparation" in e for e in errors)

    def test_empty_required_context_keys_rejected(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            required_context_keys=[],
        )
        errors = validate_spec(spec)
        assert any("required_context_keys" in e for e in errors)

    def test_non_string_required_context_keys_rejected(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            required_context_keys=["valid", ""],  # type: ignore
        )
        errors = validate_spec(spec)
        assert any("required_context_keys[1]" in e for e in errors)

    def test_valid_context_fields_pass(self):
        spec = AgentTaskSpec(
            task_prompt="test",
            judge_rubric="test",
            context_preparation="Load docs from /data/",
            required_context_keys=["research_brief"],
        )
        errors = validate_spec(spec)
        assert errors == []

    def test_execution_validates_prepare_and_validate_context(self):
        spec = AgentTaskSpec(
            task_prompt="Write about topic",
            judge_rubric="Evaluate quality",
            context_preparation="Research the topic",
            required_context_keys=["reference_context"],
            reference_context="Topic is about X",
        )
        source = generate_agent_task_class(spec, name="exec_ctx")
        errors = validate_execution(source)
        assert errors == []


# -- Designer/parser tests --

class TestDesignerContextFields:
    def test_parse_with_context_preparation(self):
        raw = (
            f'{SPEC_START}\n'
            '{\n'
            '  "task_prompt": "Write a post",\n'
            '  "judge_rubric": "Evaluate quality",\n'
            '  "output_format": "free_text",\n'
            '  "judge_model": "claude-sonnet-4-20250514",\n'
            '  "context_preparation": "Research the topic first",\n'
            '  "required_context_keys": ["research_brief", "sources"]\n'
            '}\n'
            f'{SPEC_END}'
        )
        spec = parse_agent_task_spec(raw)
        assert spec.context_preparation == "Research the topic first"
        assert spec.required_context_keys == ["research_brief", "sources"]

    def test_parse_without_context_preparation(self):
        raw = (
            f'{SPEC_START}\n'
            '{\n'
            '  "task_prompt": "Write a post",\n'
            '  "judge_rubric": "Evaluate quality"\n'
            '}\n'
            f'{SPEC_END}'
        )
        spec = parse_agent_task_spec(raw)
        assert spec.context_preparation is None
        assert spec.required_context_keys is None


# -- JudgeExecutor context validation tests --

class TaskWithRequiredContext(AgentTaskInterface):
    """Task that requires context keys."""

    def get_task_prompt(self, state: dict) -> str:
        return "test"

    def evaluate_output(self, output, state, reference_context=None,
                        required_concepts=None, calibration_examples=None):
        return AgentTaskResult(score=0.8, reasoning="good")

    def get_rubric(self) -> str:
        return "test"

    def initial_state(self, seed=None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def validate_context(self, state: dict) -> list[str]:
        errors = []
        if "research_brief" not in state:
            errors.append("missing research_brief")
        return errors


class TestJudgeExecutorContextValidation:
    def test_executor_fails_on_missing_context(self):
        task = TaskWithRequiredContext()
        executor = JudgeExecutor(task)
        result = executor.execute("some output", {})
        assert result.score == 0.0
        assert "Context validation failed" in result.reasoning
        assert "research_brief" in result.reasoning

    def test_executor_passes_with_context(self):
        task = TaskWithRequiredContext()
        executor = JudgeExecutor(task)
        result = executor.execute("some output", {"research_brief": "content"})
        assert result.score == 0.8


# -- Export tests --

class TestExportContextPreparation:
    def test_skill_package_has_context_preparation(self):
        pkg = SkillPackage(
            scenario_name="test",
            display_name="Test",
            description="Test task",
            playbook="Do the thing",
            lessons=[],
            best_strategy=None,
            best_score=0.8,
            best_elo=1500.0,
            hints="",
            task_prompt="Write about X",
            judge_rubric="Evaluate",
            context_preparation="Research X thoroughly before writing",
        )
        d = pkg.to_dict()
        assert d["context_preparation"] == "Research X thoroughly before writing"

        md = pkg.to_skill_markdown()
        assert "## Context Preparation" in md
        assert "Research X thoroughly" in md

    def test_export_agent_task_skill_with_context_preparation(self):
        pkg = export_agent_task_skill(
            scenario_name="test_ctx",
            task_prompt="Write about X",
            judge_rubric="Evaluate",
            output_format="free_text",
            playbook="Do the thing",
            lessons=["lesson 1"],
            best_outputs=[],
            context_preparation="Load reference docs first",
        )
        assert pkg.context_preparation == "Load reference docs first"
        d = pkg.to_dict()
        assert "context_preparation" in d

    def test_no_context_preparation_not_in_dict(self):
        pkg = SkillPackage(
            scenario_name="test",
            display_name="Test",
            description="Test",
            playbook="",
            lessons=[],
            best_strategy=None,
            best_score=0.0,
            best_elo=1500.0,
            hints="",
        )
        d = pkg.to_dict()
        assert "context_preparation" not in d
