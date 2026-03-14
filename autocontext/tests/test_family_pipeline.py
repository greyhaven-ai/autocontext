"""Tests for AC-247: Family-specific generator and validator pipelines.

Validates FamilyPipeline ABC, per-family pipeline registry,
family-specific spec/source/contract validation, and routing that
refuses to silently collapse unsupported families.
"""

from __future__ import annotations

from typing import Any

import pytest

from autocontext.scenarios.custom.family_pipeline import (
    PIPELINE_REGISTRY,
    FamilyContractError,
    FamilyPipeline,
    UnsupportedFamilyError,
    get_pipeline,
    has_pipeline,
    register_pipeline,
    validate_for_family,
    validate_source_for_family,
)

# ---------------------------------------------------------------------------
# FamilyPipeline ABC
# ---------------------------------------------------------------------------


class TestFamilyPipelineABC:
    def test_cannot_instantiate(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            FamilyPipeline()  # type: ignore[abstract]

    def test_concrete_subclass(self) -> None:
        class _Stub(FamilyPipeline):
            @property
            def family_name(self) -> str:
                return "_stub"

            def required_spec_fields(self) -> set[str]:
                return {"prompt"}

            def validate_spec(self, spec: dict[str, Any]) -> list[str]:
                return []

            def validate_source(self, source: str) -> list[str]:
                return []

            def validate_contract(self, source: str) -> list[str]:
                return []

        stub = _Stub()
        assert stub.family_name == "_stub"
        assert stub.required_spec_fields() == {"prompt"}


# ---------------------------------------------------------------------------
# Pipeline registry
# ---------------------------------------------------------------------------


class TestPipelineRegistry:
    def test_has_pipeline_for_agent_task(self) -> None:
        assert has_pipeline("agent_task") is True

    def test_has_pipeline_for_simulation(self) -> None:
        assert has_pipeline("simulation") is True

    def test_has_pipeline_returns_false_for_unknown(self) -> None:
        assert has_pipeline("nonexistent") is False

    def test_get_pipeline_agent_task(self) -> None:
        pipeline = get_pipeline("agent_task")
        assert pipeline.family_name == "agent_task"

    def test_get_pipeline_simulation(self) -> None:
        pipeline = get_pipeline("simulation")
        assert pipeline.family_name == "simulation"

    def test_get_pipeline_unknown_raises(self) -> None:
        with pytest.raises(UnsupportedFamilyError) as exc_info:
            get_pipeline("nonexistent")
        err = exc_info.value
        assert err.family_name == "nonexistent"
        assert isinstance(err.available_pipelines, list)
        assert "agent_task" in err.available_pipelines

    def test_register_custom_pipeline(self) -> None:
        class _Custom(FamilyPipeline):
            @property
            def family_name(self) -> str:
                return "_test_custom_pipeline"

            def required_spec_fields(self) -> set[str]:
                return set()

            def validate_spec(self, spec: dict[str, Any]) -> list[str]:
                return []

            def validate_source(self, source: str) -> list[str]:
                return []

            def validate_contract(self, source: str) -> list[str]:
                return []

        pipeline = _Custom()
        register_pipeline(pipeline)
        try:
            assert has_pipeline("_test_custom_pipeline")
            assert get_pipeline("_test_custom_pipeline") is pipeline
        finally:
            PIPELINE_REGISTRY.pop("_test_custom_pipeline", None)

    def test_register_duplicate_raises(self) -> None:
        class _Dup(FamilyPipeline):
            @property
            def family_name(self) -> str:
                return "_test_dup"

            def required_spec_fields(self) -> set[str]:
                return set()

            def validate_spec(self, spec: dict[str, Any]) -> list[str]:
                return []

            def validate_source(self, source: str) -> list[str]:
                return []

            def validate_contract(self, source: str) -> list[str]:
                return []

        pipeline = _Dup()
        register_pipeline(pipeline)
        try:
            with pytest.raises(ValueError, match="already registered"):
                register_pipeline(pipeline)
        finally:
            PIPELINE_REGISTRY.pop("_test_dup", None)


# ---------------------------------------------------------------------------
# UnsupportedFamilyError
# ---------------------------------------------------------------------------


class TestUnsupportedFamilyError:
    def test_carries_family_name(self) -> None:
        err = UnsupportedFamilyError("mystery_family", available_pipelines=["agent_task", "simulation"])
        assert err.family_name == "mystery_family"
        assert err.available_pipelines == ["agent_task", "simulation"]
        assert "mystery_family" in str(err)
        assert "agent_task" in str(err)

    def test_no_silent_collapse(self) -> None:
        """Core requirement from AC-247 comment: no silent collapse into agent_task."""
        with pytest.raises(UnsupportedFamilyError):
            get_pipeline("mystery_family")


# ---------------------------------------------------------------------------
# Agent task pipeline — spec validation
# ---------------------------------------------------------------------------


class TestAgentTaskSpecValidation:
    def test_valid_spec_passes(self) -> None:
        spec = {
            "task_prompt": "Evaluate the code for correctness",
            "judge_rubric": "Score on correctness and clarity",
        }
        errors = validate_for_family("agent_task", spec)
        assert errors == []

    def test_missing_task_prompt(self) -> None:
        spec = {"judge_rubric": "Score quality"}
        errors = validate_for_family("agent_task", spec)
        assert any("task_prompt" in e for e in errors)

    def test_missing_judge_rubric(self) -> None:
        spec = {"task_prompt": "Write an essay"}
        errors = validate_for_family("agent_task", spec)
        assert any("judge_rubric" in e for e in errors)

    def test_empty_prompt_fails(self) -> None:
        spec = {"task_prompt": "", "judge_rubric": "Score quality"}
        errors = validate_for_family("agent_task", spec)
        assert any("task_prompt" in e and "empty" in e for e in errors)

    def test_invalid_output_format(self) -> None:
        spec = {
            "task_prompt": "Generate code",
            "judge_rubric": "Score quality",
            "output_format": "invalid_format",
        }
        errors = validate_for_family("agent_task", spec)
        assert any("output_format" in e for e in errors)

    def test_valid_output_formats_accepted(self) -> None:
        for fmt in ("free_text", "code", "json_schema"):
            spec = {
                "task_prompt": "Generate something",
                "judge_rubric": "Score quality",
                "output_format": fmt,
            }
            errors = validate_for_family("agent_task", spec)
            assert errors == [], f"Format {fmt} should be valid"

    def test_required_spec_fields(self) -> None:
        pipeline = get_pipeline("agent_task")
        fields = pipeline.required_spec_fields()
        assert "task_prompt" in fields
        assert "judge_rubric" in fields


# ---------------------------------------------------------------------------
# Simulation pipeline — spec validation
# ---------------------------------------------------------------------------


class TestSimulationSpecValidation:
    def test_valid_spec_passes(self) -> None:
        spec = {
            "description": "Recover a multi-step API workflow.",
            "environment_description": "Orchestrate API calls across microservices",
            "initial_state_description": "No actions have completed yet.",
            "actions": [
                {"name": "call_api", "description": "Call an API endpoint", "parameters": {"url": "str"}},
            ],
            "success_criteria": ["all endpoints responding"],
            "failure_modes": ["partial side effects"],
            "max_steps": 8,
        }
        errors = validate_for_family("simulation", spec)
        assert errors == []

    def test_missing_description(self) -> None:
        spec = {
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [{"name": "a", "description": "b", "parameters": {}}],
            "success_criteria": ["done"],
        }
        errors = validate_for_family("simulation", spec)
        assert any("description" in e for e in errors)

    def test_missing_actions(self) -> None:
        spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "success_criteria": ["done"],
        }
        errors = validate_for_family("simulation", spec)
        assert any("actions" in e for e in errors)

    def test_empty_actions_list(self) -> None:
        spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [],
            "success_criteria": ["done"],
        }
        errors = validate_for_family("simulation", spec)
        assert any("actions" in e and "empty" in e for e in errors)

    def test_action_missing_name(self) -> None:
        spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [{"description": "no name", "parameters": {}}],
            "success_criteria": ["done"],
        }
        errors = validate_for_family("simulation", spec)
        assert any("name" in e for e in errors)

    def test_missing_success_criteria(self) -> None:
        spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [{"name": "a", "description": "b", "parameters": {}}],
        }
        errors = validate_for_family("simulation", spec)
        assert any("success_criteria" in e for e in errors)

    def test_invalid_max_steps(self) -> None:
        spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [{"name": "a", "description": "b", "parameters": {}}],
            "success_criteria": ["done"],
            "max_steps": 0,
        }
        errors = validate_for_family("simulation", spec)
        assert any("max_steps" in e for e in errors)

    def test_required_spec_fields(self) -> None:
        pipeline = get_pipeline("simulation")
        fields = pipeline.required_spec_fields()
        assert "description" in fields
        assert "initial_state_description" in fields
        assert "actions" in fields
        assert "success_criteria" in fields


