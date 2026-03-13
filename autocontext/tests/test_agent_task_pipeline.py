from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
from autocontext.scenarios.custom.agent_task_creator import AgentTaskCreator
from autocontext.scenarios.custom.agent_task_designer import (
    SPEC_END,
    SPEC_START,
    design_agent_task,
    parse_agent_task_spec,
)
from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
from autocontext.scenarios.custom.agent_task_validator import (
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
    judge_model="test-model",
)


def _mock_llm_response(spec: AgentTaskSpec) -> str:
    data: dict[str, object] = {
        "task_prompt": spec.task_prompt,
        "judge_rubric": spec.judge_rubric,
        "output_format": spec.output_format,
        "judge_model": spec.judge_model,
        "difficulty_tiers": spec.difficulty_tiers,
    }
    if spec.reference_context is not None:
        data["reference_context"] = spec.reference_context
    if spec.reference_sources is not None:
        data["reference_sources"] = spec.reference_sources
    if spec.required_concepts is not None:
        data["required_concepts"] = spec.required_concepts
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

    def test_generates_with_reference_context(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Write about RLMs",
            judge_rubric="Check accuracy",
            reference_context="RLM = Recursive Language Model",
            required_concepts=["context folding"],
        )
        source = generate_agent_task_class(spec, name="rlm_task")
        errors = validate_syntax(source)
        assert errors == [], f"Syntax errors: {errors}"
        assert "_reference_context" in source
        assert "_required_concepts" in source
        assert "RLM = Recursive Language Model" in source

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

    def test_empty_reference_context(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            reference_context="",  # empty string should fail
        )
        errors = validate_spec(spec)
        assert any("reference_context" in e for e in errors)

    def test_valid_reference_context(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            reference_context="Domain knowledge here",
            required_concepts=["concept1"],
        )
        errors = validate_spec(spec)
        assert errors == []

    def test_empty_required_concepts_list(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            required_concepts=[],
        )
        errors = validate_spec(spec)
        assert any("required_concepts" in e for e in errors)

    def test_required_concepts_with_empty_string(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            required_concepts=["valid", ""],
        )
        errors = validate_spec(spec)
        assert any("required_concepts[1]" in e for e in errors)

    def test_empty_reference_sources_list(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            reference_sources=[],
        )
        errors = validate_spec(spec)
        assert any("reference_sources" in e for e in errors)

    def test_reference_sources_with_empty_string(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            reference_sources=["https://example.com", ""],
        )
        errors = validate_spec(spec)
        assert any("reference_sources[1]" in e for e in errors)

    def test_valid_reference_sources(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            reference_sources=["https://example.com/docs"],
        )
        errors = validate_spec(spec)
        assert errors == []

    def test_empty_judge_model_is_valid(self) -> None:
        """Empty judge_model is valid — means 'use provider default'."""
        spec = AgentTaskSpec(
            task_prompt="Do something",
            judge_rubric="Some rubric",
            output_format="free_text",
            judge_model="",
        )
        errors = validate_spec(spec)
        assert not any("judge_model" in e for e in errors)


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


class TestDeriveName:
    def _creator(self) -> AgentTaskCreator:
        return AgentTaskCreator(
            llm_fn=lambda s, u: "",
            knowledge_root=Path("/tmp/unused"),
        )

    def test_prefers_longer_domain_words(self) -> None:
        creator = self._creator()
        # "Write a haiku about testing software" -> longer words first
        name = creator.derive_name("Write a haiku about testing software")
        assert name == "software_testing_haiku"

    def test_filters_stop_words(self) -> None:
        creator = self._creator()
        name = creator.derive_name(
            "I want an agent that can write clear, well-structured incident postmortems for production outages"
        )
        assert "incident" in name
        assert "want" not in name
        assert "agent" not in name

    def test_api_documentation(self) -> None:
        creator = self._creator()
        name = creator.derive_name("Create a tool that generates API documentation from code")
        assert "documentation" in name

    def test_simple_case(self) -> None:
        creator = self._creator()
        assert creator.derive_name("haiku writer") == "writer_haiku"

    def test_empty_string(self) -> None:
        creator = self._creator()
        assert creator.derive_name("") == "custom"

    def test_all_stop_words(self) -> None:
        creator = self._creator()
        assert creator.derive_name("a the and") == "custom"

    def test_deduplicates_words(self) -> None:
        creator = self._creator()
        name = creator.derive_name("test test test testing")
        assert name == "testing_test"


class TestSampleInput:
    def test_parse_sample_input(self) -> None:
        data = {
            "task_prompt": "Analyze this outage",
            "judge_rubric": "Check completeness",
            "sample_input": "Service X went down at 3am.",
        }
        raw = f"{SPEC_START}\n{json.dumps(data)}\n{SPEC_END}"
        spec = parse_agent_task_spec(raw)
        assert spec.sample_input == "Service X went down at 3am."

    def test_sample_input_defaults_to_none(self) -> None:
        data = {
            "task_prompt": "Do something",
            "judge_rubric": "Check quality",
        }
        raw = f"{SPEC_START}\n{json.dumps(data)}\n{SPEC_END}"
        spec = parse_agent_task_spec(raw)
        assert spec.sample_input is None

    def test_spec_dataclass_has_sample_input(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Test",
            judge_rubric="Rubric",
            sample_input="Some input data",
        )
        assert spec.sample_input == "Some input data"


