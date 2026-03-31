"""Canonical aggregate facet and run-event schema for completed runs (AC-255).

Defines the structured event model for cross-run signal extraction:
- RunEvent: categorized events within a run
- FrictionSignal: detected friction patterns
- DelightSignal: detected delight/efficiency patterns
- RunFacet: aggregate structured metadata for a completed run
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field


class RunEvent(BaseModel):
    """A categorized event within a run.

    Categories: observation, action, tool_invocation, validation,
    retry, cancellation, evidence_chain, dependency.
    """

    event_id: str
    run_id: str
    category: str
    event_type: str
    timestamp: str
    generation_index: int
    payload: dict[str, Any]
    severity: str = "info"

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunEvent:
        return cls.model_validate(data)


class FrictionSignal(BaseModel):
    """A detected friction pattern in a run.

    Signal types: validation_failure, retry_loop, backpressure,
    stale_context, tool_failure, dependency_error, rollback.
    """

    signal_type: str
    severity: str
    generation_index: int
    description: str
    evidence: list[str]
    recoverable: bool = True

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrictionSignal:
        return cls.model_validate(data)


class DelightSignal(BaseModel):
    """A detected delight/efficiency pattern in a run.

    Signal types: fast_advance, clean_recovery, efficient_tool_use,
    strong_improvement.
    """

    signal_type: str
    generation_index: int
    description: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelightSignal:
        return cls.model_validate(data)


class RunFacet(BaseModel):
    """Aggregate structured metadata for a completed run.

    Contains non-PII metadata about scenario family, provider/runtime,
    token counts, validation failures, friction/delight signals, and events.
    """

    run_id: str
    scenario: str
    scenario_family: str
    agent_provider: str
    executor_mode: str
    total_generations: int
    advances: int
    retries: int
    rollbacks: int
    best_score: float
    best_elo: float
    total_duration_seconds: float
    total_tokens: int
    total_cost_usd: float
    tool_invocations: int
    validation_failures: int
    consultation_count: int
    consultation_cost_usd: float
    friction_signals: list[FrictionSignal]
    delight_signals: list[DelightSignal]
    events: list[RunEvent]
    metadata: dict[str, Any] = Field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunFacet:
        return cls.model_validate(data)
