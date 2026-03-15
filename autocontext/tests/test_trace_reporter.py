"""Tests for AC-264: trace-grounded writeups and weakness reports.

Covers: TraceFinding, FailureMotif, RecoveryPath, TraceWriteup,
WeaknessReport, TraceReporter, ReportStore.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Shared helpers — build rich traces for report generation
# ---------------------------------------------------------------------------


def _actor(actor_id: str = "competitor") -> Any:
    from autocontext.analytics.run_trace import ActorRef

    return ActorRef(actor_type="role", actor_id=actor_id, actor_name=actor_id.title())


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
        actor=_actor(actor_id),
        resources=[_resource()],
        summary=f"{category} event at seq {seq}",
        detail={},
        parent_event_id=None,
        cause_event_ids=cause_ids or [],
        evidence_ids=evidence_ids or [],
        severity=severity,
        stage=stage,
        outcome=outcome,
        duration_ms=100,
        metadata={},
    )


def _make_trace_with_failures() -> Any:
    """Trace with failure→retry→recovery chain and a second unrecovered failure."""
    from autocontext.analytics.run_trace import CausalEdge, RunTrace

    events = [
        # Normal start
        _evt("e1", "action", "compete", 1),
        # First failure chain: failure → retry → new action → recovery
        _evt("e2", "validation", "match", 2, actor_id="system", cause_ids=["e1"],
             event_type="score_validation"),
        _evt("e3", "failure", "match", 3, actor_id="system", outcome="failure",
             cause_ids=["e2"], severity="error", event_type="validation_failure"),
        _evt("e4", "retry", "compete", 4, cause_ids=["e3"],
             event_type="strategy_retry"),
        _evt("e5", "action", "compete", 5, cause_ids=["e4"],
             event_type="strategy_submit"),
        _evt("e6", "validation", "match", 6, actor_id="system", cause_ids=["e5"],
             event_type="score_validation"),
        _evt("e7", "recovery", "match", 7, actor_id="system",
             cause_ids=["e3", "e6"], evidence_ids=["e3", "e6"],
             event_type="validation_recovery"),
        # Second failure — no recovery
        _evt("e8", "action", "compete", 8, event_type="strategy_submit"),
        _evt("e9", "failure", "match", 9, actor_id="system", outcome="failure",
             cause_ids=["e8"], severity="error", event_type="validation_failure"),
        # Observation — turning point (score jumped)
        _evt("e10", "observation", "gate", 10, actor_id="analyst",
             cause_ids=["e7"], event_type="score_jump"),
        # Another validation_failure type for motif detection
        _evt("e11", "failure", "match", 11, actor_id="system", outcome="failure",
             severity="warning", event_type="tool_failure"),
    ]

    edges = [
        CausalEdge(source_event_id="e1", target_event_id="e2", relation="triggers"),
        CausalEdge(source_event_id="e2", target_event_id="e3", relation="causes"),
        CausalEdge(source_event_id="e3", target_event_id="e4", relation="retries"),
        CausalEdge(source_event_id="e4", target_event_id="e5", relation="triggers"),
        CausalEdge(source_event_id="e5", target_event_id="e6", relation="triggers"),
        CausalEdge(source_event_id="e3", target_event_id="e7", relation="recovers"),
        CausalEdge(source_event_id="e6", target_event_id="e7", relation="causes"),
        CausalEdge(source_event_id="e8", target_event_id="e9", relation="causes"),
        CausalEdge(source_event_id="e7", target_event_id="e10", relation="triggers"),
    ]

    return RunTrace(
        trace_id="trace-report",
        run_id="run-1",
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=edges,
        created_at="2026-03-14T12:00:00Z",
        metadata={},
    )


def _make_clean_trace() -> Any:
    """Trace with no failures — clean run."""
    from autocontext.analytics.run_trace import CausalEdge, RunTrace

    events = [
        _evt("c1", "action", "compete", 1),
        _evt("c2", "validation", "match", 2, actor_id="system", cause_ids=["c1"]),
        _evt("c3", "observation", "gate", 3, actor_id="analyst", cause_ids=["c2"]),
        _evt("c4", "checkpoint", "gate", 4, actor_id="system", cause_ids=["c3"]),
    ]
    edges = [
        CausalEdge(source_event_id="c1", target_event_id="c2", relation="triggers"),
        CausalEdge(source_event_id="c2", target_event_id="c3", relation="triggers"),
        CausalEdge(source_event_id="c3", target_event_id="c4", relation="triggers"),
    ]
    return RunTrace(
        trace_id="trace-clean",
        run_id="run-clean",
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=edges,
        created_at="2026-03-14T12:00:00Z",
        metadata={},
    )


# ===========================================================================
# TraceFinding
# ===========================================================================


class TestTraceFinding:
    def test_construction(self) -> None:
        from autocontext.analytics.trace_reporter import TraceFinding

        f = TraceFinding(
            finding_id="f-1",
            finding_type="weakness",
            title="Validation failure in match stage",
            description="Score validation failed after strategy submission",
            evidence_event_ids=["e2", "e3"],
            severity="high",
            category="failure_motif",
        )
        assert f.finding_type == "weakness"
        assert f.evidence_event_ids == ["e2", "e3"]

    def test_roundtrip(self) -> None:
        from autocontext.analytics.trace_reporter import TraceFinding

        f = TraceFinding(
            finding_id="f-2",
            finding_type="strength",
            title="Quick recovery",
            description="System recovered from failure within 3 events",
            evidence_event_ids=["e3", "e7"],
            severity="low",
            category="recovery_path",
        )
        d = f.to_dict()
        restored = TraceFinding.from_dict(d)
        assert restored.finding_id == "f-2"
        assert restored.finding_type == "strength"
        assert restored.evidence_event_ids == ["e3", "e7"]


# ===========================================================================
# FailureMotif
# ===========================================================================


class TestFailureMotif:
    def test_construction(self) -> None:
        from autocontext.analytics.trace_reporter import FailureMotif

        m = FailureMotif(
            motif_id="m-1",
            pattern_name="validation_failure",
            occurrence_count=2,
            evidence_event_ids=["e3", "e9"],
            description="Recurring validation failures in match stage",
        )
        assert m.pattern_name == "validation_failure"
        assert m.occurrence_count == 2

    def test_roundtrip(self) -> None:
        from autocontext.analytics.trace_reporter import FailureMotif

        m = FailureMotif(
            motif_id="m-2",
            pattern_name="tool_failure",
            occurrence_count=1,
            evidence_event_ids=["e11"],
            description="Single tool failure",
        )
        d = m.to_dict()
        restored = FailureMotif.from_dict(d)
        assert restored.motif_id == "m-2"
        assert restored.occurrence_count == 1


# ===========================================================================
# RecoveryPath
# ===========================================================================


class TestRecoveryPath:
    def test_construction(self) -> None:
        from autocontext.analytics.trace_reporter import RecoveryPath

        r = RecoveryPath(
            recovery_id="r-1",
            failure_event_id="e3",
            recovery_event_id="e7",
            path_event_ids=["e3", "e4", "e5", "e6", "e7"],
            description="Recovery from validation failure via retry",
        )
        assert r.failure_event_id == "e3"
        assert r.recovery_event_id == "e7"

    def test_roundtrip(self) -> None:
        from autocontext.analytics.trace_reporter import RecoveryPath

        r = RecoveryPath(
            recovery_id="r-2",
            failure_event_id="e9",
            recovery_event_id="e10",
            path_event_ids=["e9", "e10"],
            description="Quick recovery",
        )
        d = r.to_dict()
        restored = RecoveryPath.from_dict(d)
        assert restored.recovery_id == "r-2"
        assert restored.path_event_ids == ["e9", "e10"]


# ===========================================================================
# TraceWriteup
# ===========================================================================


class TestTraceWriteup:
    def test_construction(self) -> None:
        from autocontext.analytics.trace_reporter import TraceWriteup

        w = TraceWriteup(
            writeup_id="w-1",
            run_id="run-1",
            generation_index=None,
            findings=[],
            failure_motifs=[],
            recovery_paths=[],
            summary="Clean run with no issues.",
            created_at="2026-03-14T12:00:00Z",
        )
        assert w.writeup_id == "w-1"
        assert w.summary == "Clean run with no issues."

    def test_roundtrip(self) -> None:
        from autocontext.analytics.trace_reporter import (
            FailureMotif,
            TraceFinding,
            TraceWriteup,
        )

        w = TraceWriteup(
            writeup_id="w-2",
            run_id="run-1",
            generation_index=0,
            findings=[
                TraceFinding(
                    finding_id="f-1", finding_type="weakness",
                    title="Failure", description="desc",
                    evidence_event_ids=["e3"], severity="high",
                    category="failure_motif",
                ),
            ],
            failure_motifs=[
                FailureMotif(
                    motif_id="m-1", pattern_name="validation_failure",
                    occurrence_count=2, evidence_event_ids=["e3", "e9"],
                    description="Recurring",
                ),
            ],
            recovery_paths=[],
            summary="Run had 1 failure motif.",
            created_at="2026-03-14T12:00:00Z",
        )
        d = w.to_dict()
        restored = TraceWriteup.from_dict(d)
        assert restored.writeup_id == "w-2"
        assert len(restored.findings) == 1
        assert len(restored.failure_motifs) == 1


# ===========================================================================
# WeaknessReport
# ===========================================================================


class TestWeaknessReport:
    def test_construction(self) -> None:
        from autocontext.analytics.trace_reporter import WeaknessReport

        r = WeaknessReport(
            report_id="wr-1",
            run_id="run-1",
            weaknesses=[],
            failure_motifs=[],
            recovery_analysis="No recoveries needed.",
            recommendations=["Continue current approach."],
            created_at="2026-03-14T12:00:00Z",
        )
        assert r.report_id == "wr-1"
        assert len(r.recommendations) == 1

    def test_roundtrip(self) -> None:
        from autocontext.analytics.trace_reporter import (
            TraceFinding,
            WeaknessReport,
        )

        r = WeaknessReport(
            report_id="wr-2",
            run_id="run-1",
            weaknesses=[
                TraceFinding(
                    finding_id="f-1", finding_type="weakness",
                    title="Failure", description="desc",
                    evidence_event_ids=["e3"], severity="high",
                    category="failure_motif",
                ),
            ],
            failure_motifs=[],
            recovery_analysis="One recovery via retry.",
            recommendations=["Investigate validation", "Add pre-check"],
            created_at="2026-03-14T12:00:00Z",
        )
        d = r.to_dict()
        restored = WeaknessReport.from_dict(d)
        assert restored.report_id == "wr-2"
        assert len(restored.weaknesses) == 1
        assert len(restored.recommendations) == 2


# ===========================================================================
# TraceReporter — extract_findings
# ===========================================================================


class TestTraceReporterExtractFindings:
    def test_finds_failures_as_weaknesses(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        findings = reporter.extract_findings(trace)

        weakness_findings = [f for f in findings if f.finding_type == "weakness"]
        # 3 failure events → 3 weakness findings
        assert len(weakness_findings) == 3

    def test_finds_recoveries_as_strengths(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        findings = reporter.extract_findings(trace)

        strength_findings = [f for f in findings if f.finding_type == "strength"]
        # 1 recovery event → 1 strength finding
        assert len(strength_findings) == 1

    def test_evidence_references_trace_events(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        findings = reporter.extract_findings(trace)

        event_ids = {e.event_id for e in trace.events}
        for finding in findings:
            for eid in finding.evidence_event_ids:
                assert eid in event_ids, f"{eid} not in trace events"

    def test_empty_trace(self) -> None:
        from autocontext.analytics.run_trace import RunTrace
        from autocontext.analytics.trace_reporter import TraceReporter

        empty = RunTrace(
            trace_id="empty", run_id="run-0", generation_index=None,
            schema_version="1.0.0", events=[], causal_edges=[],
            created_at="", metadata={},
        )
        reporter = TraceReporter()
        assert reporter.extract_findings(empty) == []

    def test_clean_trace_no_weaknesses(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_clean_trace()
        reporter = TraceReporter()
        findings = reporter.extract_findings(trace)

        weakness_findings = [f for f in findings if f.finding_type == "weakness"]
        assert len(weakness_findings) == 0


# ===========================================================================
# TraceReporter — extract_failure_motifs
# ===========================================================================


class TestTraceReporterExtractMotifs:
    def test_groups_recurring_failures(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        motifs = reporter.extract_failure_motifs(trace)

        # "validation_failure" appears twice (e3, e9)
        vf_motifs = [m for m in motifs if m.pattern_name == "validation_failure"]
        assert len(vf_motifs) == 1
        assert vf_motifs[0].occurrence_count == 2

    def test_single_occurrence_also_reported(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        motifs = reporter.extract_failure_motifs(trace)

        # "tool_failure" appears once (e11)
        tf_motifs = [m for m in motifs if m.pattern_name == "tool_failure"]
        assert len(tf_motifs) == 1
        assert tf_motifs[0].occurrence_count == 1

    def test_no_failures_no_motifs(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_clean_trace()
        reporter = TraceReporter()
        assert reporter.extract_failure_motifs(trace) == []


# ===========================================================================
# TraceReporter — extract_recovery_paths
# ===========================================================================


class TestTraceReporterExtractRecoveryPaths:
    def test_finds_recovery_chain(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        paths = reporter.extract_recovery_paths(trace)

        assert len(paths) == 1
        path = paths[0]
        assert path.recovery_event_id == "e7"
        # Path should include the failure that was recovered from
        assert "e3" in path.path_event_ids

    def test_no_recoveries_no_paths(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_clean_trace()
        reporter = TraceReporter()
        assert reporter.extract_recovery_paths(trace) == []


# ===========================================================================
# TraceReporter — generate_writeup
# ===========================================================================


class TestTraceReporterGenerateWriteup:
    def test_basic_writeup(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        writeup = reporter.generate_writeup(trace)

        assert writeup.run_id == "run-1"
        assert len(writeup.findings) > 0
        assert len(writeup.failure_motifs) > 0
        assert len(writeup.summary) > 0

    def test_writeup_cites_evidence(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        writeup = reporter.generate_writeup(trace)

        # At least one finding must have evidence
        has_evidence = any(f.evidence_event_ids for f in writeup.findings)
        assert has_evidence

    def test_clean_writeup(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_clean_trace()
        reporter = TraceReporter()
        writeup = reporter.generate_writeup(trace)

        assert writeup.run_id == "run-clean"
        assert len([f for f in writeup.findings if f.finding_type == "weakness"]) == 0
        assert len(writeup.summary) > 0


# ===========================================================================
# TraceReporter — generate_weakness_report
# ===========================================================================


class TestTraceReporterGenerateWeaknessReport:
    def test_basic_report(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        report = reporter.generate_weakness_report(trace)

        assert report.run_id == "run-1"
        assert len(report.weaknesses) > 0
        assert len(report.recommendations) > 0

    def test_report_includes_recovery_analysis(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        report = reporter.generate_weakness_report(trace)

        assert len(report.recovery_analysis) > 0

    def test_weakness_findings_only(self) -> None:
        """Weakness report should only contain weakness-type findings."""
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_trace_with_failures()
        reporter = TraceReporter()
        report = reporter.generate_weakness_report(trace)

        for w in report.weaknesses:
            assert w.finding_type == "weakness"

    def test_clean_trace_no_weaknesses(self) -> None:
        from autocontext.analytics.trace_reporter import TraceReporter

        trace = _make_clean_trace()
        reporter = TraceReporter()
        report = reporter.generate_weakness_report(trace)

        assert len(report.weaknesses) == 0


# ===========================================================================
# ReportStore
# ===========================================================================


class TestReportStore:
    def test_persist_and_load_writeup(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, TraceWriteup

        store = ReportStore(tmp_path)
        w = TraceWriteup(
            writeup_id="w-store",
            run_id="run-1",
            generation_index=None,
            findings=[], failure_motifs=[], recovery_paths=[],
            summary="Test writeup.",
            created_at="2026-03-14T12:00:00Z",
        )
        path = store.persist_writeup(w)
        assert path.exists()

        loaded = store.load_writeup("w-store")
        assert loaded is not None
        assert loaded.summary == "Test writeup."

    def test_load_missing_writeup(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore

        store = ReportStore(tmp_path)
        assert store.load_writeup("nonexistent") is None

    def test_persist_and_load_weakness_report(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, WeaknessReport

        store = ReportStore(tmp_path)
        r = WeaknessReport(
            report_id="wr-store",
            run_id="run-1",
            weaknesses=[], failure_motifs=[],
            recovery_analysis="None needed.",
            recommendations=["All good."],
            created_at="2026-03-14T12:00:00Z",
        )
        path = store.persist_weakness_report(r)
        assert path.exists()

        loaded = store.load_weakness_report("wr-store")
        assert loaded is not None
        assert loaded.recommendations == ["All good."]

    def test_load_missing_weakness_report(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore

        store = ReportStore(tmp_path)
        assert store.load_weakness_report("nonexistent") is None

    def test_list_writeups(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, TraceWriteup

        store = ReportStore(tmp_path)
        for i in range(3):
            store.persist_writeup(TraceWriteup(
                writeup_id=f"w-{i}", run_id="run-1",
                generation_index=None,
                findings=[], failure_motifs=[], recovery_paths=[],
                summary="", created_at="",
            ))
        assert len(store.list_writeups()) == 3

    def test_list_weakness_reports(self, tmp_path: Path) -> None:
        from autocontext.analytics.trace_reporter import ReportStore, WeaknessReport

        store = ReportStore(tmp_path)
        for i in range(2):
            store.persist_weakness_report(WeaknessReport(
                report_id=f"wr-{i}", run_id="run-1",
                weaknesses=[], failure_motifs=[],
                recovery_analysis="", recommendations=[],
                created_at="",
            ))
        assert len(store.list_weakness_reports()) == 2
