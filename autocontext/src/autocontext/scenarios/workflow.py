"""Workflow scenario family with transactional evaluation (AC-249).

Workflow scenarios where agents execute multi-step transactional workflows
with retries, compensation/rollback, and side-effect tracking. Evaluated
on workflow completeness, compensation quality, and side-effect containment.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass
from typing import Any

from autocontext.scenarios.simulation import ActionResult, SimulationInterface


@dataclass(slots=True)
class WorkflowStep:
    """A single step in a transactional workflow."""

    name: str
    description: str
    idempotent: bool
    reversible: bool
    compensation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "idempotent": self.idempotent,
            "reversible": self.reversible,
            "compensation": self.compensation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        return cls(
            name=data["name"],
            description=data["description"],
            idempotent=data["idempotent"],
            reversible=data["reversible"],
            compensation=data.get("compensation"),
        )


@dataclass(slots=True)
class SideEffect:
    """A side effect produced by a workflow step."""

    step_name: str
    effect_type: str  # e.g., "payment", "notification", "external_api"
    description: str
    reversible: bool
    reversed: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "effect_type": self.effect_type,
            "description": self.description,
            "reversible": self.reversible,
            "reversed": self.reversed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SideEffect:
        return cls(
            step_name=data["step_name"],
            effect_type=data["effect_type"],
            description=data["description"],
            reversible=data["reversible"],
            reversed=data["reversed"],
        )


@dataclass(slots=True)
class CompensationAction:
    """Result of executing a compensation/rollback action."""

    step_name: str
    compensation_name: str
    success: bool
    output: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "step_name": self.step_name,
            "compensation_name": self.compensation_name,
            "success": self.success,
            "output": self.output,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompensationAction:
        return cls(
            step_name=data["step_name"],
            compensation_name=data["compensation_name"],
            success=data["success"],
            output=data["output"],
        )


@dataclass(slots=True)
class WorkflowResult:
    """Result of evaluating a workflow scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    steps_completed: int
    steps_total: int
    retries: int
    compensations_triggered: int
    compensations_successful: int
    side_effects: list[SideEffect]
    side_effects_reversed: int
    side_effects_leaked: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "steps_completed": self.steps_completed,
            "steps_total": self.steps_total,
            "retries": self.retries,
            "compensations_triggered": self.compensations_triggered,
            "compensations_successful": self.compensations_successful,
            "side_effects": [se.to_dict() for se in self.side_effects],
            "side_effects_reversed": self.side_effects_reversed,
            "side_effects_leaked": self.side_effects_leaked,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            steps_completed=data["steps_completed"],
            steps_total=data["steps_total"],
            retries=data["retries"],
            compensations_triggered=data["compensations_triggered"],
            compensations_successful=data["compensations_successful"],
            side_effects=[SideEffect.from_dict(se) for se in data["side_effects"]],
            side_effects_reversed=data["side_effects_reversed"],
            side_effects_leaked=data["side_effects_leaked"],
        )


class WorkflowInterface(SimulationInterface):
    """Contract for transactional workflow scenarios.

    Extends SimulationInterface with workflow-step management,
    compensation/rollback execution, and side-effect tracking.
    Agents are judged on completeness, compensation quality,
    and side-effect containment.
    """

    @abstractmethod
    def get_workflow_steps(self) -> list[WorkflowStep]:
        """Return the ordered workflow steps."""

    @abstractmethod
    def execute_step(
        self, state: dict[str, Any], step: WorkflowStep
    ) -> tuple[ActionResult, dict[str, Any]]:
        """Execute a single workflow step, returning result and new state."""

    @abstractmethod
    def execute_compensation(
        self, state: dict[str, Any], step: WorkflowStep
    ) -> CompensationAction:
        """Execute compensation/rollback for a failed or reversed step."""

    @abstractmethod
    def get_side_effects(self, state: dict[str, Any]) -> list[SideEffect]:
        """Return all side effects produced so far."""

    @abstractmethod
    def evaluate_workflow(self, state: dict[str, Any]) -> WorkflowResult:
        """Evaluate the complete workflow execution."""
