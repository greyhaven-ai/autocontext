"""Tests for AC-243: Simulation-style scenario contract for action-trace evaluation.

Defines and validates the SimulationInterface ABC and its supporting data models
(ActionSpec, Action, ActionResult, ActionRecord, ActionTrace, EnvironmentSpec,
SimulationResult) for scenarios where agents interact with mock environments
and are judged on action traces rather than prose quality.
"""

from __future__ import annotations

from typing import Any

import pytest

from autocontext.scenarios.simulation import (
    Action,
    ActionRecord,
    ActionResult,
    ActionSpec,
    ActionTrace,
    EnvironmentSpec,
    SimulationInterface,
    SimulationResult,
)

# ---------------------------------------------------------------------------
# Data model construction
# ---------------------------------------------------------------------------


class TestActionSpec:
    def test_construction(self) -> None:
        spec = ActionSpec(
            name="api_call",
            description="Call an API endpoint",
            parameters={"url": "string", "method": "string"},
        )
        assert spec.name == "api_call"
        assert spec.description == "Call an API endpoint"
        assert spec.parameters == {"url": "string", "method": "string"}
        assert spec.preconditions == []
        assert spec.effects == []

    def test_with_preconditions_and_effects(self) -> None:
        spec = ActionSpec(
            name="deploy",
            description="Deploy service",
            parameters={"service": "string"},
            preconditions=["service_built", "tests_passing"],
            effects=["service_deployed"],
        )
        assert spec.preconditions == ["service_built", "tests_passing"]
        assert spec.effects == ["service_deployed"]


class TestAction:
    def test_construction(self) -> None:
        action = Action(name="api_call", parameters={"url": "/users", "method": "GET"})
        assert action.name == "api_call"
        assert action.parameters == {"url": "/users", "method": "GET"}
        assert action.reasoning == ""

    def test_with_reasoning(self) -> None:
        action = Action(
            name="rollback",
            parameters={"version": "v1.2"},
            reasoning="Deployment failed, rolling back to stable version",
        )
        assert action.reasoning == "Deployment failed, rolling back to stable version"


class TestActionResult:
    def test_success(self) -> None:
        result = ActionResult(
            success=True,
            output='{"status": "ok"}',
            state_changes={"deployed": True},
        )
        assert result.success is True
        assert result.error == ""
        assert result.side_effects == []

    def test_failure(self) -> None:
        result = ActionResult(
            success=False,
            output="",
            state_changes={},
            error="Connection timeout",
            side_effects=["partial_write"],
        )
        assert result.success is False
        assert result.error == "Connection timeout"
        assert result.side_effects == ["partial_write"]


class TestActionRecord:
    def test_construction(self) -> None:
        record = ActionRecord(
            step=0,
            action=Action(name="check_status", parameters={}),
            result=ActionResult(success=True, output="ok", state_changes={}),
            state_before={"service_up": False},
            state_after={"service_up": True},
        )
        assert record.step == 0
        assert record.action.name == "check_status"
        assert record.result.success is True
        assert record.state_before == {"service_up": False}
        assert record.state_after == {"service_up": True}


# ---------------------------------------------------------------------------
# ActionTrace
# ---------------------------------------------------------------------------


class TestActionTrace:
    def _make_trace(self, n: int = 3, failures: int = 0) -> ActionTrace:
        records = []
        for i in range(n):
            success = i >= failures  # first `failures` records fail
            records.append(
                ActionRecord(
                    step=i,
                    action=Action(name=f"step_{i}", parameters={"i": i}),
                    result=ActionResult(
                        success=success,
                        output=f"result_{i}",
                        state_changes={"step": i},
                        error="" if success else "failed",
                    ),
                    state_before={"step": i - 1 if i > 0 else -1},
                    state_after={"step": i},
                )
            )
        return ActionTrace(records=records)

    def test_actions_property(self) -> None:
        trace = self._make_trace(3)
        actions = trace.actions
        assert len(actions) == 3
        assert [a.name for a in actions] == ["step_0", "step_1", "step_2"]

    def test_success_rate_all_pass(self) -> None:
        trace = self._make_trace(4, failures=0)
        assert trace.success_rate == 1.0

    def test_success_rate_some_fail(self) -> None:
        trace = self._make_trace(4, failures=2)
        assert trace.success_rate == 0.5

    def test_success_rate_empty(self) -> None:
        trace = ActionTrace(records=[])
        assert trace.success_rate == 0.0

    def test_to_dict_from_dict_roundtrip(self) -> None:
        trace = self._make_trace(2, failures=1)
        data = trace.to_dict()
        restored = ActionTrace.from_dict(data)
        assert len(restored.records) == 2
        assert restored.records[0].action.name == "step_0"
        assert restored.records[0].result.success is False
        assert restored.records[1].result.success is True
        assert restored.success_rate == trace.success_rate

    def test_to_dict_structure(self) -> None:
        trace = self._make_trace(1)
        data = trace.to_dict()
        assert "records" in data
        assert len(data["records"]) == 1
        rec = data["records"][0]
        assert "step" in rec
        assert "action" in rec
        assert "result" in rec
        assert "state_before" in rec
        assert "state_after" in rec