# ---------------------------------------------------------------------------
# Cross-family contract mismatch
# ---------------------------------------------------------------------------


class TestCrossFamilyMismatch:
    def test_agent_task_spec_through_simulation_pipeline(self) -> None:
        """An agent_task spec should fail simulation validation."""
        agent_task_spec = {
            "task_prompt": "Write an essay",
            "judge_rubric": "Score quality",
        }
        errors = validate_for_family("simulation", agent_task_spec)
        assert len(errors) > 0, "Agent task spec should fail simulation validation"

    def test_simulation_spec_through_agent_task_pipeline(self) -> None:
        """A simulation spec should fail agent_task validation."""
        sim_spec = {
            "description": "Recover workflow",
            "environment_description": "desc",
            "initial_state_description": "initial state",
            "actions": [{"name": "a", "description": "b", "parameters": {}}],
            "success_criteria": ["done"],
        }
        errors = validate_for_family("agent_task", sim_spec)
        assert len(errors) > 0, "Simulation spec should fail agent_task validation"


# ---------------------------------------------------------------------------
# Source validation — contract checks
# ---------------------------------------------------------------------------


class TestAgentTaskSourceValidation:
    def test_valid_source(self) -> None:
        source = '''
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

class MyTask(AgentTaskInterface):
    def get_task_prompt(self, state):
        return "prompt"
    def evaluate_output(self, output, state, **kwargs):
        return AgentTaskResult(score=0.5, reasoning="ok")
    def get_rubric(self):
        return "rubric"
    def initial_state(self, seed=None):
        return {}
    def describe_task(self):
        return "test"
'''
        errors = validate_source_for_family("agent_task", source)
        assert errors == []

    def test_missing_interface_subclass(self) -> None:
        source = '''
class NotATask:
    pass
'''
        errors = validate_source_for_family("agent_task", source)
        assert any("AgentTaskInterface" in e for e in errors)

    def test_syntax_error(self) -> None:
        source = "def broken("
        errors = validate_source_for_family("agent_task", source)
        assert any("syntax" in e.lower() or "parse" in e.lower() for e in errors)

    def test_missing_required_methods(self) -> None:
        source = '''
from autocontext.scenarios.agent_task import AgentTaskInterface

class IncompleteTask(AgentTaskInterface):
    def get_task_prompt(self, state):
        return "prompt"
'''
        errors = validate_source_for_family("agent_task", source)
        assert any("missing required methods" in e for e in errors)


