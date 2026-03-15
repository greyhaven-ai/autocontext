"""Tests for AC-262: canonical run-state event model and causal trace artifact.

Covers: ActorRef, ResourceRef, TraceEvent, CausalEdge, RunTrace, TraceStore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _make_actor(actor_type: str = "role", actor_id: str = "competitor") -> Any:
    from autocontext.analytics.run_trace import ActorRef

    return ActorRef(actor_type=actor_type, actor_id=actor_id, actor_name=actor_id.title())


def _make_resource(
    resource_type: str = "artifact",
    resource_id: str = "playbook-v3",
) -> Any:
    from autocontext.analytics.run_trace import ResourceRef

    return ResourceRef(
        resource_type=resource_type,
        resource_id=resource_id,
        resource_name="Playbook v3",
        resource_path="knowledge/grid_ctf/playbook.md",
    )


def _make_event(
    event_id: str = "evt-1",
    category: str = "action",
    **overrides: Any,
) -> Any:
    from autocontext.analytics.run_trace import TraceEvent

    defaults: dict[str, Any] = {
        "event_id": event_id,
        "run_id": "run-1",
        "generation_index": 0,
        "sequence_number": 1,
        "timestamp": "2026-03-14T12:00:00Z",
        "category": category,
        "event_type": "strategy_submit",
        "actor": _make_actor(),
        "resources": [_make_resource()],
        "summary": "Competitor submitted strategy",
        "detail": {},
        "parent_event_id": None,
        "cause_event_ids": [],
        "evidence_ids": [],
        "severity": "info",
        "stage": "compete",
        "outcome": "success",
        "duration_ms": 1200,
        "metadata": {},
    }
    defaults.update(overrides)
    return TraceEvent(**defaults)


def _make_trace(**overrides: Any) -> Any:
    from autocontext.analytics.run_trace import CausalEdge, RunTrace

    e1 = _make_event("evt-1", "action", sequence_number=1)
    e2 = _make_event(
        "evt-2", "validation", event_type="score_validation",
        sequence_number=2, cause_event_ids=["evt-1"],
        stage="match", outcome="success",
    )
    e3 = _make_event(
        "evt-3", "observation", event_type="score_reported",
        sequence_number=3, cause_event_ids=["evt-2"],
        evidence_ids=["evt-1", "evt-2"], stage="match",
    )

    defaults: dict[str, Any] = {
        "trace_id": "trace-1",
        "run_id": "run-1",
        "generation_index": None,
        "schema_version": "1.0.0",
        "events": [e1, e2, e3],
        "causal_edges": [
            CausalEdge(source_event_id="evt-1", target_event_id="evt-2", relation="triggers"),
            CausalEdge(source_event_id="evt-2", target_event_id="evt-3", relation="causes"),
        ],
        "created_at": "2026-03-14T12:00:00Z",
        "metadata": {},
    }
    defaults.update(overrides)
    return RunTrace(**defaults)


# ===========================================================================
# ActorRef
# ===========================================================================


class TestActorRef:
    def test_construction(self) -> None:
        actor = _make_actor()
        assert actor.actor_type == "role"
        assert actor.actor_id == "competitor"
        assert actor.actor_name == "Competitor"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.run_trace import ActorRef

        actor = _make_actor("tool", "grid_ctf_engine")
        d = actor.to_dict()
        restored = ActorRef.from_dict(d)
        assert restored.actor_type == "tool"
        assert restored.actor_id == "grid_ctf_engine"


# ===========================================================================
# ResourceRef
# ===========================================================================


class TestResourceRef:
    def test_construction(self) -> None:
        res = _make_resource()
        assert res.resource_type == "artifact"
        assert res.resource_id == "playbook-v3"
        assert res.resource_path == "knowledge/grid_ctf/playbook.md"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.run_trace import ResourceRef

        res = _make_resource("model", "claude-sonnet")
        d = res.to_dict()
        restored = ResourceRef.from_dict(d)
        assert restored.resource_type == "model"
        assert restored.resource_id == "claude-sonnet"


# ===========================================================================
# TraceEvent
# ===========================================================================


class TestTraceEvent:
    def test_construction(self) -> None:
        evt = _make_event()
        assert evt.event_id == "evt-1"
        assert evt.category == "action"
        assert evt.actor.actor_id == "competitor"
        assert len(evt.resources) == 1

    def test_roundtrip(self) -> None:
        from autocontext.analytics.run_trace import TraceEvent

        evt = _make_event("evt-rt", "tool_invocation", event_type="repl_exec")
        d = evt.to_dict()
        restored = TraceEvent.from_dict(d)
        assert restored.event_id == "evt-rt"
        assert restored.category == "tool_invocation"
        assert restored.actor.actor_id == "competitor"
        assert restored.resources[0].resource_id == "playbook-v3"

    def test_with_parent(self) -> None:
        child = _make_event("evt-child", "observation", parent_event_id="evt-parent")
        assert child.parent_event_id == "evt-parent"

    def test_with_causes_and_evidence(self) -> None:
        evt = _make_event(
            "evt-caused", "recovery",
            cause_event_ids=["evt-fail-1", "evt-fail-2"],
            evidence_ids=["evt-fail-1"],
        )
        assert evt.cause_event_ids == ["evt-fail-1", "evt-fail-2"]
        assert evt.evidence_ids == ["evt-fail-1"]

    def test_all_categories_accepted(self) -> None:
        """Schema should accept all canonical categories without error."""
        categories = [
            "observation", "hypothesis", "action", "tool_invocation",
            "validation", "retry", "cancellation", "failure",
            "recovery", "checkpoint", "evidence_link",
        ]
        for cat in categories:
            evt = _make_event(f"evt-{cat}", cat)
            assert evt.category == cat

    def test_all_severity_levels(self) -> None:
        for sev in ("info", "warning", "error", "critical"):
            evt = _make_event(severity=sev)
            assert evt.severity == sev

    def test_all_stages(self) -> None:
        stages = ["init", "compete", "analyze", "coach", "architect", "curate", "match", "gate"]
        for stg in stages:
            evt = _make_event(stage=stg)
            assert evt.stage == stg


# ===========================================================================
# CausalEdge
# ===========================================================================


class TestCausalEdge:
    def test_construction(self) -> None:
        from autocontext.analytics.run_trace import CausalEdge

        edge = CausalEdge(
            source_event_id="evt-1",
            target_event_id="evt-2",
            relation="triggers",
        )
        assert edge.source_event_id == "evt-1"
        assert edge.relation == "triggers"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.run_trace import CausalEdge

        edge = CausalEdge(
            source_event_id="evt-a",
            target_event_id="evt-b",
            relation="recovers",
        )
        d = edge.to_dict()
        restored = CausalEdge.from_dict(d)
        assert restored.source_event_id == "evt-a"
        assert restored.relation == "recovers"

    def test_all_relations(self) -> None:
        from autocontext.analytics.run_trace import CausalEdge

        for rel in ("causes", "depends_on", "triggers", "supersedes", "retries", "recovers"):
            edge = CausalEdge(source_event_id="a", target_event_id="b", relation=rel)
            assert edge.relation == rel


# ===========================================================================
# RunTrace
# ===========================================================================


class TestRunTrace:
    def test_construction(self) -> None:
        trace = _make_trace()
        assert trace.trace_id == "trace-1"
        assert trace.schema_version == "1.0.0"
        assert len(trace.events) == 3
        assert len(trace.causal_edges) == 2

    def test_roundtrip(self) -> None:
        from autocontext.analytics.run_trace import RunTrace

        trace = _make_trace()
        d = trace.to_dict()
        restored = RunTrace.from_dict(d)
        assert restored.trace_id == "trace-1"
        assert len(restored.events) == 3
        assert restored.events[0].actor.actor_id == "competitor"
        assert len(restored.causal_edges) == 2
        assert restored.causal_edges[0].relation == "triggers"

    def test_generation_scoped(self) -> None:
        trace = _make_trace(generation_index=2)
        assert trace.generation_index == 2

    def test_schema_version(self) -> None:
        trace = _make_trace(schema_version="2.0.0")
        assert trace.schema_version == "2.0.0"

    def test_events_ordered_by_sequence(self) -> None:
        trace = _make_trace()
        seqs = [e.sequence_number for e in trace.events]
        assert seqs == sorted(seqs)

    def test_dependency_edges_present(self) -> None:
        """Causal edges express ordering between events."""
        trace = _make_trace()
        sources = {e.source_event_id for e in trace.causal_edges}
        targets = {e.target_event_id for e in trace.causal_edges}
        event_ids = {e.event_id for e in trace.events}
        assert sources.issubset(event_ids)
        assert targets.issubset(event_ids)

    def test_evidence_chain(self) -> None:
        """Evidence IDs on events reference other events in the trace."""
        trace = _make_trace()
        event_ids = {e.event_id for e in trace.events}
        for evt in trace.events:
            for eid in evt.evidence_ids:
                assert eid in event_ids


# ===========================================================================
# TraceStore
# ===========================================================================


class TestTraceStore:
    def test_persist_and_load(self, tmp_path: Path) -> None:
        from autocontext.analytics.run_trace import TraceStore

        store = TraceStore(tmp_path)
        trace = _make_trace()
        path = store.persist(trace)
        assert path.exists()

        loaded = store.load("trace-1")
        assert loaded is not None
        assert loaded.trace_id == "trace-1"
        assert len(loaded.events) == 3

    def test_load_missing(self, tmp_path: Path) -> None:
        from autocontext.analytics.run_trace import TraceStore

        store = TraceStore(tmp_path)
        assert store.load("nonexistent") is None

    def test_list_traces(self, tmp_path: Path) -> None:
        from autocontext.analytics.run_trace import TraceStore

        store = TraceStore(tmp_path)
        for i in range(3):
            store.persist(_make_trace(trace_id=f"trace-{i}"))
        assert len(store.list_traces()) == 3

    def test_list_by_run_id(self, tmp_path: Path) -> None:
        from autocontext.analytics.run_trace import TraceStore

        store = TraceStore(tmp_path)
        store.persist(_make_trace(trace_id="t1", run_id="run-A"))
        store.persist(_make_trace(trace_id="t2", run_id="run-B"))
        store.persist(_make_trace(trace_id="t3", run_id="run-A"))

        results = store.list_traces(run_id="run-A")
        assert len(results) == 2
        assert all(t.run_id == "run-A" for t in results)

    def test_list_by_generation(self, tmp_path: Path) -> None:
        from autocontext.analytics.run_trace import TraceStore

        store = TraceStore(tmp_path)
        store.persist(_make_trace(trace_id="t1", generation_index=None))
        store.persist(_make_trace(trace_id="t2", generation_index=1))
        store.persist(_make_trace(trace_id="t3", generation_index=2))

        results = store.list_traces(generation_index=1)
        assert len(results) == 1
        assert results[0].generation_index == 1
