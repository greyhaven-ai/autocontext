"""Negotiation scenario family with adversarial hidden-state evaluation (AC-250).

Negotiation scenarios where agents negotiate under hidden preferences,
BATNA constraints, and repeated rounds. Evaluated on deal quality,
opponent modeling accuracy, efficiency, and strategic adaptation.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from autocontext.scenarios.simulation import SimulationInterface


class HiddenPreferences(BaseModel):
    """The opponent's hidden negotiation parameters (ground truth)."""

    priorities: dict[str, float]  # dimension → weight (0.0–1.0)
    reservation_value: float  # minimum acceptable deal value
    aspiration_value: float  # ideal deal value
    batna_description: str  # best alternative to negotiated agreement
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HiddenPreferences:
        return cls.model_validate(data)


class NegotiationRound(BaseModel):
    """A single round of negotiation."""

    round_number: int
    offer: dict[str, Any]  # the agent's offer
    counter_offer: dict[str, Any] | None  # opponent counter (None if accepted/final)
    accepted: bool
    agent_reasoning: str = ""
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NegotiationRound:
        return cls.model_validate(data)


class OpponentModel(BaseModel):
    """The agent's inferred model of the opponent."""

    inferred_priorities: dict[str, float]
    inferred_reservation: float
    strategy_hypothesis: str
    confidence: float  # 0.0–1.0
    adaptation_notes: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> OpponentModel:
        return cls.model_validate(data)


class NegotiationResult(BaseModel):
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
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> NegotiationResult:
        return cls.model_validate(data)


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