# ---------------------------------------------------------------------------
# EnvironmentSpec
# ---------------------------------------------------------------------------


class TestEnvironmentSpec:
    def test_construction(self) -> None:
        env = EnvironmentSpec(
            name="api_orchestration",
            description="Orchestrate microservice API calls",
            available_actions=[
                ActionSpec(name="call", description="Call endpoint", parameters={"url": "str"}),
            ],
            initial_state_description="All services healthy",
            success_criteria=["all endpoints responding", "data consistent"],
        )
        assert env.name == "api_orchestration"
        assert len(env.available_actions) == 1
        assert len(env.success_criteria) == 2
        assert env.failure_modes == []

    def test_with_failure_modes(self) -> None:
        env = EnvironmentSpec(
            name="deployment",
            description="Deploy services",
            available_actions=[],
            initial_state_description="Clean state",
            success_criteria=["deployed"],
            failure_modes=["timeout", "dependency_conflict", "rollback_failure"],
        )
        assert len(env.failure_modes) == 3


# ---------------------------------------------------------------------------
# SimulationResult
# ---------------------------------------------------------------------------


class TestSimulationResult:
    def test_construction(self) -> None:
        result = SimulationResult(
            score=0.85,
            reasoning="Good workflow completion with minor ordering issues",
            dimension_scores={
                "completion": 0.95,
                "ordering": 0.75,
                "recovery": 0.85,
            },
            workflow_complete=True,
            actions_taken=10,
            actions_successful=9,
        )
        assert result.score == 0.85
        assert result.workflow_complete is True
        assert result.actions_taken == 10
        assert result.recovery_attempts == 0
        assert result.rollback_quality == 0.0

    def test_to_dict_from_dict_roundtrip(self) -> None:
        result = SimulationResult(
            score=0.7,
            reasoning="Partial completion",
            dimension_scores={"completion": 0.6, "recovery": 0.8},
            workflow_complete=False,
            actions_taken=5,
            actions_successful=3,
            recovery_attempts=2,
            rollback_quality=0.6,
        )
        data = result.to_dict()
        restored = SimulationResult.from_dict(data)
        assert restored.score == result.score
        assert restored.reasoning == result.reasoning
        assert restored.dimension_scores == result.dimension_scores
        assert restored.workflow_complete == result.workflow_complete
        assert restored.recovery_attempts == result.recovery_attempts
        assert restored.rollback_quality == result.rollback_quality


# ---------------------------------------------------------------------------
# SimulationInterface ABC
# ---------------------------------------------------------------------------


