"""Simulation-style scenario contract for action-trace evaluation (AC-243).

Simulation scenarios are real first-class scenarios: they register in the same
registry, execute through the normal run loop, and are judged from action
traces and terminal state rather than prose quality alone.
"""

from __future__ import annotations

from abc import abstractmethod
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.base import Observation, Result, ScenarioInterface


@dataclass(slots=True)
class ActionSpec:
    """Describes an available action in the simulation environment."""

    name: str
    description: str
    parameters: dict[str, str]
    preconditions: list[str] = field(default_factory=list)
    effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Action:
    """An action submitted by the agent."""

    name: str
    parameters: dict[str, Any]
    reasoning: str = ""


@dataclass(slots=True)
class ActionResult:
    """Result of executing a single action."""

    success: bool
    output: str
    state_changes: dict[str, Any]
    error: str = ""
    side_effects: list[str] = field(default_factory=list)


@dataclass(slots=True)
class ActionRecord:
    """A single entry in the action trace."""

    step: int
    action: Action
    result: ActionResult
    state_before: dict[str, Any]
    state_after: dict[str, Any]


@dataclass(slots=True)
class ActionTrace:
    """Complete record of all actions taken during a simulation."""

    records: list[ActionRecord]

    @property
    def actions(self) -> list[Action]:
        return [r.action for r in self.records]

    @property
    def success_rate(self) -> float:
        if not self.records:
            return 0.0
        return sum(1 for r in self.records if r.result.success) / len(self.records)

    def to_dict(self) -> dict[str, Any]:
        return {
            "records": [
                {
                    "step": r.step,
                    "action": {"name": r.action.name, "parameters": r.action.parameters, "reasoning": r.action.reasoning},
                    "result": {
                        "success": r.result.success,
                        "output": r.result.output,
                        "state_changes": r.result.state_changes,
                        "error": r.result.error,
                        "side_effects": r.result.side_effects,
                    },
                    "state_before": r.state_before,
                    "state_after": r.state_after,
                }
                for r in self.records
            ],
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActionTrace:
        records = []
        for rec in data["records"]:
            action = Action(
                name=rec["action"]["name"],
                parameters=rec["action"]["parameters"],
                reasoning=rec["action"].get("reasoning", ""),
            )
            result = ActionResult(
                success=rec["result"]["success"],
                output=rec["result"]["output"],
                state_changes=rec["result"]["state_changes"],
                error=rec["result"].get("error", ""),
                side_effects=rec["result"].get("side_effects", []),
            )
            records.append(
                ActionRecord(
                    step=rec["step"],
                    action=action,
                    result=result,
                    state_before=rec["state_before"],
                    state_after=rec["state_after"],
                )
            )
        return cls(records=records)


@dataclass(slots=True)
class EnvironmentSpec:
    """Describes the simulation environment."""

    name: str
    description: str
    available_actions: list[ActionSpec]
    initial_state_description: str
    success_criteria: list[str]
    failure_modes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class SimulationResult:
    """Result of evaluating a complete simulation trace."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    workflow_complete: bool
    actions_taken: int
    actions_successful: int
    recovery_attempts: int = 0
    rollback_quality: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "workflow_complete": self.workflow_complete,
            "actions_taken": self.actions_taken,
            "actions_successful": self.actions_successful,
            "recovery_attempts": self.recovery_attempts,
            "rollback_quality": self.rollback_quality,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            workflow_complete=data["workflow_complete"],
            actions_taken=data["actions_taken"],
            actions_successful=data["actions_successful"],
            recovery_attempts=data.get("recovery_attempts", 0),
            rollback_quality=data.get("rollback_quality", 0.0),
        )


class SimulationInterface(ScenarioInterface):
    """Scenario contract for action-trace evaluation."""

    @abstractmethod
    def describe_scenario(self) -> str:
        """Return a human-readable scenario description."""

    @abstractmethod
    def describe_environment(self) -> EnvironmentSpec:
        """Return the environment specification."""

    @abstractmethod
    def initial_state(self, seed: int | None = None) -> dict[str, Any]:
        """Create deterministic initial state."""

    @abstractmethod
    def get_available_actions(self, state: dict[str, Any]) -> list[ActionSpec]:
        """Return actions available in the current state."""

    @abstractmethod
    def execute_action(self, state: dict[str, Any], action: Action) -> tuple[ActionResult, dict[str, Any]]:
        """Execute an action, returning result and new state."""

    @abstractmethod
    def is_terminal(self, state: Mapping[str, Any]) -> bool:
        """Check if the simulation has ended."""

    @abstractmethod
    def evaluate_trace(self, trace: ActionTrace, final_state: dict[str, Any]) -> SimulationResult:
        """Evaluate the complete action trace."""

    @abstractmethod
    def get_rubric(self) -> str:
        """Return evaluation rubric for the simulation."""

    def validate_action(self, state: dict[str, Any], action: Action) -> tuple[bool, str]:
        """Validate an action before execution. Default: always valid."""
        return True, ""

    def max_steps(self) -> int:
        return 50

    def inject_fault(self, state: dict[str, Any], step: int) -> dict[str, Any]:
        return state

    def describe_rules(self) -> str:
        env = self.describe_environment()
        action_lines = "\n".join(f"- {action.name}: {action.description}" for action in env.available_actions)
        criteria = "\n".join(f"- {criterion}" for criterion in env.success_criteria)
        failure_modes = (
            "\n".join(f"- {failure}" for failure in env.failure_modes)
            if env.failure_modes
            else "- none explicitly modeled"
        )
        return (
            f"{self.describe_scenario()}\n\n"
            f"Environment: {env.description}\n"
            f"Initial state: {env.initial_state_description}\n\n"
            f"Available actions:\n{action_lines}\n\n"
            f"Success criteria:\n{criteria}\n\n"
            f"Known failure modes:\n{failure_modes}"
        )

    def describe_strategy_interface(self) -> str:
        action_names = ", ".join(action.name for action in self.describe_environment().available_actions)
        return (
            "Return JSON with an ordered action plan:\n"
            "{\n"
            '  "actions": [\n'
            '    {"name": "action_name", "parameters": {...}, "reasoning": "why this step now"}\n'
            "  ]\n"
            "}\n\n"
            f"Allowed action names: {action_names}\n"
            "The order matters. Use parameters required by the chosen action."
        )

    def describe_evaluation_criteria(self) -> str:
        return self.get_rubric()

    def get_world_state(self, state: Mapping[str, Any]) -> Any | None:
        """Return an optional structured world-state snapshot for this scenario."""
        raw = state.get("_world_state")
        if not isinstance(raw, Mapping):
            return None
        try:
            from autocontext.scenarios.world_state import WorldState

            return WorldState.from_dict(dict(raw))
        except (KeyError, TypeError, ValueError):
            return None

    def get_observation(self, state: Mapping[str, Any], player_id: str) -> Observation:
        available_actions = self.get_available_actions(dict(state))
        action_names = ", ".join(action.name for action in available_actions) or "none"
        trace = state.get("_simulation_trace", {"records": []})
        prior_steps = 0
        if isinstance(trace, dict) and isinstance(trace.get("records"), list):
            prior_steps = len(trace["records"])
        return Observation(
            narrative=(
                f"{player_id} is operating in a simulation environment. "
                f"Step={state.get('step', 0)}. Prior actions={prior_steps}. "
                f"Available actions: {action_names}."
            ),
            state=dict(state),
            constraints=[f"max_steps={self.max_steps()}"],
        )

    def validate_actions(self, state: Mapping[str, Any], player_id: str, actions: Mapping[str, Any]) -> tuple[bool, str]:
        del player_id
        plan = actions.get("actions")
        if not isinstance(plan, list) or not plan:
            return False, "strategy must contain a non-empty 'actions' list"
        available_names = {spec.name for spec in self.get_available_actions(dict(state))}
        for idx, raw_action in enumerate(plan):
            if not isinstance(raw_action, Mapping):
                return False, f"action {idx} must be an object"
            name = raw_action.get("name")
            if not isinstance(name, str) or not name:
                return False, f"action {idx} is missing a valid name"
            if name not in available_names:
                return False, f"action {idx} references unknown action '{name}'"
            params = raw_action.get("parameters", {})
            if not isinstance(params, Mapping):
                return False, f"action {idx} parameters must be an object"
            reasoning = raw_action.get("reasoning", "")
            if reasoning is not None and not isinstance(reasoning, str):
                return False, f"action {idx} reasoning must be a string"
        return True, "ok"

    def _coerce_action(self, raw_action: Mapping[str, Any]) -> Action:
        return Action(
            name=str(raw_action["name"]),
            parameters=dict(raw_action.get("parameters", {})),
            reasoning=str(raw_action.get("reasoning", "") or ""),
        )

    def _execute_plan(self, state: dict[str, Any], actions: Mapping[str, Any]) -> tuple[dict[str, Any], ActionTrace]:
        current_state = dict(state)
        records: list[ActionRecord] = []
        plan = actions.get("actions", [])
        if not isinstance(plan, list):
            return current_state, ActionTrace(records=[])
        for idx, raw_action in enumerate(plan[: self.max_steps()]):
            if not isinstance(raw_action, Mapping):
                continue
            current_state = self.inject_fault(current_state, idx)
            action = self._coerce_action(raw_action)
            state_before = dict(current_state)
            is_valid, reason = self.validate_action(current_state, action)
            if not is_valid:
                result = ActionResult(success=False, output="", state_changes={}, error=reason)
                next_state = dict(current_state)
            else:
                result, next_state = self.execute_action(current_state, action)
            records.append(
                ActionRecord(
                    step=idx,
                    action=action,
                    result=result,
                    state_before=state_before,
                    state_after=dict(next_state),
                )
            )
            current_state = dict(next_state)
            current_state["step"] = idx + 1
            if self.is_terminal(current_state):
                break
        trace = ActionTrace(records=records)
        current_state["_simulation_trace"] = trace.to_dict()
        current_state["terminal"] = self.is_terminal(current_state) or len(records) >= self.max_steps()
        return current_state, trace

    def step(self, state: Mapping[str, Any], actions: Mapping[str, Any]) -> dict[str, Any]:
        final_state, _trace = self._execute_plan(dict(state), actions)
        return final_state

    def get_result(self, state: Mapping[str, Any]) -> Result:
        trace_data = state.get("_simulation_trace", {"records": []})
        trace = ActionTrace.from_dict(trace_data) if isinstance(trace_data, dict) else ActionTrace(records=[])
        final_state = dict(state)
        sim_result = self.evaluate_trace(trace, final_state)
        return Result(
            score=sim_result.score,
            winner="challenger" if sim_result.score >= 0.5 else "incumbent",
            summary=sim_result.reasoning,
            replay=trace.to_dict()["records"],
            metrics={
                **sim_result.dimension_scores,
                "workflow_complete": 1.0 if sim_result.workflow_complete else 0.0,
                "actions_taken": float(sim_result.actions_taken),
                "actions_successful": float(sim_result.actions_successful),
                "recovery_attempts": float(sim_result.recovery_attempts),
                "rollback_quality": sim_result.rollback_quality,
            },
        )

    def replay_to_narrative(self, replay: list[dict[str, Any]]) -> str:
        if not replay:
            return "No simulation actions were recorded."
        rendered = []
        for record in replay:
            action = record.get("action", {})
            result = record.get("result", {})
            rendered.append(f"{action.get('name', 'unknown')} -> {'ok' if result.get('success') else 'failed'}")
        return " | ".join(rendered)

    def render_frame(self, state: Mapping[str, Any]) -> dict[str, Any]:
        frame = {
            "scenario": self.name,
            "state": dict(state),
            "available_actions": [
                {
                    "name": action.name,
                    "description": action.description,
                    "parameters": action.parameters,
                }
                for action in self.get_available_actions(dict(state))
            ],
        }
        world_state = self.get_world_state(state)
        if world_state is not None:
            frame["world_state"] = world_state.to_dict()
        return frame

    def execute_match(self, strategy: Mapping[str, Any], seed: int) -> Result:
        state = self.initial_state(seed=seed)
        valid, reason = self.validate_actions(state, "challenger", strategy)
        if not valid:
            return Result(
                score=0.0,
                winner="incumbent",
                summary="simulation plan rejected during validation",
                replay=[{"event": "validation_failed", "reason": reason}],
                metrics={"valid": 0.0},
                validation_errors=[reason],
            )
        next_state, trace = self._execute_plan(state, strategy)
        next_state["_simulation_trace"] = trace.to_dict()
        return self.get_result(next_state)
