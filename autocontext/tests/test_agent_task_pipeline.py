from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from autocontext.scenarios.artifact_editing import ArtifactEditingInterface
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
from autocontext.scenarios.custom.artifact_editing_designer import (
    ARTIFACT_SPEC_END,
    ARTIFACT_SPEC_START,
)
from autocontext.scenarios.custom.family_pipeline import validate_for_family
from autocontext.scenarios.custom.investigation_designer import (
    INVESTIGATION_SPEC_END,
    INVESTIGATION_SPEC_START,
)
from autocontext.scenarios.custom.simulation_designer import SIM_SPEC_END, SIM_SPEC_START
from autocontext.scenarios.custom.workflow_designer import (
    WORKFLOW_SPEC_END,
    WORKFLOW_SPEC_START,
)
from autocontext.scenarios.investigation import InvestigationInterface
from autocontext.scenarios.simulation import SimulationInterface
from autocontext.scenarios.workflow import WorkflowInterface

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


def _mock_simulation_response() -> str:
    data = {
        "description": "Recover a multi-step API workflow.",
        "environment_description": "Mock API orchestration environment.",
        "initial_state_description": "No calls completed.",
        "success_criteria": ["all required actions complete", "invalid order is recovered"],
        "failure_modes": ["dependency mismatch", "partial side effects"],
        "max_steps": 6,
        "actions": [
            {
                "name": "book_flight",
                "description": "Reserve a flight.",
                "parameters": {"flight_id": "string"},
                "preconditions": [],
                "effects": ["flight_reserved"],
            },
            {
                "name": "book_hotel",
                "description": "Reserve a hotel.",
                "parameters": {"hotel_id": "string"},
                "preconditions": ["book_flight"],
                "effects": ["hotel_reserved"],
            },
        ],
    }
    return f"{SIM_SPEC_START}\n{json.dumps(data, indent=2)}\n{SIM_SPEC_END}\n"


def _mock_artifact_editing_response() -> str:
    data = {
        "task_description": "Update a YAML config to add a database section.",
        "rubric": "Evaluate artifact correctness, validator success, and minimal unnecessary changes.",
        "validation_rules": [
            'config/app.yaml must contain "database:"',
            'config/app.yaml must contain "host:"',
            'config/app.yaml must contain "port:"',
        ],
        "artifacts": [
            {
                "path": "config/app.yaml",
                "content": "app:\n  name: myapp\n  port: 8080\n",
                "content_type": "yaml",
            },
        ],
    }
    return f"{ARTIFACT_SPEC_START}\n{json.dumps(data, indent=2)}\n{ARTIFACT_SPEC_END}\n"


def _mock_investigation_response() -> str:
    data = {
        "description": "Investigate a production outage by gathering evidence and identifying the root cause.",
        "environment_description": "Mock service environment with logs and dashboards.",
        "initial_state_description": "An outage is active and only partial evidence is visible.",
        "evidence_pool_description": (
            "Logs implicate the auth service, metrics show latency spikes, and a cron-job entry is a red herring."
        ),
        "diagnosis_target": "A bad auth deployment exhausted the database connection pool.",
        "success_criteria": [
            "collect enough evidence to explain the outage",
            "identify the correct diagnosis without relying on red herrings",
        ],
        "failure_modes": ["following a cron-job red herring"],
        "max_steps": 6,
        "actions": [
            {
                "name": "inspect_logs",
                "description": "Review service logs around the incident.",
                "parameters": {"service": "string"},
                "preconditions": [],
                "effects": ["log_evidence_collected"],
            },
            {
                "name": "query_metrics",
                "description": "Check dashboard metrics related to the outage.",
                "parameters": {"metric": "string"},
                "preconditions": [],
                "effects": ["metrics_evidence_collected"],
            },
            {
                "name": "record_diagnosis",
                "description": "Submit the final diagnosis.",
                "parameters": {"diagnosis": "string"},
                "preconditions": ["inspect_logs", "query_metrics"],
                "effects": ["diagnosis_recorded"],
            },
        ],
    }
    return f"{INVESTIGATION_SPEC_START}\n{json.dumps(data, indent=2)}\n{INVESTIGATION_SPEC_END}\n"


