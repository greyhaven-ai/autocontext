"""Multi-agent coordination scenario family (AC-253).

Scenarios where multiple worker agents coordinate under partial context,
hand off information, and merge outputs. Evaluated on duplication avoidance,
handoff quality, merge quality, and final outcome quality.
"""

from __future__ import annotations

from abc import abstractmethod
from typing import Any

from pydantic import BaseModel, Field

from autocontext.scenarios.simulation import SimulationInterface


class WorkerContext(BaseModel):
    """Partial context assigned to a worker agent."""

    worker_id: str
    role: str
    context_partition: dict[str, Any]  # what this worker can see
    visible_data: list[str]  # keys/sections visible to this worker
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WorkerContext:
        return cls.model_validate(data)


class HandoffRecord(BaseModel):
    """A record of information passed between workers."""

    from_worker: str
    to_worker: str
    content: str
    quality: float  # 0.0–1.0
    step: int
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> HandoffRecord:
        return cls.model_validate(data)


class CoordinationResult(BaseModel):
    """Evaluation result for multi-agent coordination."""

    score: float
    reasoning: str
    dimension_scores: dict[str, float]
    workers_used: int
    handoffs_completed: int
    duplication_rate: float  # 0.0–1.0 (lower is better)
    merge_conflicts: int

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CoordinationResult:
        return cls.model_validate(data)


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
