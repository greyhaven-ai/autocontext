"""Tests for AC-263: timeline and state inspector for runs and generations.

Covers: TimelineFilter, TimelineEntry, TimelineBuilder, RunInspection,
GenerationInspection, StateInspector.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers — build rich traces for testing
# ---------------------------------------------------------------------------


def _actor(actor_type: str = "role", actor_id: str = "competitor") -> Any:
    from autocontext.analytics.run_trace import ActorRef

    return ActorRef(actor_type=actor_type, actor_id=actor_id, actor_name=actor_id.title())


def _resource(resource_id: str = "playbook-v3") -> Any:
    from autocontext.analytics.run_trace import ResourceRef

    return ResourceRef(
        resource_type="artifact", resource_id=resource_id,
        resource_name=resource_id, resource_path=f"knowledge/{resource_id}",
    )


def _evt(
    event_id: str,
    category: str,
    stage: str,
    seq: int,
    *,
    actor_id: str = "competitor",
    outcome: str | None = "success",
    cause_ids: list[str] | None = None,
    evidence_ids: list[str] | None = None,
    gen: int = 0,
    severity: str = "info",
    event_type: str = "",
    duration_ms: int | None = 100,
) -> Any:
    from autocontext.analytics.run_trace import TraceEvent

    return TraceEvent(
        event_id=event_id,
        run_id="run-1",
        generation_index=gen,
        sequence_number=seq,
        timestamp=f"2026-03-14T12:{seq:02d}:00Z",
        category=category,
        event_type=event_type or f"{category}_default",
        actor=_actor(actor_id=actor_id),
        resources=[_resource()],
        summary=f"{category} event at seq {seq}",
        detail={},
        parent_event_id=None,
        cause_event_ids=cause_ids or [],
        evidence_ids=evidence_ids or [],
        severity=severity,
        stage=stage,
        outcome=outcome,
        duration_ms=duration_ms,
        metadata={},
    )


def _make_rich_trace() -> Any:
    """A trace with a failure → retry → recovery chain for inspector tests."""
    from autocontext.analytics.run_trace import CausalEdge, RunTrace

    events = [
        _evt("e1", "action", "compete", 1, actor_id="competitor"),
        _evt("e2", "validation", "match", 2, actor_id="system", cause_ids=["e1"]),
        _evt("e3", "failure", "match", 3, actor_id="system",
             outcome="failure", cause_ids=["e2"], severity="error"),
        _evt("e4", "retry", "compete", 4, actor_id="competitor",
             cause_ids=["e3"], outcome=None),
        _evt("e5", "action", "compete", 5, actor_id="competitor",
             cause_ids=["e4"]),
        _evt("e6", "validation", "match", 6, actor_id="system",
             cause_ids=["e5"]),
        _evt("e7", "recovery", "match", 7, actor_id="system",
             cause_ids=["e3", "e6"], evidence_ids=["e3", "e6"]),
        _evt("e8", "observation", "gate", 8, actor_id="analyst",
             cause_ids=["e7"]),
        _evt("e9", "checkpoint", "gate", 9, actor_id="system",
             cause_ids=["e8"]),
    ]

    edges = [
        CausalEdge(source_event_id="e1", target_event_id="e2", relation="triggers"),
        CausalEdge(source_event_id="e2", target_event_id="e3", relation="causes"),
        CausalEdge(source_event_id="e3", target_event_id="e4", relation="retries"),
        CausalEdge(source_event_id="e4", target_event_id="e5", relation="triggers"),
        CausalEdge(source_event_id="e5", target_event_id="e6", relation="triggers"),
        CausalEdge(source_event_id="e3", target_event_id="e7", relation="recovers"),
        CausalEdge(source_event_id="e6", target_event_id="e7", relation="causes"),
        CausalEdge(source_event_id="e7", target_event_id="e8", relation="triggers"),
        CausalEdge(source_event_id="e8", target_event_id="e9", relation="triggers"),
    ]

    return RunTrace(
        trace_id="trace-rich",
        run_id="run-1",
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=edges,
        created_at="2026-03-14T12:00:00Z",
        metadata={},
    )


def _make_multi_gen_traces() -> list[Any]:
    """Two generation-scoped traces for comparison tests."""
    from autocontext.analytics.run_trace import RunTrace

    gen0_events = [
        _evt("g0-e1", "action", "compete", 1, gen=0),
        _evt("g0-e2", "failure", "match", 2, gen=0, outcome="failure", severity="error"),
    ]
    gen1_events = [
        _evt("g1-e1", "action", "compete", 1, gen=1),
        _evt("g1-e2", "validation", "match", 2, gen=1),
        _evt("g1-e3", "observation", "gate", 3, gen=1),
    ]

    return [
        RunTrace(
            trace_id="trace-g0", run_id="run-1", generation_index=0,
            schema_version="1.0.0", events=gen0_events,
            causal_edges=[], created_at="2026-03-14T12:00:00Z", metadata={},
        ),
        RunTrace(
            trace_id="trace-g1", run_id="run-1", generation_index=1,
            schema_version="1.0.0", events=gen1_events,
            causal_edges=[], created_at="2026-03-14T12:01:00Z", metadata={},
        ),
    ]


# ===========================================================================
# TimelineFilter
# ===========================================================================


class TestTimelineFilter:
    def test_defaults(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineFilter

        f = TimelineFilter()
        assert f.roles is None
        assert f.stages is None
        assert f.categories is None
        assert f.event_types is None
        assert f.min_severity is None
        assert f.generation_index is None

    def test_custom(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineFilter

        f = TimelineFilter(
            roles=["competitor", "analyst"],
            stages=["compete"],
            categories=["action"],
            min_severity="warning",
        )
        assert f.roles == ["competitor", "analyst"]
        assert f.stages == ["compete"]
        assert f.min_severity == "warning"


# ===========================================================================
# TimelineEntry
# ===========================================================================


class TestTimelineEntry:
    def test_construction(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineEntry

        evt = _evt("e1", "action", "compete", 1)
        entry = TimelineEntry(
            entry_id="entry-1",
            event=evt,
            depth=0,
            children_count=0,
            artifact_links=[],
            highlight=False,
        )
        assert entry.entry_id == "entry-1"
        assert entry.event.event_id == "e1"
        assert entry.depth == 0

    def test_roundtrip(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineEntry

        evt = _evt("e1", "action", "compete", 1)
        entry = TimelineEntry(
            entry_id="entry-2",
            event=evt,
            depth=1,
            children_count=3,
            artifact_links=["knowledge/playbook.md"],
            highlight=True,
        )
        d = entry.to_dict()
        restored = TimelineEntry.from_dict(d)
        assert restored.entry_id == "entry-2"
        assert restored.depth == 1
        assert restored.children_count == 3
        assert restored.highlight is True
        assert restored.event.event_id == "e1"


# ===========================================================================
# TimelineBuilder
# ===========================================================================


class TestTimelineBuilder:
    def test_build_basic(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineBuilder

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace)

        assert len(entries) == len(trace.events)
        # Entries should be in sequence order
        seqs = [e.event.sequence_number for e in entries]
        assert seqs == sorted(seqs)

    def test_build_filter_by_category(self) -> None:
        from autocontext.analytics.timeline_inspector import (
            TimelineBuilder,
            TimelineFilter,
        )

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace, TimelineFilter(categories=["failure", "recovery"]))

        categories = {e.event.category for e in entries}
        assert categories == {"failure", "recovery"}

    def test_build_filter_by_stage(self) -> None:
        from autocontext.analytics.timeline_inspector import (
            TimelineBuilder,
            TimelineFilter,
        )

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace, TimelineFilter(stages=["gate"]))

        stages = {e.event.stage for e in entries}
        assert stages == {"gate"}

    def test_build_filter_by_role(self) -> None:
        from autocontext.analytics.timeline_inspector import (
            TimelineBuilder,
            TimelineFilter,
        )

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace, TimelineFilter(roles=["analyst"]))

        actors = {e.event.actor.actor_id for e in entries}
        assert actors == {"analyst"}

    def test_build_filter_by_severity(self) -> None:
        from autocontext.analytics.timeline_inspector import (
            TimelineBuilder,
            TimelineFilter,
        )

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace, TimelineFilter(min_severity="error"))

        # Only events with severity >= error
        assert len(entries) > 0
        assert all(e.event.severity in ("error", "critical") for e in entries)

    def test_build_summary_collapses(self) -> None:
        """Summary should collapse consecutive same-stage events."""
        from autocontext.analytics.timeline_inspector import TimelineBuilder

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        summary = builder.build_summary(trace)

        # Summary should have fewer entries than full timeline
        assert len(summary) <= len(trace.events)
        # Should still cover all stages present
        full_stages = {e.stage for e in trace.events}
        summary_stages = {e.event.stage for e in summary}
        assert summary_stages == full_stages

    def test_compare_generations(self) -> None:
        from autocontext.analytics.timeline_inspector import TimelineBuilder

        traces = _make_multi_gen_traces()
        builder = TimelineBuilder()
        comparison = builder.compare_generations(traces)

        # Should include entries from both generations
        gens = {e.event.generation_index for e in comparison}
        assert gens == {0, 1}

    def test_build_empty_trace(self) -> None:
        from autocontext.analytics.run_trace import RunTrace
        from autocontext.analytics.timeline_inspector import TimelineBuilder

        empty = RunTrace(
            trace_id="empty", run_id="run-0", generation_index=None,
            schema_version="1.0.0", events=[], causal_edges=[],
            created_at="", metadata={},
        )
        builder = TimelineBuilder()
        assert builder.build(empty) == []
        assert builder.build_summary(empty) == []

    def test_highlight_failures(self) -> None:
        """Failure and recovery events should be highlighted."""
        from autocontext.analytics.timeline_inspector import TimelineBuilder

        trace = _make_rich_trace()
        builder = TimelineBuilder()
        entries = builder.build(trace)

        failure_entries = [e for e in entries if e.event.category == "failure"]
        assert all(e.highlight for e in failure_entries)

        recovery_entries = [e for e in entries if e.event.category == "recovery"]
        assert all(e.highlight for e in recovery_entries)


# ===========================================================================
# RunInspection
# ===========================================================================


class TestRunInspection:
    def test_construction(self) -> None:
        from autocontext.analytics.timeline_inspector import RunInspection

        insp = RunInspection(
            summary="Run with 1 failure, 1 recovery",
            total_events=9,
            events_by_category={"action": 2, "failure": 1, "recovery": 1},
            events_by_stage={"compete": 3, "match": 4, "gate": 2},
            failure_count=1,
            recovery_count=1,
            retry_count=1,
            causal_depth=5,
        )
        assert insp.total_events == 9
        assert insp.failure_count == 1
        assert insp.causal_depth == 5


# ===========================================================================
# GenerationInspection
# ===========================================================================


class TestGenerationInspection:
    def test_construction(self) -> None:
        from autocontext.analytics.timeline_inspector import GenerationInspection

        insp = GenerationInspection(
            generation_index=0,
            summary="Gen 0 with failure",
            total_events=2,
            events_by_category={"action": 1, "failure": 1},
            events_by_stage={"compete": 1, "match": 1},
            failure_count=1,
            recovery_count=0,
        )
        assert insp.generation_index == 0
        assert insp.failure_count == 1


# ===========================================================================
# StateInspector
# ===========================================================================


class TestStateInspector:
    def test_inspect_run(self) -> None:
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        inspector = StateInspector()
        result = inspector.inspect_run(trace)

        assert result.total_events == 9
        assert result.failure_count == 1
        assert result.recovery_count == 1
        assert result.retry_count == 1
        assert result.causal_depth >= 1

    def test_inspect_generation(self) -> None:
        from autocontext.analytics.timeline_inspector import StateInspector

        traces = _make_multi_gen_traces()
        inspector = StateInspector()

        gen0 = inspector.inspect_generation(traces[0], 0)
        assert gen0.generation_index == 0
        assert gen0.total_events == 2
        assert gen0.failure_count == 1

        gen1 = inspector.inspect_generation(traces[1], 1)
        assert gen1.generation_index == 1
        assert gen1.total_events == 3
        assert gen1.failure_count == 0

    def test_find_failure_paths(self) -> None:
        """Should find the causal chain leading to each failure."""
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        inspector = StateInspector()
        paths = inspector.find_failure_paths(trace)

        assert len(paths) >= 1
        # Each path ends with a failure event
        for path in paths:
            assert path[-1].category == "failure"

    def test_find_recovery_paths(self) -> None:
        """Should find the causal chain leading to each recovery."""
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        inspector = StateInspector()
        paths = inspector.find_recovery_paths(trace)

        assert len(paths) >= 1
        for path in paths:
            assert path[-1].category == "recovery"

    def test_dependency_chain(self) -> None:
        """Given an event ID, trace backward through cause_event_ids."""
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        inspector = StateInspector()

        # e7 (recovery) depends on e3 and e6
        chain = inspector.dependency_chain(trace, "e7")
        chain_ids = [e.event_id for e in chain]
        assert "e7" in chain_ids
        assert "e3" in chain_ids  # direct cause

    def test_dependency_chain_unknown_event(self) -> None:
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        inspector = StateInspector()
        chain = inspector.dependency_chain(trace, "nonexistent")
        assert chain == []

    def test_dependency_chain_uses_causal_edges_without_inline_causes(self) -> None:
        from autocontext.analytics.run_trace import RunTrace, TraceEvent
        from autocontext.analytics.timeline_inspector import StateInspector

        trace = _make_rich_trace()
        edge_only_events = [
            TraceEvent(
                event_id=event.event_id,
                run_id=event.run_id,
                generation_index=event.generation_index,
                sequence_number=event.sequence_number,
                timestamp=event.timestamp,
                category=event.category,
                event_type=event.event_type,
                actor=event.actor,
                resources=event.resources,
                summary=event.summary,
                detail=event.detail,
                parent_event_id=None,
                cause_event_ids=[],
                evidence_ids=event.evidence_ids,
                severity=event.severity,
                stage=event.stage,
                outcome=event.outcome,
                duration_ms=event.duration_ms,
                metadata=event.metadata,
            )
            for event in trace.events
        ]
        edge_only_trace = RunTrace(
            trace_id="trace-edge-only",
            run_id=trace.run_id,
            generation_index=trace.generation_index,
            schema_version=trace.schema_version,
            events=edge_only_events,
            causal_edges=trace.causal_edges,
            created_at=trace.created_at,
            metadata=trace.metadata,
        )

        inspector = StateInspector()
        chain = inspector.dependency_chain(edge_only_trace, "e7")
        chain_ids = [event.event_id for event in chain]
        assert "e3" in chain_ids
        assert "e6" in chain_ids
        assert chain_ids[-1] == "e7"
        assert inspector.inspect_run(edge_only_trace).causal_depth >= 4

    def test_empty_trace(self) -> None:
        from autocontext.analytics.run_trace import RunTrace
        from autocontext.analytics.timeline_inspector import StateInspector

        empty = RunTrace(
            trace_id="empty", run_id="run-0", generation_index=None,
            schema_version="1.0.0", events=[], causal_edges=[],
            created_at="", metadata={},
        )
        inspector = StateInspector()
        result = inspector.inspect_run(empty)
        assert result.total_events == 0
        assert result.failure_count == 0

        assert inspector.find_failure_paths(empty) == []
        assert inspector.find_recovery_paths(empty) == []
