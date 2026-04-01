"""Investigation scenario family with evidence-chain evaluation (AC-249).

Investigation scenarios where agents gather evidence, build causal chains,
avoid red herrings, and produce a diagnosis. Evaluated on evidence quality,
chain coherence, and diagnosis accuracy rather than prose quality.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from autocontext.scenarios.simulation import SimulationInterface


class EvidenceItem(BaseModel):
    """A single piece of evidence in an investigation."""

    id: str
    content: str
    source: str
    relevance: float  # 0.0–1.0 ground-truth relevance
    is_red_herring: bool
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        return cls.model_validate(data)


class EvidenceChain(BaseModel):
    """An ordered chain of evidence items with reasoning."""

    items: list[EvidenceItem]
    reasoning: str

    @property
    def contains_red_herring(self) -> bool:
        return any(item.is_red_herring for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceChain:
        return cls.model_validate(data)


class InvestigationResult(BaseModel):
    """Result of evaluating an investigation scenario."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    diagnosis: str
    evidence_collected: int
    red_herrings_avoided: int
    red_herrings_followed: int
    diagnosis_correct: bool

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationResult:
        return cls.model_validate(data)


class InvestigationInterface(SimulationInterface):
    """Contract for investigation scenarios with evidence-chain evaluation.

    Extends SimulationInterface with evidence gathering, chain building,
    and diagnosis evaluation. Agents are judged on evidence quality,
    red herring avoidance, and diagnosis accuracy.
    """

    @abstractmethod
    def get_evidence_pool(self, state: dict[str, Any]) -> list[EvidenceItem]:
        """Return available evidence items in the current state."""

    @abstractmethod
    def evaluate_evidence_chain(
        self, chain: EvidenceChain, state: dict[str, Any]
    ) -> float:
        """Score an evidence chain (0.0–1.0) for coherence and relevance."""

    @abstractmethod
    def evaluate_diagnosis(
        self,
        diagnosis: str,
        evidence_chain: EvidenceChain,
        state: dict[str, Any],
    ) -> InvestigationResult:
        """Evaluate the final diagnosis against ground truth."""
