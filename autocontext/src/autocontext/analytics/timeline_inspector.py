"""Timeline and state inspector for runs and generations (AC-263).

Provides an operator-facing inspection surface for understanding what
happened during a run or generation, with causal structure for debugging
failures and inspecting learning.

Key types:
- TimelineFilter: criteria for filtering timeline entries
- TimelineEntry: a display-ready entry in the timeline
- TimelineBuilder: builds timeline from RunTrace, supports filtering and summary
- RunInspection / GenerationInspection: structured inspection results
- StateInspector: main inspection API with failure/recovery path analysis
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from autocontext.analytics.run_trace import RunTrace, TraceEvent

# Severity ordering for filtering
_SEVERITY_ORDER = {"info": 0, "warning": 1, "error": 2, "critical": 3}

# Categories that warrant highlighting
_HIGHLIGHT_CATEGORIES = {"failure", "recovery", "cancellation"}


@dataclass(slots=True)
class TimelineFilter:
    """Criteria for filtering timeline entries."""

    roles: list[str] | None = None
    stages: list[str] | None = None
    categories: list[str] | None = None
    event_types: list[str] | None = None
    min_severity: str | None = None
    generation_index: int | None = None


@dataclass(slots=True)
class TimelineEntry:
    """A display-ready entry in the timeline."""

    entry_id: str
    event: TraceEvent
    depth: int
    children_count: int
    artifact_links: list[str]
    highlight: bool
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "entry_id": self.entry_id,
            "event": self.event.to_dict(),
            "depth": self.depth,
            "children_count": self.children_count,
            "artifact_links": self.artifact_links,
            "highlight": self.highlight,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TimelineEntry:
        return cls(
            entry_id=data["entry_id"],
            event=TraceEvent.from_dict(data["event"]),
            depth=data.get("depth", 0),
            children_count=data.get("children_count", 0),
            artifact_links=data.get("artifact_links", []),
            highlight=data.get("highlight", False),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class RunInspection:
    """Structured inspection result for a run."""

    summary: str
    total_events: int
    events_by_category: dict[str, int]
    events_by_stage: dict[str, int]
    failure_count: int
    recovery_count: int
    retry_count: int
    causal_depth: int


@dataclass(slots=True)
class GenerationInspection:
    """Structured inspection result for a single generation."""

    generation_index: int
    summary: str
    total_events: int
    events_by_category: dict[str, int]
    events_by_stage: dict[str, int]
    failure_count: int
    recovery_count: int


def _matches_filter(event: TraceEvent, filt: TimelineFilter) -> bool:
    """Check if an event passes the filter criteria."""
    if filt.roles is not None and event.actor.actor_id not in filt.roles:
        return False
    if filt.stages is not None and event.stage not in filt.stages:
        return False
    if filt.categories is not None and event.category not in filt.categories:
        return False
    if filt.event_types is not None and event.event_type not in filt.event_types:
        return False
    if filt.min_severity is not None:
        threshold = _SEVERITY_ORDER.get(filt.min_severity, 0)
        actual = _SEVERITY_ORDER.get(event.severity, 0)
        if actual < threshold:
            return False
    if filt.generation_index is not None and event.generation_index != filt.generation_index:
        return False
    return True


def _make_entry(event: TraceEvent, idx: int) -> TimelineEntry:
    """Create a TimelineEntry from a TraceEvent."""
    artifact_links = [r.resource_path for r in event.resources if r.resource_path]
    highlight = event.category in _HIGHLIGHT_CATEGORIES
    return TimelineEntry(
        entry_id=f"tl-{idx}",
        event=event,
        depth=0,
        children_count=0,
        artifact_links=artifact_links,
        highlight=highlight,
    )


class TimelineBuilder:
    """Builds timeline views from RunTrace data."""

    def build(
        self,
        trace: RunTrace,
        filt: TimelineFilter | None = None,
    ) -> list[TimelineEntry]:
        if not trace.events:
            return []

        events = sorted(trace.events, key=lambda e: e.sequence_number)
        if filt is not None:
            events = [e for e in events if _matches_filter(e, filt)]

        return [_make_entry(e, i) for i, e in enumerate(events)]

    def build_summary(self, trace: RunTrace) -> list[TimelineEntry]:
        """Collapsed summary — one representative entry per stage."""
        if not trace.events:
            return []

        events = sorted(trace.events, key=lambda e: e.sequence_number)

        entries: list[TimelineEntry] = []
        current_stage: str | None = None
        stage_count = 0

        for event in events:
            if event.stage != current_stage:
                # Emit representative for new stage
                entry = _make_entry(event, len(entries))
                entry.children_count = 0
                entries.append(entry)
                current_stage = event.stage
                stage_count = 1
            else:
                stage_count += 1
                entries[-1].children_count = stage_count - 1

        return entries

    def compare_generations(
        self,
        traces: list[RunTrace],
    ) -> list[TimelineEntry]:
        """Interleave events from multiple generation traces for comparison."""
        all_events: list[TraceEvent] = []
        for trace in traces:
            all_events.extend(trace.events)

        all_events.sort(key=lambda e: (e.generation_index, e.sequence_number))
        return [_make_entry(e, i) for i, e in enumerate(all_events)]


class StateInspector:
    """Main inspection API for run and generation state."""

    def inspect_run(self, trace: RunTrace) -> RunInspection:
        events = trace.events
        cat_counts: Counter[str] = Counter(e.category for e in events)
        stage_counts: Counter[str] = Counter(e.stage for e in events)

        failure_count = cat_counts.get("failure", 0)
        recovery_count = cat_counts.get("recovery", 0)
        retry_count = cat_counts.get("retry", 0)

        causal_depth = self._compute_causal_depth(trace)

        return RunInspection(
            summary=self._run_summary(len(events), failure_count, recovery_count, retry_count),
            total_events=len(events),
            events_by_category=dict(cat_counts),
            events_by_stage=dict(stage_counts),
            failure_count=failure_count,
            recovery_count=recovery_count,
            retry_count=retry_count,
            causal_depth=causal_depth,
        )

    def inspect_generation(
        self,
        trace: RunTrace,
        generation_index: int,
    ) -> GenerationInspection:
        events = [e for e in trace.events if e.generation_index == generation_index]
        cat_counts: Counter[str] = Counter(e.category for e in events)
        stage_counts: Counter[str] = Counter(e.stage for e in events)

        return GenerationInspection(
            generation_index=generation_index,
            summary=f"Generation {generation_index}: {len(events)} events",
            total_events=len(events),
            events_by_category=dict(cat_counts),
            events_by_stage=dict(stage_counts),
            failure_count=cat_counts.get("failure", 0),
            recovery_count=cat_counts.get("recovery", 0),
        )

    def find_failure_paths(self, trace: RunTrace) -> list[list[TraceEvent]]:
        """Find causal chains leading to each failure event."""
        failures = [e for e in trace.events if e.category == "failure"]
        return [self._trace_causes(trace, f.event_id) for f in failures]

    def find_recovery_paths(self, trace: RunTrace) -> list[list[TraceEvent]]:
        """Find causal chains leading to each recovery event."""
        recoveries = [e for e in trace.events if e.category == "recovery"]
        return [self._trace_causes(trace, r.event_id) for r in recoveries]

    def dependency_chain(
        self,
        trace: RunTrace,
        event_id: str,
    ) -> list[TraceEvent]:
        """Trace backward through cause_event_ids from a given event."""
        return self._trace_causes(trace, event_id)

    def _trace_causes(
        self,
        trace: RunTrace,
        event_id: str,
    ) -> list[TraceEvent]:
        """BFS backward through causal edges, falling back to inline cause ids."""
        event_map = {e.event_id: e for e in trace.events}
        if event_id not in event_map:
            return []

        parent_map = self._parent_map(trace)
        visited: set[str] = set()
        queue = [event_id]
        chain: list[TraceEvent] = []

        while queue:
            eid = queue.pop(0)
            if eid in visited:
                continue
            visited.add(eid)
            evt = event_map.get(eid)
            if evt is None:
                continue
            chain.append(evt)
            for cause_id in parent_map.get(eid, []):
                if cause_id not in visited:
                    queue.append(cause_id)

        # Return in sequence order, target event last
        chain.sort(key=lambda e: e.sequence_number)
        return chain

    def _compute_causal_depth(self, trace: RunTrace) -> int:
        """Compute the maximum causal chain length in the trace."""
        if not trace.events:
            return 0

        event_map = {e.event_id: e for e in trace.events}
        parent_map = self._parent_map(trace)
        memo: dict[str, int] = {}

        def depth(eid: str) -> int:
            if eid in memo:
                return memo[eid]
            evt = event_map.get(eid)
            parent_ids = [parent_id for parent_id in parent_map.get(eid, []) if parent_id in event_map]
            if evt is None or not parent_ids:
                memo[eid] = 1
                return 1
            d = 1 + max(depth(parent_id) for parent_id in parent_ids)
            memo[eid] = d
            return d

        return max(depth(e.event_id) for e in trace.events)

    def _parent_map(self, trace: RunTrace) -> dict[str, list[str]]:
        """Build a canonical parent map from explicit edges plus inline fallbacks."""
        event_ids = {event.event_id for event in trace.events}
        parent_map: dict[str, list[str]] = {event_id: [] for event_id in event_ids}

        for edge in trace.causal_edges:
            if edge.source_event_id not in event_ids or edge.target_event_id not in event_ids:
                continue
            parents = parent_map.setdefault(edge.target_event_id, [])
            if edge.source_event_id not in parents:
                parents.append(edge.source_event_id)

        for event in trace.events:
            parents = parent_map.setdefault(event.event_id, [])
            if event.parent_event_id and event.parent_event_id in event_ids and event.parent_event_id not in parents:
                parents.append(event.parent_event_id)
            for cause_id in event.cause_event_ids:
                if cause_id in event_ids and cause_id not in parents:
                    parents.append(cause_id)

        return parent_map

    def _run_summary(
        self,
        total: int,
        failures: int,
        recoveries: int,
        retries: int,
    ) -> str:
        parts = [f"{total} events"]
        if failures:
            parts.append(f"{failures} failure(s)")
        if recoveries:
            parts.append(f"{recoveries} recovery(ies)")
        if retries:
            parts.append(f"{retries} retry(ies)")
        return "Run: " + ", ".join(parts)
