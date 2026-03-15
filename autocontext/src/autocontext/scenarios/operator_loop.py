"""Operator-in-the-loop scenario family (AC-251).

Scenarios where agents must decide when to act autonomously vs when to
escalate, request clarification, or consult an operator. Evaluated on
judgment quality: correct deferrals, unnecessary escalations, and missed
escalations are scored separately.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface


@dataclass(slots=True)
class ClarificationRequest:
    """A clarification request from the agent to the operator."""

    question: str
    context: str
    urgency: str  # "low", "medium", "high"
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "question": self.question,
            "context": self.context,
            "urgency": self.urgency,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ClarificationRequest:
        return cls(
            question=data["question"],
            context=data["context"],
            urgency=data["urgency"],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class EscalationEvent:
    """A record of an escalation to the operator."""

    step: int
    reason: str
    severity: str  # "low", "medium", "high", "critical"
    context: str
    was_necessary: bool  # ground truth for evaluation
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "step": self.step,
            "reason": self.reason,
            "severity": self.severity,
            "context": self.context,
            "was_necessary": self.was_necessary,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EscalationEvent:
        return cls(
            step=data["step"],
            reason=data["reason"],
            severity=data["severity"],
            context=data["context"],
            was_necessary=data["was_necessary"],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class OperatorLoopResult:
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
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "total_actions": self.total_actions,
            "escalations": self.escalations,
            "necessary_escalations": self.necessary_escalations,
            "unnecessary_escalations": self.unnecessary_escalations,
            "missed_escalations": self.missed_escalations,
            "clarifications_requested": self.clarifications_requested,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OperatorLoopResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            total_actions=data["total_actions"],
            escalations=data["escalations"],
            necessary_escalations=data["necessary_escalations"],
            unnecessary_escalations=data["unnecessary_escalations"],
            missed_escalations=data["missed_escalations"],
            clarifications_requested=data["clarifications_requested"],
        )


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