class _MockSimulation(SimulationInterface):
    """Concrete test implementation of SimulationInterface."""

    name = "mock_sim"

    def __init__(self) -> None:
        self._fault_steps: set[int] = set()

    def describe_scenario(self) -> str:
        return "Mock simulation for testing"

    def describe_environment(self) -> EnvironmentSpec:
        return EnvironmentSpec(
            name="mock_env",
            description="A mock environment",
            available_actions=[
                ActionSpec(name="ping", description="Ping a service", parameters={"target": "str"}),
                ActionSpec(name="deploy", description="Deploy", parameters={"service": "str"}),
            ],
            initial_state_description="Clean state",
            success_criteria=["all_deployed"],
        )

    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        return {"seed": seed or 0, "deployed": [], "step": 0, "errors": []}

    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        actions = [
            ActionSpec(name="ping", description="Ping", parameters={"target": "str"}),
            ActionSpec(name="deploy", description="Deploy", parameters={"service": "str"}),
        ]
        if state.get("errors"):
            actions.append(ActionSpec(name="rollback", description="Rollback", parameters={}))
        return actions

    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        new_state = {**state, "step": state["step"] + 1}
        if action.name == "ping":
            return ActionResult(success=True, output="pong", state_changes={}), new_state
        if action.name == "deploy":
            service = action.parameters.get("service", "unknown")
            new_state["deployed"] = [*state.get("deployed", []), service]
            return (
                ActionResult(success=True, output=f"deployed {service}", state_changes={"deployed": service}),
                new_state,
            )
        if action.name == "rollback":
            new_state["deployed"] = []
            new_state["errors"] = []
            return ActionResult(success=True, output="rolled back", state_changes={"deployed": []}), new_state
        return (
            ActionResult(success=False, output="", state_changes={}, error=f"unknown action: {action.name}"),
            new_state,
        )

    def is_terminal(self, state: dict[str, Any]) -> bool:
        return len(state.get("deployed", [])) >= 2 or state.get("step", 0) >= 10

    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        deployed = final_state.get("deployed", [])
        complete = len(deployed) >= 2
        completion_score = min(len(deployed) / 2, 1.0)
        ordering_score = 1.0 if trace.success_rate == 1.0 else 0.5
        recovery_score = 1.0
        for rec in trace.records:
            if not rec.result.success and rec.action.name != "rollback":
                # Check if next action was a recovery
                next_idx = rec.step + 1
                if next_idx < len(trace.records) and trace.records[next_idx].action.name == "rollback":
                    recovery_score = 0.8
                else:
                    recovery_score = 0.3

        score = completion_score * 0.5 + ordering_score * 0.3 + recovery_score * 0.2
        return SimulationResult(
            score=score,
            reasoning=f"Deployed {len(deployed)} services",
            dimension_scores={"completion": completion_score, "ordering": ordering_score, "recovery": recovery_score},
            workflow_complete=complete,
            actions_taken=len(trace.records),
            actions_successful=sum(1 for r in trace.records if r.result.success),
            recovery_attempts=sum(1 for r in trace.records if r.action.name == "rollback"),
            rollback_quality=1.0 if not final_state.get("errors") else 0.0,
        )

    def get_rubric(self) -> str:
        return (
            "Evaluate on: workflow completion (50%), action ordering (30%), "
            "error recovery (20%)"
        )

    def inject_fault(self, state: dict[str, Any], step: int) -> dict[str, Any]:
        if step in self._fault_steps:
            return {**state, "errors": [*state.get("errors", []), f"fault_at_step_{step}"]}
        return state


