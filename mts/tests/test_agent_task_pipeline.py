from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from mts.scenarios.custom.agent_task_codegen import generate_agent_task_class
from mts.scenarios.custom.agent_task_creator import AgentTaskCreator
from mts.scenarios.custom.agent_task_designer import (
    SPEC_END,
    SPEC_START,
    design_agent_task,
    parse_agent_task_spec,
)
from mts.scenarios.custom.agent_task_spec import AgentTaskSpec
from mts.scenarios.custom.agent_task_validator import (
    validate_execution,
    validate_spec,
    validate_syntax,
)

# --- Fixtures ---

SAMPLE_SPEC = AgentTaskSpec(
    task_prompt="Write a haiku about testing software.",
    judge_rubric=(
        "Evaluate on: (1) Format — is it a valid haiku (5-7-5 syllables)? "
        "(2) Relevance — is it about software testing? "
        "(3) Creativity — is it original and evocative?"
    ),
    output_format="free_text",
    judge_model="claude-sonnet-4-20250514",
)


def _mock_llm_response(spec: AgentTaskSpec) -> str:
    data = {
        "task_prompt": spec.task_prompt,
        "judge_rubric": spec.judge_rubric,
        "output_format": spec.output_format,
        "judge_model": spec.judge_model,
        "difficulty_tiers": spec.difficulty_tiers,
    }
    return f"Here is the spec:\n{SPEC_START}\n{json.dumps(data, indent=2)}\n{SPEC_END}\n"


# --- Tests ---


class TestDesignAgentTask:
    def test_parse_spec_from_response(self) -> None:
        response = _mock_llm_response(SAMPLE_SPEC)
        spec = parse_agent_task_spec(response)
        assert spec.task_prompt == SAMPLE_SPEC.task_prompt
        assert spec.judge_rubric == SAMPLE_SPEC.judge_rubric
        assert spec.output_format == "free_text"

    def test_parse_spec_missing_delimiters(self) -> None:
        with pytest.raises(ValueError, match="does not contain"):
            parse_agent_task_spec("no delimiters here")

    def test_design_agent_task_with_mock(self) -> None:
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        spec = design_agent_task("Write a haiku about testing", mock_llm)
        assert spec.task_prompt == SAMPLE_SPEC.task_prompt
        assert spec.output_format == "free_text"


class TestGenerateAgentTaskClass:
    def test_produces_valid_python(self) -> None:
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        errors = validate_syntax(source)
        assert errors == [], f"Syntax errors: {errors}"

    def test_contains_class_and_methods(self) -> None:
        source = generate_agent_task_class(SAMPLE_SPEC, name="haiku_task")
        assert "class HaikuTaskAgentTask" in source
        assert "def get_task_prompt" in source
        assert "def evaluate_output" in source
        assert "def get_rubric" in source
        assert "def initial_state" in source
        assert "def describe_task" in source


class TestValidateSpec:
    def test_valid_spec(self) -> None:
        errors = validate_spec(SAMPLE_SPEC)
        assert errors == []

    def test_empty_rubric(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="",
            output_format="free_text",
            judge_model="some-model",
        )
        errors = validate_spec(spec)
        assert any("judge_rubric" in e for e in errors)

    def test_empty_task_prompt(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="",
            judge_rubric="Some rubric",
            output_format="free_text",
            judge_model="some-model",
        )
        errors = validate_spec(spec)
        assert any("task_prompt" in e for e in errors)

    def test_invalid_output_format(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            output_format="invalid_format",
            judge_model="some-model",
        )
        errors = validate_spec(spec)
        assert any("output_format" in e for e in errors)

    def test_empty_judge_model(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            output_format="free_text",
            judge_model="",
        )
        errors = validate_spec(spec)
        assert any("judge_model" in e for e in errors)


class TestValidateSyntax:
    def test_valid_code(self) -> None:
        errors = validate_syntax("x = 1\ny = 2\n")
        assert errors == []

    def test_bad_code(self) -> None:
        errors = validate_syntax("def foo(\n")
        assert len(errors) > 0
        assert "syntax error" in errors[0]


class TestValidateExecution:
    def test_generated_code_passes(self) -> None:
        source = generate_agent_task_class(SAMPLE_SPEC, name="exec_test")
        errors = validate_execution(source)
        assert errors == [], f"Execution errors: {errors}"


class TestAgentTaskCreator:
    def test_end_to_end(self) -> None:
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create("Write a haiku about testing software")

            assert instance.get_task_prompt({}) == SAMPLE_SPEC.task_prompt
            assert instance.get_rubric() == SAMPLE_SPEC.judge_rubric

            # Check files were saved
            custom_dir = Path(tmp) / "_custom_scenarios"
            dirs = list(custom_dir.iterdir())
            assert len(dirs) == 1
            scenario_dir = dirs[0]
            assert (scenario_dir / "agent_task.py").exists()
            assert (scenario_dir / "agent_task_spec.json").exists()
            assert (scenario_dir / "scenario_type.txt").exists()
            assert (scenario_dir / "scenario_type.txt").read_text() == "agent_task"
