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

from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

from autocontext.util.json_io import read_json, write_json


class ActorRef(BaseModel):
    """Who or what generated an event.

    Actor types: role, tool, system, external.
    """

    actor_type: str
    actor_id: str
    actor_name: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ActorRef:
        return cls.model_validate(data)


class ResourceRef(BaseModel):
    """An artifact, entity, or service involved in an event.

    Resource types: artifact, scenario_entity, service, model, knowledge.
    """

    resource_type: str
    resource_id: str
    resource_name: str
    resource_path: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ResourceRef:
        return cls.model_validate(data)


class TraceEvent(BaseModel):
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
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceEvent:
        return cls.model_validate(data)


class CausalEdge(BaseModel):
    """An explicit dependency or causality link between two events.

    Relations: causes, depends_on, triggers, supersedes, retries, recovers.
    """

    source_event_id: str
    target_event_id: str
    relation: str

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CausalEdge:
        return cls.model_validate(data)


class RunTrace(BaseModel):
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
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RunTrace:
        return cls.model_validate(data)


class TraceStore:
    """Persists and queries RunTrace artifacts as JSON files."""

    def __init__(self, root: Path, writer: Callable[[Path, dict[str, Any]], None] | None = None) -> None:
        self._dir = root / "traces"
        self._writer = writer
        self._dir.mkdir(parents=True, exist_ok=True)

    def persist(self, trace: RunTrace) -> Path:
        path = self._dir / f"{trace.trace_id}.json"
        payload = trace.to_dict()
        if self._writer is not None:
            self._writer(path, payload)
        else:
            write_json(path, payload)
        return path

    def load(self, trace_id: str) -> RunTrace | None:
        path = self._dir / f"{trace_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return RunTrace.from_dict(data)

    def list_traces(
        self,
        run_id: str | None = None,
        generation_index: int | None = None,
    ) -> list[RunTrace]:
        results: list[RunTrace] = []
        for path in sorted(self._dir.glob("*.json")):
            data = read_json(path)
            trace = RunTrace.from_dict(data)
            if run_id is not None and trace.run_id != run_id:
                continue
            if generation_index is not None and trace.generation_index != generation_index:
                continue
            results.append(trace)
        return results
