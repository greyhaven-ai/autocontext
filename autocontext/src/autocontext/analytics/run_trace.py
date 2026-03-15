"""Canonical run-state event model and causal trace artifact (AC-262).

Provides a rich, versioned event schema for representing what actually happened
inside a run at the granularity needed for cross-run learning, clustering,
audit, and operator inspection.

Key types:
- ActorRef: who/what generated an event (role, tool, system, external)
- ResourceRef: what artifact/entity was involved
- TraceEvent: a single timestamped event with causality and evidence links
- CausalEdge: explicit dependency/causality between events
- RunTrace: per-run or per-generation trace artifact containing ordered events
- TraceStore: JSON-file persistence for traces
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass(slots=True)
class ActorRef:
    """Who or what generated an event.

    Actor types: role, tool, system, external.
    """

    actor_type: str
    actor_id: str
    actor_name: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "actor_type": self.actor_type,
            "actor_id": self.actor_id,
            "actor_name": self.actor_name,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActorRef:
        return cls(
            actor_type=data["actor_type"],
            actor_id=data["actor_id"],
            actor_name=data.get("actor_name", ""),
        )


@dataclass(slots=True)
class ResourceRef:
    """An artifact, entity, or service involved in an event.

    Resource types: artifact, scenario_entity, service, model, knowledge.
    """

    resource_type: str
    resource_id: str
    resource_name: str
    resource_path: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "resource_type": self.resource_type,
            "resource_id": self.resource_id,
            "resource_name": self.resource_name,
            "resource_path": self.resource_path,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceRef:
        return cls(
            resource_type=data["resource_type"],
            resource_id=data["resource_id"],
            resource_name=data.get("resource_name", ""),
            resource_path=data.get("resource_path", ""),
        )


@dataclass(slots=True)
class TraceEvent:
    """A single timestamped event in a run trace.

    Categories: observation, hypothesis, action, tool_invocation,
    validation, retry, cancellation, failure, recovery, checkpoint,
    evidence_link.

    Stages: init, compete, analyze, coach, architect, curate, match, gate.

    Severity: info, warning, error, critical.
    """

    event_id: str
    run_id: str
    generation_index: int
    sequence_number: int
    timestamp: str
    category: str
    event_type: str
    actor: ActorRef
    resources: list[ResourceRef]
    summary: str
    detail: dict[str, Any]
    parent_event_id: str | None
    cause_event_ids: list[str]
    evidence_ids: list[str]
    severity: str
    stage: str
    outcome: str | None
    duration_ms: int | None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_id": self.event_id,
            "run_id": self.run_id,
            "generation_index": self.generation_index,
            "sequence_number": self.sequence_number,
            "timestamp": self.timestamp,
            "category": self.category,
            "event_type": self.event_type,
            "actor": self.actor.to_dict(),
            "resources": [r.to_dict() for r in self.resources],
            "summary": self.summary,
            "detail": self.detail,
            "parent_event_id": self.parent_event_id,
            "cause_event_ids": self.cause_event_ids,
            "evidence_ids": self.evidence_ids,
            "severity": self.severity,
            "stage": self.stage,
            "outcome": self.outcome,
            "duration_ms": self.duration_ms,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceEvent:
        return cls(
            event_id=data["event_id"],
            run_id=data["run_id"],
            generation_index=data.get("generation_index", 0),
            sequence_number=data.get("sequence_number", 0),
            timestamp=data.get("timestamp", ""),
            category=data["category"],
            event_type=data.get("event_type", ""),
            actor=ActorRef.from_dict(data["actor"]),
            resources=[ResourceRef.from_dict(r) for r in data.get("resources", [])],
            summary=data.get("summary", ""),
            detail=data.get("detail", {}),
            parent_event_id=data.get("parent_event_id"),
            cause_event_ids=data.get("cause_event_ids", []),
            evidence_ids=data.get("evidence_ids", []),
            severity=data.get("severity", "info"),
            stage=data.get("stage", ""),
            outcome=data.get("outcome"),
            duration_ms=data.get("duration_ms"),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class CausalEdge:
    """An explicit dependency or causality link between two events.

    Relations: causes, depends_on, triggers, supersedes, retries, recovers.
    """

    source_event_id: str
    target_event_id: str
    relation: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_event_id": self.source_event_id,
            "target_event_id": self.target_event_id,
            "relation": self.relation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CausalEdge:
        return cls(
            source_event_id=data["source_event_id"],
            target_event_id=data["target_event_id"],
            relation=data["relation"],
        )


@dataclass(slots=True)
class RunTrace:
    """Per-run or per-generation trace artifact.

    Contains ordered events and explicit causal edges.
    Schema is versioned for safe downstream evolution.
    """

    trace_id: str
    run_id: str
    generation_index: int | None
    schema_version: str
    events: list[TraceEvent]
    causal_edges: list[CausalEdge]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "generation_index": self.generation_index,
            "schema_version": self.schema_version,
            "events": [e.to_dict() for e in self.events],
            "causal_edges": [e.to_dict() for e in self.causal_edges],
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunTrace:
        return cls(
            trace_id=data["trace_id"],
            run_id=data["run_id"],
            generation_index=data.get("generation_index"),
            schema_version=data.get("schema_version", "1.0.0"),
            events=[TraceEvent.from_dict(e) for e in data.get("events", [])],
            causal_edges=[CausalEdge.from_dict(e) for e in data.get("causal_edges", [])],
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )


class TraceStore:
    """Persists and queries RunTrace artifacts as JSON files."""

    def __init__(self, root: Path) -> None:
        self._dir = root / "traces"
        self._dir.mkdir(parents=True, exist_ok=True)

    def persist(self, trace: RunTrace) -> Path:
        path = self._dir / f"{trace.trace_id}.json"
        path.write_text(json.dumps(trace.to_dict(), indent=2), encoding="utf-8")
        return path

    def load(self, trace_id: str) -> RunTrace | None:
        path = self._dir / f"{trace_id}.json"
        if not path.exists():
            return None
        data = json.loads(path.read_text(encoding="utf-8"))
        return RunTrace.from_dict(data)

    def list_traces(
        self,
        run_id: str | None = None,
        generation_index: int | None = None,
    ) -> list[RunTrace]:
        results: list[RunTrace] = []
        for path in sorted(self._dir.glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            trace = RunTrace.from_dict(data)
            if run_id is not None and trace.run_id != run_id:
                continue
            if generation_index is not None and trace.generation_index != generation_index:
                continue
            results.append(trace)
        return results