class TestSimulationInterfaceABC:
    def test_cannot_instantiate_abc(self) -> None:
        with pytest.raises(TypeError, match="abstract"):
            SimulationInterface()  # type: ignore[abstract]

    def test_concrete_subclass_instantiates(self) -> None:
        sim = _MockSimulation()
        assert sim.name == "mock_sim"

    def test_describe_scenario(self) -> None:
        sim = _MockSimulation()
        assert "Mock simulation" in sim.describe_scenario()

    def test_describe_environment(self) -> None:
        sim = _MockSimulation()
        env = sim.describe_environment()
        assert isinstance(env, EnvironmentSpec)
        assert env.name == "mock_env"
        assert len(env.available_actions) == 2

    def test_initial_state(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state(seed=42)
        assert state["seed"] == 42
        assert state["deployed"] == []
        assert state["step"] == 0

    def test_get_available_actions(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state()
        actions = sim.get_available_actions(state)
        assert len(actions) == 2
        assert {a.name for a in actions} == {"ping", "deploy"}

    def test_get_available_actions_with_errors(self) -> None:
        sim = _MockSimulation()
        state = {"errors": ["something_broke"], "deployed": []}
        actions = sim.get_available_actions(state)
        assert {a.name for a in actions} == {"ping", "deploy", "rollback"}

    def test_execute_action(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state()
        result, new_state = sim.execute_action(state, Action(name="ping", parameters={"target": "svc_a"}))
        assert result.success is True
        assert result.output == "pong"
        assert new_state["step"] == 1

    def test_execute_deploy(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state()
        result, new_state = sim.execute_action(state, Action(name="deploy", parameters={"service": "svc_a"}))
        assert result.success is True
        assert "svc_a" in new_state["deployed"]

    def test_execute_unknown_action(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state()
        result, new_state = sim.execute_action(state, Action(name="explode", parameters={}))
        assert result.success is False
        assert "unknown action" in result.error

    def test_is_terminal_false(self) -> None:
        sim = _MockSimulation()
        state = sim.initial_state()
        assert sim.is_terminal(state) is False

    def test_is_terminal_after_deployments(self) -> None:
        sim = _MockSimulation()
        state = {"deployed": ["svc_a", "svc_b"], "step": 2}
        assert sim.is_terminal(state) is True

    def test_is_terminal_after_max_steps(self) -> None:
        sim = _MockSimulation()
        state = {"deployed": [], "step": 10}
        assert sim.is_terminal(state) is True

    def test_get_rubric(self) -> None:
        sim = _MockSimulation()
        rubric = sim.get_rubric()
        assert "completion" in rubric
        assert "ordering" in rubric
        assert "recovery" in rubric


# ---------------------------------------------------------------------------
# End-to-end simulation run
# ---------------------------------------------------------------------------


class TestEndToEndSimulation:
    def test_successful_workflow(self) -> None:
        """Agent deploys two services successfully."""
        sim = _MockSimulation()
        state = sim.initial_state(seed=1)
        records: list[ActionRecord] = []

        actions_to_take = [
            Action(name="ping", parameters={"target": "svc_a"}),
            Action(name="deploy", parameters={"service": "svc_a"}),
            Action(name="deploy", parameters={"service": "svc_b"}),
        ]

        for i, action in enumerate(actions_to_take):
            state_before = dict(state)
            result, state = sim.execute_action(state, action)
            records.append(ActionRecord(
                step=i, action=action, result=result,
                state_before=state_before, state_after=dict(state),
            ))
            if sim.is_terminal(state):
                break

        trace = ActionTrace(records=records)
        sim_result = sim.evaluate_trace(trace, state)

        assert sim_result.workflow_complete is True
        assert sim_result.score > 0.8
        assert sim_result.actions_taken == 3
        assert sim_result.actions_successful == 3
        assert sim_result.dimension_scores["completion"] == 1.0

    def test_workflow_with_recovery(self) -> None:
        """Agent encounters an error and rolls back."""
        sim = _MockSimulation()
        state = sim.initial_state()
        records: list[ActionRecord] = []

        # Force an error via unknown action, then recover
        actions_to_take = [
            Action(name="explode", parameters={}),  # fails
            Action(name="rollback", parameters={}),  # recovery
            Action(name="deploy", parameters={"service": "svc_a"}),
            Action(name="deploy", parameters={"service": "svc_b"}),
        ]

        for i, action in enumerate(actions_to_take):
            state_before = dict(state)
            result, state = sim.execute_action(state, action)
            records.append(ActionRecord(
                step=i, action=action, result=result,
                state_before=state_before, state_after=dict(state),
            ))
            if sim.is_terminal(state):
                break

        trace = ActionTrace(records=records)
        sim_result = sim.evaluate_trace(trace, state)

        assert sim_result.workflow_complete is True
        assert sim_result.recovery_attempts >= 1
        assert sim_result.actions_taken == 4
        # Score should be lower than perfect due to the failure
        assert sim_result.score < 1.0

    def test_incomplete_workflow(self) -> None:
        """Agent only deploys one service."""
        sim = _MockSimulation()
        state = sim.initial_state()
        records: list[ActionRecord] = []

        action = Action(name="deploy", parameters={"service": "svc_a"})
        state_before = dict(state)
        result, state = sim.execute_action(state, action)
        records.append(ActionRecord(
            step=0, action=action, result=result,
            state_before=state_before, state_after=dict(state),
        ))

        trace = ActionTrace(records=records)
        sim_result = sim.evaluate_trace(trace, state)

        assert sim_result.workflow_complete is False
        assert sim_result.dimension_scores["completion"] == 0.5
        assert sim_result.score < 0.8


# ---------------------------------------------------------------------------
# AC-308: Empty actions list should not hard-fail
# ---------------------------------------------------------------------------


class TestEmptyActionsValidation:
    def test_empty_actions_list_passes_validation(self) -> None:
        """AC-308: An empty actions list should pass validation, not hard-fail."""
        sim = _MockSimulation()
        state = sim.initial_state(seed=1)
        valid, reason = sim.validate_actions(state, "challenger", {"actions": []})
        assert valid is True, f"Empty actions list should be accepted, got: {reason}"

    def test_missing_actions_key_still_fails(self) -> None:
        """A strategy without the 'actions' key at all is structurally invalid."""
        sim = _MockSimulation()
        state = sim.initial_state(seed=1)
        valid, _ = sim.validate_actions(state, "challenger", {"plan": "something"})
        assert valid is False

    def test_non_list_actions_still_fails(self) -> None:
        """Actions must be a list, not a string or other type."""
        sim = _MockSimulation()
        state = sim.initial_state(seed=1)
        valid, _ = sim.validate_actions(state, "challenger", {"actions": "deploy svc_a"})
        assert valid is False

    def test_empty_actions_produces_valid_result(self) -> None:
        """An empty actions list should execute without crashing and produce a result."""
        sim = _MockSimulation()
        result = sim.execute_match({"actions": []}, seed=1)
        assert 0.0 <= result.score <= 1.0
        assert result.summary  # has a summary string


# ---------------------------------------------------------------------------
# Default methods
# ---------------------------------------------------------------------------


class TestDefaultMethods:
    def test_validate_action_default(self) -> None:
        sim = _MockSimulation()
        valid, reason = sim.validate_action({}, Action(name="anything", parameters={}))
        assert valid is True
        assert reason == ""

    def test_max_steps_default(self) -> None:
        sim = _MockSimulation()
        assert sim.max_steps() == 50

    def test_inject_fault_default_noop(self) -> None:
        """Default inject_fault returns state unchanged."""
        sim = _MockSimulation()
        state = {"key": "value"}
        assert sim.inject_fault(state, 0) == state

    def test_inject_fault_override(self) -> None:
        """Mock's inject_fault adds errors for configured steps."""
        sim = _MockSimulation()
        sim._fault_steps = {2, 5}
        state = {"errors": []}
        modified = sim.inject_fault(state, 2)
        assert len(modified["errors"]) == 1
        assert "fault_at_step_2" in modified["errors"][0]
        # Step 3 should not inject
        unmodified = sim.inject_fault(state, 3)
        assert unmodified == state


# ---------------------------------------------------------------------------
# Registry compatibility
# ---------------------------------------------------------------------------


class TestRegistryCompatibility:
    def test_can_store_in_scenario_registry(self) -> None:
        """SimulationInterface subclass can be stored in SCENARIO_REGISTRY."""
        from autocontext.scenarios import SCENARIO_REGISTRY

        SCENARIO_REGISTRY["_test_mock_sim"] = _MockSimulation  # type: ignore[assignment]
        try:
            assert "_test_mock_sim" in SCENARIO_REGISTRY
            cls = SCENARIO_REGISTRY["_test_mock_sim"]
            instance = cls()
            assert hasattr(instance, "evaluate_trace")
            assert hasattr(instance, "execute_action")
        finally:
            del SCENARIO_REGISTRY["_test_mock_sim"]

    def test_detection_via_hasattr(self) -> None:
        """Simulation scenarios expose both simulation and base scenario hooks."""
        sim = _MockSimulation()
        # Simulation-specific methods
        assert hasattr(sim, "evaluate_trace")
        assert hasattr(sim, "execute_action")
        assert hasattr(sim, "describe_environment")
        assert hasattr(sim, "get_available_actions")
        # And now intentionally support the standard run-loop execution path.
        assert hasattr(sim, "execute_match")
        assert hasattr(sim, "validate_actions")
        assert hasattr(sim, "step")
        # Should NOT have agent-task-specific methods
        assert not hasattr(sim, "evaluate_output")
        assert not hasattr(sim, "get_task_prompt")

    def test_has_rubric_like_agent_task(self) -> None:
        """Simulation scenarios share get_rubric() for knowledge compatibility."""
        sim = _MockSimulation()
        assert hasattr(sim, "get_rubric")
        assert isinstance(sim.get_rubric(), str)

    def test_has_initial_state_like_both_interfaces(self) -> None:
        """initial_state() is shared across all scenario types."""
        sim = _MockSimulation()
        assert hasattr(sim, "initial_state")
        state = sim.initial_state()
        assert isinstance(state, dict)