class TestSimulationSourceValidation:
    def test_valid_source(self) -> None:
        source = '''
from autocontext.scenarios.simulation import (
    Action,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationInterface,
    SimulationResult,
)

class MySim(SimulationInterface):
    name = "my_sim"
    def describe_scenario(self):
        return "scenario"
    def describe_environment(self):
        return EnvironmentSpec(
            name="my_sim",
            description="desc",
            available_actions=[ActionSpec(name="step", description="do step", parameters={})],
            initial_state_description="start",
            success_criteria=["done"],
        )
    def initial_state(self, seed=None):
        return {}
    def get_available_actions(self, state):
        return self.describe_environment().available_actions
    def execute_action(self, state, action):
        return ActionResult(success=True, output="ok", state_changes={}), state
    def is_terminal(self, state):
        return True
    def evaluate_trace(self, trace, final_state):
        return SimulationResult(
            score=1.0,
            reasoning="ok",
            dimension_scores={},
            workflow_complete=True,
            actions_taken=0,
            actions_successful=0,
        )
    def get_rubric(self):
        return "rubric"
'''
        errors = validate_source_for_family("simulation", source)
        assert errors == []

    def test_missing_interface_subclass(self) -> None:
        source = '''
class NotASim:
    pass
'''
        errors = validate_source_for_family("simulation", source)
        assert any("SimulationInterface" in e for e in errors)

    def test_missing_required_methods(self) -> None:
        source = '''
from autocontext.scenarios.simulation import SimulationInterface

class IncompleteSim(SimulationInterface):
    name = "my_sim"
    def describe_scenario(self):
        return "scenario"
'''
        errors = validate_source_for_family("simulation", source)
        assert any("missing required methods" in e for e in errors)


# ---------------------------------------------------------------------------
# FamilyContractError
# ---------------------------------------------------------------------------


class TestFamilyContractError:
    def test_construction(self) -> None:
        err = FamilyContractError(
            family_name="simulation",
            errors=["missing execute_action", "missing evaluate_trace"],
        )
        assert err.family_name == "simulation"
        assert len(err.errors) == 2
        assert "simulation" in str(err)


# ---------------------------------------------------------------------------
# validate_for_family routing to unsupported family
# ---------------------------------------------------------------------------


class TestValidateRouting:
    def test_validate_unsupported_family_raises(self) -> None:
        with pytest.raises(UnsupportedFamilyError):
            validate_for_family("nonexistent", {"key": "val"})

    def test_validate_source_unsupported_family_raises(self) -> None:
        with pytest.raises(UnsupportedFamilyError):
            validate_source_for_family("nonexistent", "class Foo: pass")
