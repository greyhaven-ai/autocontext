"""Negotiation scenario family with adversarial hidden-state evaluation (AC-250).

Negotiation scenarios where agents negotiate under hidden preferences,
BATNA constraints, and repeated rounds. Evaluated on deal quality,
opponent modeling accuracy, efficiency, and strategic adaptation.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface


@dataclass(slots=True)
class HiddenPreferences:
    """The opponent's hidden negotiation parameters (ground truth)."""

    priorities: dict[str, float]  # dimension → weight (0.0–1.0)
    reservation_value: float  # minimum acceptable deal value
    aspiration_value: float  # ideal deal value
    batna_description: str  # best alternative to negotiated agreement
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "priorities": self.priorities,
            "reservation_value": self.reservation_value,
            "aspiration_value": self.aspiration_value,
            "batna_description": self.batna_description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HiddenPreferences:
        return cls(
            priorities=data["priorities"],
            reservation_value=data["reservation_value"],
            aspiration_value=data["aspiration_value"],
            batna_description=data["batna_description"],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class NegotiationRound:
    """A single round of negotiation."""

    round_number: int
    offer: dict[str, Any]  # the agent's offer
    counter_offer: dict[str, Any] | None  # opponent counter (None if accepted/final)
    accepted: bool
    agent_reasoning: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "round_number": self.round_number,
            "offer": self.offer,
            "counter_offer": self.counter_offer,
            "accepted": self.accepted,
            "agent_reasoning": self.agent_reasoning,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NegotiationRound:
        return cls(
            round_number=data["round_number"],
            offer=data["offer"],
            counter_offer=data.get("counter_offer"),
            accepted=data["accepted"],
            agent_reasoning=data.get("agent_reasoning", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class OpponentModel:
    """The agent's inferred model of the opponent."""

    inferred_priorities: dict[str, float]
    inferred_reservation: float
    strategy_hypothesis: str
    confidence: float  # 0.0–1.0
    adaptation_notes: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "inferred_priorities": self.inferred_priorities,
            "inferred_reservation": self.inferred_reservation,
            "strategy_hypothesis": self.strategy_hypothesis,
            "confidence": self.confidence,
            "adaptation_notes": self.adaptation_notes,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpponentModel:
        return cls(
            inferred_priorities=data["inferred_priorities"],
            inferred_reservation=data["inferred_reservation"],
            strategy_hypothesis=data["strategy_hypothesis"],
            confidence=data["confidence"],
            adaptation_notes=data.get("adaptation_notes", []),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class NegotiationResult:
    """Evaluation result for a negotiation scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]  # deal_quality, opponent_modeling, efficiency, adaptation
    deal_value: float
    rounds_used: int
    max_rounds: int
    opponent_model_accuracy: float  # how close the inferred model was to ground truth
    value_claimed_ratio: float  # fraction of available surplus claimed

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "deal_value": self.deal_value,
            "rounds_used": self.rounds_used,
            "max_rounds": self.max_rounds,
            "opponent_model_accuracy": self.opponent_model_accuracy,
            "value_claimed_ratio": self.value_claimed_ratio,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NegotiationResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            deal_value=data["deal_value"],
            rounds_used=data["rounds_used"],
            max_rounds=data["max_rounds"],
            opponent_model_accuracy=data["opponent_model_accuracy"],
            value_claimed_ratio=data["value_claimed_ratio"],
        )


class NegotiationInterface(SimulationInterface):
    """ABC for negotiation scenarios with hidden preferences and repeated rounds.

    Extends SimulationInterface with negotiation-specific methods for
    opponent modeling, round tracking, and deal quality evaluation.
    """

    @abstractmethod
    def get_hidden_preferences(self, state: dict[str, Any]) -> HiddenPreferences:
        """Return the opponent's hidden preferences (ground truth for evaluation)."""

    @abstractmethod
    def get_rounds(self, state: dict[str, Any]) -> list[NegotiationRound]:
        """Return the negotiation rounds completed so far."""

    @abstractmethod
    def get_opponent_model(self, state: dict[str, Any]) -> OpponentModel | None:
        """Return the agent's current inferred opponent model, if any."""

    @abstractmethod
    def update_opponent_model(
        self, state: dict[str, Any], model: OpponentModel
    ) -> dict[str, Any]:
        """Update the opponent model in state. Returns new state."""

    @abstractmethod
    def evaluate_negotiation(self, state: dict[str, Any]) -> NegotiationResult:
        """Evaluate the negotiation outcome with dimension scores."""
