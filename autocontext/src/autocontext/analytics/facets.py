"""Canonical aggregate facet and run-event schema for completed runs (AC-255).

Defines the structured event model for cross-run signal extraction:
- RunEvent: categorized events within a run
- FrictionSignal: detected friction patterns
- DelightSignal: detected delight/efficiency patterns
- RunFacet: aggregate structured metadata for a completed run
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(slots=True)
class RunEvent:
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
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "category": self.category,
            "event_type": self.event_type,
            "timestamp": self.timestamp,
            "generation_index": self.generation_index,
            "payload": self.payload,
            "severity": self.severity,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunEvent:
        return cls(
            event_id=data["event_id"],
            run_id=data["run_id"],
            category=data["category"],
            event_type=data["event_type"],
            timestamp=data["timestamp"],
            generation_index=data["generation_index"],
            payload=data.get("payload", {}),
            severity=data.get("severity", "info"),
        )


@dataclass(slots=True)
class FrictionSignal:
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
        return {
            "signal_type": self.signal_type,
            "severity": self.severity,
            "generation_index": self.generation_index,
            "description": self.description,
            "evidence": self.evidence,
            "recoverable": self.recoverable,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FrictionSignal:
        return cls(
            signal_type=data["signal_type"],
            severity=data["severity"],
            generation_index=data["generation_index"],
            description=data["description"],
            evidence=data.get("evidence", []),
            recoverable=data.get("recoverable", True),
        )


@dataclass(slots=True)
class DelightSignal:
    """A detected delight/efficiency pattern in a run.

    Signal types: fast_advance, clean_recovery, efficient_tool_use,
    strong_improvement.
    """

    signal_type: str
    generation_index: int
    description: str
    evidence: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {
            "signal_type": self.signal_type,
            "generation_index": self.generation_index,
            "description": self.description,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> DelightSignal:
        return cls(
            signal_type=data["signal_type"],
            generation_index=data["generation_index"],
            description=data["description"],
            evidence=data.get("evidence", []),
        )


@dataclass(slots=True)
class RunFacet:
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
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id,
            "scenario": self.scenario,
            "scenario_family": self.scenario_family,
            "agent_provider": self.agent_provider,
            "executor_mode": self.executor_mode,
            "total_generations": self.total_generations,
            "advances": self.advances,
            "retries": self.retries,
            "rollbacks": self.rollbacks,
            "best_score": self.best_score,
            "best_elo": self.best_elo,
            "total_duration_seconds": self.total_duration_seconds,
            "total_tokens": self.total_tokens,
            "total_cost_usd": self.total_cost_usd,
            "tool_invocations": self.tool_invocations,
            "validation_failures": self.validation_failures,
            "consultation_count": self.consultation_count,
            "consultation_cost_usd": self.consultation_cost_usd,
            "friction_signals": [s.to_dict() for s in self.friction_signals],
            "delight_signals": [s.to_dict() for s in self.delight_signals],
            "events": [e.to_dict() for e in self.events],
            "metadata": self.metadata,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunFacet:
        return cls(
            run_id=data["run_id"],
            scenario=data["scenario"],
            scenario_family=data["scenario_family"],
            agent_provider=data["agent_provider"],
            executor_mode=data["executor_mode"],
            total_generations=data["total_generations"],
            advances=data["advances"],
            retries=data["retries"],
            rollbacks=data["rollbacks"],
            best_score=data["best_score"],
            best_elo=data["best_elo"],
            total_duration_seconds=data["total_duration_seconds"],
            total_tokens=data["total_tokens"],
            total_cost_usd=data["total_cost_usd"],
            tool_invocations=data["tool_invocations"],
            validation_failures=data["validation_failures"],
            consultation_count=data["consultation_count"],
            consultation_cost_usd=data["consultation_cost_usd"],
            friction_signals=[
                FrictionSignal.from_dict(s) for s in data.get("friction_signals", [])
            ],
            delight_signals=[
                DelightSignal.from_dict(s) for s in data.get("delight_signals", [])
            ],
            events=[RunEvent.from_dict(e) for e in data.get("events", [])],
            metadata=data.get("metadata", {}),
            created_at=data.get("created_at", ""),
        )
