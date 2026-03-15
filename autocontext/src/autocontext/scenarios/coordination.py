"""Multi-agent coordination scenario family (AC-253).

Scenarios where multiple worker agents coordinate under partial context,
hand off information, and merge outputs. Evaluated on duplication avoidance,
handoff quality, merge quality, and final outcome quality.
"""

from __future__ import annotations

from abc import abstractmethod
from dataclasses import dataclass, field
from typing import Any

from autocontext.scenarios.simulation import SimulationInterface


@dataclass(slots=True)
class WorkerContext:
    """Partial context assigned to a worker agent."""

    worker_id: str
    role: str
    context_partition: dict[str, Any]  # what this worker can see
    visible_data: list[str]  # keys/sections visible to this worker
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "worker_id": self.worker_id,
            "role": self.role,
            "context_partition": self.context_partition,
            "visible_data": self.visible_data,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerContext:
        return cls(
            worker_id=data["worker_id"],
            role=data["role"],
            context_partition=data.get("context_partition", {}),
            visible_data=data.get("visible_data", []),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class HandoffRecord:
    """A record of information passed between workers."""

    from_worker: str
    to_worker: str
    content: str
    quality: float  # 0.0–1.0
    step: int
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "from_worker": self.from_worker,
            "to_worker": self.to_worker,
            "content": self.content,
            "quality": self.quality,
            "step": self.step,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffRecord:
        return cls(
            from_worker=data["from_worker"],
            to_worker=data["to_worker"],
            content=data["content"],
            quality=data["quality"],
            step=data["step"],
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CoordinationResult:
    """Evaluation result for multi-agent coordination."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    workers_used: int
    handoffs_completed: int
    duplication_rate: float  # 0.0–1.0 (lower is better)
    merge_conflicts: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "reasoning": self.reasoning,
            "dimension_scores": self.dimension_scores,
            "workers_used": self.workers_used,
            "handoffs_completed": self.handoffs_completed,
            "duplication_rate": self.duplication_rate,
            "merge_conflicts": self.merge_conflicts,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinationResult:
        return cls(
            score=data["score"],
            reasoning=data["reasoning"],
            dimension_scores=data["dimension_scores"],
            workers_used=data["workers_used"],
            handoffs_completed=data["handoffs_completed"],
            duplication_rate=data["duplication_rate"],
            merge_conflicts=data["merge_conflicts"],
        )


class CoordinationInterface(SimulationInterface):
    """ABC for multi-agent coordination scenarios.

    Extends SimulationInterface with worker context management,
    handoff tracking, output merging, and coordination evaluation.
    """

    @abstractmethod
    def get_worker_contexts(self, state: dict[str, Any]) -> list[WorkerContext]:
        """Return the partial contexts for all workers."""

    @abstractmethod
    def get_handoff_log(self, state: dict[str, Any]) -> list[HandoffRecord]:
        """Return all handoff records so far."""

    @abstractmethod
    def record_handoff(
        self, state: dict[str, Any], handoff: HandoffRecord
    ) -> dict[str, Any]:
        """Record an information handoff between workers. Returns new state."""

    @abstractmethod
    def merge_outputs(
        self, state: dict[str, Any], worker_outputs: dict[str, str]
    ) -> dict[str, Any]:
        """Merge outputs from multiple workers. Returns new state."""

    @abstractmethod
    def evaluate_coordination(self, state: dict[str, Any]) -> CoordinationResult:
        """Evaluate coordination quality across all dimensions."""