class TestAgentTaskCreator:
    def test_end_to_end(self) -> None:
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create("Write a haiku about testing software")
            registered_name = creator.derive_name("Write a haiku about testing software")

            try:
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
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_end_to_end_with_reference_context(self) -> None:
        spec = AgentTaskSpec(
            task_prompt="Write about RLMs",
            judge_rubric="Check accuracy",
            reference_context="RLM = Recursive Language Model",
            reference_sources=["https://example.com/rlm"],
            required_concepts=["context folding"],
        )
        response_text = _mock_llm_response(spec)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            creator.create("Write about recursive language models")
            registered_name = creator.derive_name("Write about recursive language models")

            try:
                # Check spec JSON persists new fields
                custom_dir = Path(tmp) / "_custom_scenarios"
                dirs = list(custom_dir.iterdir())
                scenario_dir = dirs[0]
                spec_data = json.loads((scenario_dir / "agent_task_spec.json").read_text())
                assert spec_data["reference_context"] == "RLM = Recursive Language Model"
                assert spec_data["reference_sources"] == ["https://example.com/rlm"]
                assert spec_data["required_concepts"] == ["context folding"]
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)


class TestSampleInputWiring:
    def test_sample_input_embedded_in_prompt(self) -> None:
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Analyze the following data and provide insights.",
            judge_rubric="Evaluate analysis quality",
            sample_input='{"users": [{"name": "Alice", "age": 30}]}',
        )
        source = generate_agent_task_class(spec, name="data_analysis")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)  # noqa: S102
        cls = ns["DataAnalysisAgentTask"]
        instance = cls()
        prompt = instance.get_task_prompt({})
        assert '{"users"' in prompt
        assert "Analyze the following data" in prompt

    def test_sample_input_in_initial_state(self) -> None:
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Analyze data.",
            judge_rubric="Evaluate",
            sample_input="some input data",
        )
        source = generate_agent_task_class(spec, name="data_task")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)  # noqa: S102
        cls = ns["DataTaskAgentTask"]
        instance = cls()
        state = instance.initial_state()
        assert state.get("sample_input") == "some input data"

    def test_no_sample_input_unchanged(self) -> None:
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Write a haiku.",
            judge_rubric="Evaluate quality",
        )
        source = generate_agent_task_class(spec, name="haiku_task")
        ns: dict = {}
        exec(compile(source, "<test>", "exec"), ns)  # noqa: S102
        cls = ns["HaikuTaskAgentTask"]
        instance = cls()
        prompt = instance.get_task_prompt({})
        assert prompt == "Write a haiku."
        state = instance.initial_state()
        assert "sample_input" not in state


class TestValidatorExternalDataReference:
    def test_warns_when_prompt_references_data_without_sample_input(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt="You will be provided with customer data. Analyze it.",
            judge_rubric="Evaluate analysis",
        )
        errors = validate_spec(spec)
        assert any("sample_input" in e for e in errors)

    def test_no_warning_when_sample_input_provided(self) -> None:
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt="You will be provided with customer data. Analyze it.",
            judge_rubric="Evaluate analysis",
            sample_input='{"customers": []}',
        )
        errors = validate_spec(spec)
        assert not any("sample_input" in e for e in errors)


class TestInternalRetriesSurfacing:
    def test_agent_task_result_has_internal_retries(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult

        result = AgentTaskResult(score=0.8, reasoning="ok", internal_retries=2)
        assert result.internal_retries == 2

    def test_agent_task_result_defaults_to_zero(self) -> None:
        from autocontext.scenarios.agent_task import AgentTaskResult

        result = AgentTaskResult(score=0.8, reasoning="ok")
        assert result.internal_retries == 0


class TestImprovementResultRetries:
    def test_improvement_result_has_total_internal_retries(self) -> None:
        from autocontext.execution.improvement_loop import ImprovementResult, RoundResult

        result = ImprovementResult(
            rounds=[RoundResult(round_number=1, output="o", score=0.8, reasoning="ok")],
            best_output="o",
            best_score=0.8,
            best_round=1,
            total_rounds=1,
            met_threshold=False,
            total_internal_retries=3,
        )
        assert result.total_internal_retries == 3

    def test_improvement_result_defaults_to_zero(self) -> None:
        from autocontext.execution.improvement_loop import ImprovementResult

        result = ImprovementResult(
            rounds=[],
            best_output="",
            best_score=0.0,
            best_round=0,
            total_rounds=0,
            met_threshold=False,
        )
        assert result.total_internal_retries == 0
