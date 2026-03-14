"""Investigation scenario family with evidence-chain evaluation (AC-249).

Investigation scenarios where agents gather evidence, build causal chains,
avoid red herrings, and produce a diagnosis. Evaluated on evidence quality,
chain coherence, and diagnosis accuracy rather than prose quality.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface


@dataclass(slots=True)
class EvidenceItem:
    """A single piece of evidence in an investigation."""

    id: str
    content: str
    source: str
    relevance: float  # 0.0–1.0 ground-truth relevance
    is_red_herring: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "content": self.content,
            "source": self.source,
            "relevance": self.relevance,
            "is_red_herring": self.is_red_herring,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceItem:
        return cls(
            id=data["id"],
            content=data["content"],
            source=data["source"],
            relevance=data["relevance"],
            is_red_herring=data["is_red_herring"],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class EvidenceChain:
    """An ordered chain of evidence items with reasoning."""

    items: list[EvidenceItem]
    reasoning: str

    @property
    def contains_red_herring(self) -> bool:
        return any(item.is_red_herring for item in self.items)

    def to_dict(self) -> dict[str, Any]:
        return {
            "items": [item.to_dict() for item in self.items],
            "reasoning": self.reasoning,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EvidenceChain:
        return cls(
            items=[EvidenceItem.from_dict(item) for item in data["items"]],
            reasoning=data["reasoning"],
        )


@dataclass(slots=True)
class InvestigationResult:
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
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "diagnosis": self.diagnosis,
            "evidence_collected": self.evidence_collected,
            "red_herrings_avoided": self.red_herrings_avoided,
            "red_herrings_followed": self.red_herrings_followed,
            "diagnosis_correct": self.diagnosis_correct,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InvestigationResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            diagnosis=data["diagnosis"],
            evidence_collected=data["evidence_collected"],
            red_herrings_avoided=data["red_herrings_avoided"],
            red_herrings_followed=data["red_herrings_followed"],
            diagnosis_correct=data["diagnosis_correct"],
        )


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
