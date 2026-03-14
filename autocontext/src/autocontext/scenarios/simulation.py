"""Simulation-style scenario contract for action-trace evaluation (AC-243).

Defines the third scenario type: agents interact with mock environments
and are evaluated on their action traces (sequence correctness, recovery
behavior, rollback quality) rather than prose output.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any


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


class SimulationInterface(ABC):
    """Contract for simulation-style scenarios with action-trace evaluation.

    The third scenario type in AutoContext. Agents interact with a mock
    environment (APIs, filesystem, state machines) and are judged on their
    action traces — sequence correctness, recovery behavior, rollback quality,
    and dependency ordering — rather than prose quality alone.
    """

    name: str

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
    def is_terminal(self, state: dict[str, Any]) -> bool:
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
        """Maximum number of steps before forced termination."""
        return 50

    def inject_fault(self, state: dict[str, Any], step: int) -> dict[str, Any]:
        """Optionally inject faults to test recovery. Default: no-op."""
        return state
