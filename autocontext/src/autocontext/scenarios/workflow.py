"""Workflow scenario family with transactional evaluation (AC-249).

Workflow scenarios where agents execute multi-step transactional workflows
with retries, compensation/rollback, and side-effect tracking. Evaluated
on workflow completeness, compensation quality, and side-effect containment.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel

from autocontext.scenarios.simulation import ActionResult, SimulationInterface


class WorkflowStep(BaseModel):
    """A single step in a transactional workflow."""

    name: str
    description: str
    idempotent: bool
    reversible: bool
    compensation: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowStep:
        return cls.model_validate(data)


class SideEffect(BaseModel):
    """A side effect produced by a workflow step."""

    step_name: str
    effect_type: str  # e.g., "payment", "notification", "external_api"
    description: str
    reversible: bool
    reversed: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SideEffect:
        return cls.model_validate(data)


class CompensationAction(BaseModel):
    """Result of executing a compensation/rollback action."""

    step_name: str
    compensation_name: str
    success: bool
    output: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CompensationAction:
        return cls.model_validate(data)


class WorkflowResult(BaseModel):
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
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkflowResult:
        return cls.model_validate(data)


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