def _mock_workflow_response() -> str:
    data = {
        "description": "Execute an order-processing workflow with compensation when downstream steps fail.",
        "environment_description": "Mock commerce workflow with payment, inventory, and notification side effects.",
        "initial_state_description": "No workflow steps have run yet.",
        "workflow_steps": [
            {
                "name": "charge_payment",
                "description": "Charge the payment method.",
                "idempotent": False,
                "reversible": True,
                "compensation": "refund_payment",
            },
            {
                "name": "reserve_inventory",
                "description": "Reserve inventory for the order.",
                "idempotent": True,
                "reversible": True,
                "compensation": "release_inventory",
            },
            {
                "name": "send_confirmation",
                "description": "Send the confirmation notification.",
                "idempotent": True,
                "reversible": False,
            },
        ],
        "success_criteria": [
            "all required workflow steps complete in order",
            "reversible side effects are compensated if failures occur",
        ],
        "failure_modes": ["payment failure", "notification sent before rollback"],
        "max_steps": 7,
        "actions": [
            {
                "name": "charge_payment",
                "description": "Charge the payment method.",
                "parameters": {"payment_id": "string"},
                "preconditions": [],
                "effects": ["payment_captured"],
            },
            {
                "name": "reserve_inventory",
                "description": "Reserve inventory for the order.",
                "parameters": {"sku": "string"},
                "preconditions": ["charge_payment"],
                "effects": ["inventory_reserved"],
            },
            {
                "name": "send_confirmation",
                "description": "Send the confirmation notification.",
                "parameters": {"channel": "string"},
                "preconditions": ["reserve_inventory"],
                "effects": ["confirmation_sent"],
            },
        ],
    }
    return f"{WORKFLOW_SPEC_START}\n{json.dumps(data, indent=2)}\n{WORKFLOW_SPEC_END}\n"


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

    def test_parse_spec_serializes_structured_judge_rubric(self) -> None:
        spec_data = {
            "task_prompt": "Choose whether to optimize the visible metric or the true user goal.",
            "judge_rubric": {
                "dimensions": [
                    {"name": "true_goal_usefulness", "weight": 0.4},
                    {"name": "anti_gaming", "weight": 0.3},
                ],
                "overall_rule": "Prefer genuinely helpful outputs over score exploitation.",
            },
            "output_format": "free_text",
        }
        raw = f"{SPEC_START}\n{json.dumps(spec_data, indent=2)}\n{SPEC_END}"

        spec = parse_agent_task_spec(raw)

        assert isinstance(spec.judge_rubric, str)
        assert '"true_goal_usefulness"' in spec.judge_rubric
        assert '"overall_rule"' in spec.judge_rubric

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

    def test_family_pipeline_normalizes_structured_runtime_fields(self) -> None:
        errors = validate_for_family(
            "agent_task",
            {
                "task_prompt": "Summarize the prepared evidence.",
                "judge_rubric": "Evaluate completeness and grounding.",
                "reference_context": {"facts": ["alpha", "beta"]},
                "context_preparation": {"steps": ["load evidence"]},
                "revision_prompt": ["Add missing facts"],
                "sample_input": {"case_id": "case-123"},
            },
        )
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

    def test_uses_shared_improved_naming_logic(self) -> None:
        from autocontext.scenarios.custom.naming import derive_name

        creator = self._creator()
        description = "Write a haiku about testing software"
        name = creator.derive_name(description)
        assert name == derive_name(description)
        assert any(word in name.split("_") for word in ("haiku", "testing", "software"))

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
        assert creator.derive_name("haiku writer") == "haiku_writer"

    def test_empty_string(self) -> None:
        creator = self._creator()
        assert creator.derive_name("") == "custom"

    def test_all_stop_words(self) -> None:
        creator = self._creator()
        assert creator.derive_name("a the and") == "custom"

    def test_deduplicates_words(self) -> None:
        creator = self._creator()
        name = creator.derive_name("test test test testing")
        assert name == "test_testing"


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

    def test_parse_structured_sample_input_serializes_json(self) -> None:
        data = {
            "task_prompt": "Analyze this clinical trial brief",
            "judge_rubric": "Check completeness",
            "sample_input": {
                "indication": "oncology",
                "phase": "II",
                "jurisdiction": "FDA",
            },
        }
        raw = f"{SPEC_START}\n{json.dumps(data)}\n{SPEC_END}"
        spec = parse_agent_task_spec(raw)
        assert isinstance(spec.sample_input, str)
        assert '"indication": "oncology"' in spec.sample_input
        assert '"phase": "II"' in spec.sample_input

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

    def test_end_to_end_with_structured_sample_input(self) -> None:
        spec_data = {
            "task_prompt": "Design a Phase II trial protocol from the study brief.",
            "judge_rubric": "Evaluate protocol rigor and regulatory alignment.",
            "output_format": "free_text",
            "sample_input": {
                "indication": "oncology",
                "phase": "II",
                "jurisdiction": "FDA",
                "budget": "moderate",
            },
            "calibration_examples": [
                {
                    "human_score": 0.3,
                    "human_notes": "Missing endpoint rationale.",
                    "agent_output": "Use a generic protocol.",
                },
                {
                    "human_score": 0.9,
                    "human_notes": "Well scoped, justified, and safety-aware.",
                    "agent_output": "Use a randomized protocol with clear endpoints.",
                },
            ],
        }
        response_text = f"Here is the spec:\n{SPEC_START}\n{json.dumps(spec_data, indent=2)}\n{SPEC_END}\n"

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            from unittest.mock import patch

            from autocontext.scenarios.families import get_family

            with patch(
                "autocontext.scenarios.custom.agent_task_creator.route_to_family",
                return_value=get_family("agent_task"),
            ):
                instance = creator.create("Design a clinical trial protocol for oncology")
            registered_name = creator.derive_name("Design a clinical trial protocol for oncology")

            try:
                prompt = instance.get_task_prompt({})
                assert '"indication": "oncology"' in prompt
                assert "## Input Data" in prompt
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_retries_agent_task_design_after_timeout(self) -> None:
        attempts = {"count": 0}
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            del system, user
            attempts["count"] += 1
            if attempts["count"] == 1:
                raise RuntimeError("PiCLIRuntime failed: timeout")
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
                assert instance.get_rubric() == SAMPLE_SPEC.judge_rubric
                assert attempts["count"] == 2
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_retries_agent_task_design_after_parse_failure(self) -> None:
        attempts = {"count": 0}
        invalid_response = f'{SPEC_START}\n{{\n  "task_prompt": }}\n{SPEC_END}\n'
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            del system, user
            attempts["count"] += 1
            if attempts["count"] == 1:
                return invalid_response
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
                assert instance.get_rubric() == SAMPLE_SPEC.judge_rubric
                assert attempts["count"] == 2
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_routes_simulation_like_requests_to_simulation_creator(self) -> None:
        response_text = _mock_simulation_response()

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create("Build a stateful API orchestration workflow with rollback")
            registered_name = creator.derive_name("Build a stateful API orchestration workflow with rollback")
            try:
                assert isinstance(instance, SimulationInterface)
                result = instance.execute_match(
                    {
                        "actions": [
                            {"name": "book_flight", "parameters": {"flight_id": "F1"}},
                            {"name": "book_hotel", "parameters": {"hotel_id": "H1"}},
                        ]
                    },
                    seed=0,
                )
                assert result.score > 0.5
                scenario_dir = Path(tmp) / "_custom_scenarios" / registered_name
                assert (scenario_dir / "scenario.py").exists()
                assert (scenario_dir / "spec.json").exists()
                assert (scenario_dir / "scenario_type.txt").read_text() == "simulation"
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_rejects_classified_but_unsupported_game_families(self) -> None:
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            with pytest.raises(ValueError, match="not yet supported for custom scaffolding"):
                creator.create("Create a competitive two-player board game tournament")

    def test_routes_artifact_editing_requests_to_artifact_creator(self) -> None:
        response_text = _mock_artifact_editing_response()

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create("Edit a YAML config file to add a database section")
            registered_name = creator.derive_name("Edit a YAML config file to add a database section")
            try:
                assert isinstance(instance, ArtifactEditingInterface)
                artifacts = instance.initial_artifacts()
                assert artifacts[0].path == "config/app.yaml"
                assert "database section" in instance.describe_task().lower()
                scenario_dir = Path(tmp) / "_custom_scenarios" / registered_name
                assert (scenario_dir / "scenario.py").exists()
                assert (scenario_dir / "spec.json").exists()
                assert (scenario_dir / "scenario_type.txt").read_text() == "artifact_editing"
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_routes_investigation_requests_to_investigation_creator(self) -> None:
        response_text = _mock_investigation_response()

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create(
                "Create an investigation where the agent gathers evidence, avoids red herrings, and finds the root cause"
            )
            registered_name = creator.derive_name(
                "Create an investigation where the agent gathers evidence, avoids red herrings, and finds the root cause"
            )
            try:
                assert isinstance(instance, InvestigationInterface)
                assert instance.get_evidence_pool(instance.initial_state())
                scenario_dir = Path(tmp) / "_custom_scenarios" / registered_name
                assert (scenario_dir / "scenario.py").exists()
                assert (scenario_dir / "spec.json").exists()
                assert (scenario_dir / "scenario_type.txt").read_text() == "investigation"
            finally:
                SCENARIO_REGISTRY.pop(registered_name, None)

    def test_routes_workflow_requests_to_workflow_creator(self) -> None:
        response_text = _mock_workflow_response()

        def mock_llm(system: str, user: str) -> str:
            return response_text

        from autocontext.scenarios import SCENARIO_REGISTRY

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            instance = creator.create("Create a transactional workflow with compensation and side effects")
            registered_name = creator.derive_name("Create a transactional workflow with compensation and side effects")
            try:
                from autocontext.scenarios.world_state import WorldState

                assert isinstance(instance, WorkflowInterface)
                assert len(instance.get_workflow_steps()) >= 2
                initial_state = instance.initial_state()
                world_state = WorldState.from_dict(initial_state["_world_state"])
                assert any(entity.entity_id == "workflow" for entity in world_state.entities)
                assert any(entity.entity_type == "workflow_step" for entity in world_state.entities)

                first_step = instance.get_workflow_steps()[0]
                _result, next_state = instance.execute_step(initial_state, first_step)
                next_world_state = WorldState.from_dict(next_state["_world_state"])
                step_entity = next(
                    entity for entity in next_world_state.entities if entity.entity_id == f"step:{first_step.name}"
                )
                assert step_entity.status == "completed"
                assert next_state["world_state_deltas"]
                scenario_dir = Path(tmp) / "_custom_scenarios" / registered_name
                assert (scenario_dir / "scenario.py").exists()
                assert (scenario_dir / "spec.json").exists()
                assert (scenario_dir / "scenario_type.txt").read_text() == "workflow"
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

    def test_agent_task_creation_uses_family_pipeline_spec_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response_text = _mock_llm_response(SAMPLE_SPEC)

        def mock_llm(system: str, user: str) -> str:
            return response_text

        monkeypatch.setattr(
            "autocontext.scenarios.custom.agent_task_creator.validate_for_family",
            lambda family_name, spec: ["pipeline rejected spec"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            with pytest.raises(ValueError, match="pipeline rejected spec"):
                creator.create("Write a haiku about testing software")

    def test_simulation_creation_uses_family_pipeline_source_validation(self, monkeypatch: pytest.MonkeyPatch) -> None:
        response_text = _mock_simulation_response()

        def mock_llm(system: str, user: str) -> str:
            return response_text

        monkeypatch.setattr(
            "autocontext.scenarios.custom.generic_creator.validate_source_for_family",
            lambda family_name, source: ["pipeline rejected simulation source"],
        )

        with tempfile.TemporaryDirectory() as tmp:
            creator = AgentTaskCreator(
                llm_fn=mock_llm,
                knowledge_root=Path(tmp),
            )
            with pytest.raises(ValueError, match="pipeline rejected simulation source"):
                creator.create("Build a stateful API orchestration workflow with rollback")


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

    def test_structured_sample_input_survives_execution_validation(self) -> None:
        from autocontext.scenarios.custom.agent_task_codegen import generate_agent_task_class
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec

        spec = AgentTaskSpec(
            task_prompt="Design a clinical trial protocol from the provided study brief.",
            judge_rubric="Evaluate statistical rigor and regulatory alignment",
            sample_input={
                "indication": "oncology",
                "phase": "II",
                "jurisdiction": "FDA",
            },  # type: ignore[arg-type]
        )
        source = generate_agent_task_class(spec, name="clinical_trial_protocol")
        errors = validate_execution(source)
        assert errors == []

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

    def test_inline_data_after_analyze_the_following_passes(self) -> None:
        """AC-279: 'Analyze the following' with inline data should NOT trigger false positive."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt=(
                "Analyze the following patient profile:\n\n"
                "Name: John Smith\n"
                "Age: 45\n"
                "Medications: Warfarin, Metformin, Lisinopril\n"
                "Conditions: Atrial fibrillation, Type 2 diabetes, Hypertension\n\n"
                "Identify potential drug interactions and risk factors."
            ),
            judge_rubric="Evaluate completeness and accuracy of drug interaction analysis",
        )
        errors = validate_spec(spec)
        assert not any("sample_input" in e for e in errors)

    def test_inline_json_data_passes(self) -> None:
        """AC-279: Prompt with inline JSON data should pass without sample_input."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt=(
                "Based on the data below:\n\n"
                "```json\n"
                '{"gdp_growth": 2.1, "inflation": 3.5, "unemployment": 4.2}\n'
                "```\n\n"
                "Provide an economic outlook assessment."
            ),
            judge_rubric="Evaluate economic analysis quality",
        )
        errors = validate_spec(spec)
        assert not any("sample_input" in e for e in errors)

    def test_inline_bullet_data_passes(self) -> None:
        """AC-279: Prompt with inline bullet-list data should pass."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt=(
                "Given the following data points:\n\n"
                "- Revenue: $5.2M (+12% YoY)\n"
                "- Operating costs: $3.8M (+5% YoY)\n"
                "- Customer churn: 8.3%\n"
                "- NPS: 42\n\n"
                "Write a quarterly business review."
            ),
            judge_rubric="Evaluate business analysis",
        )
        errors = validate_spec(spec)
        assert not any("sample_input" in e for e in errors)

    def test_truly_external_data_still_fails(self) -> None:
        """AC-279: Prompts referencing external data without providing it should still fail."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt="You will be provided with a customer spreadsheet. Analyze it.",
            judge_rubric="Evaluate analysis",
        )
        errors = validate_spec(spec)
        assert any("sample_input" in e for e in errors)

    def test_using_the_provided_still_fails(self) -> None:
        """AC-279: 'Using the provided' without inline data should still fail."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt="Using the provided dataset, perform clustering analysis.",
            judge_rubric="Evaluate clustering quality",
        )
        errors = validate_spec(spec)
        assert any("sample_input" in e for e in errors)

    def test_using_the_provided_with_inline_data_passes(self) -> None:
        """AC-279: 'Using the provided' should pass when the payload is inline."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt=(
                "Using the provided incident timeline below:\n\n"
                "- 09:03 UTC: elevated 500s on checkout\n"
                "- 09:06 UTC: deploy completed in us-east-1\n"
                "- 09:11 UTC: rollback started\n\n"
                "Write an incident summary and likely root cause."
            ),
            judge_rubric="Evaluate incident analysis quality",
        )
        errors = validate_spec(spec)
        assert not any("sample_input" in e for e in errors)

    def test_long_plain_prose_still_requires_sample_input(self) -> None:
        """AC-279: Long prose after a trigger phrase is not enough to count as inline data."""
        from autocontext.scenarios.custom.agent_task_spec import AgentTaskSpec
        from autocontext.scenarios.custom.agent_task_validator import validate_spec

        spec = AgentTaskSpec(
            task_prompt=(
                "Analyze the following customer complaint and explain the refund exposure, "
                "escalation path, contract risk, support obligations, and recommended next step."
            ),
            judge_rubric="Evaluate complaint analysis quality",
        )
        errors = validate_spec(spec)
        assert any("sample_input" in e for e in errors)


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
