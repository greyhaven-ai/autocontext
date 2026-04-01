"""Operator-in-the-loop scenario family (AC-251).

Scenarios where agents must decide when to act autonomously vs when to
escalate, request clarification, or consult an operator. Evaluated on
judgment quality: correct deferrals, unnecessary escalations, and missed
escalations are scored separately.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from autocontext.scenarios.simulation import SimulationInterface


class ClarificationRequest(BaseModel):
    """A clarification request from the agent to the operator."""

    question: str
    context: str
    urgency: str  # "low", "medium", "high"
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClarificationRequest:
        return cls.model_validate(data)


class EscalationEvent(BaseModel):
    """A record of an escalation to the operator."""

    step: int
    reason: str
    severity: str  # "low", "medium", "high", "critical"
    context: str
    was_necessary: bool  # ground truth for evaluation
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationEvent:
        return cls.model_validate(data)


class OperatorLoopResult(BaseModel):
    """Evaluation result for operator-in-the-loop judgment."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    total_actions: int
    escalations: int
    necessary_escalations: int
    unnecessary_escalations: int
    missed_escalations: int
    clarifications_requested: int

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperatorLoopResult:
        return cls.model_validate(data)


class OperatorLoopInterface(SimulationInterface):
    """ABC for operator-in-the-loop scenarios.

    Extends SimulationInterface with escalation, clarification, and
    judgment evaluation methods.
    """

    @abstractmethod
    def get_escalation_log(self, state: dict[str, Any]) -> list[EscalationEvent]:
        """Return all escalation events so far."""

    @abstractmethod
    def get_clarification_log(self, state: dict[str, Any]) -> list[ClarificationRequest]:
        """Return all clarification requests so far."""

    @abstractmethod
    def escalate(self, state: dict[str, Any], event: EscalationEvent) -> dict[str, Any]:
        """Record an escalation event. Returns new state."""

    @abstractmethod
    def request_clarification(
        self, state: dict[str, Any], request: ClarificationRequest
    ) -> dict[str, Any]:
        """Record a clarification request. Returns new state."""

    @abstractmethod
    def evaluate_judgment(self, state: dict[str, Any]) -> OperatorLoopResult:
        """Evaluate the agent's escalation/clarification judgment."""
