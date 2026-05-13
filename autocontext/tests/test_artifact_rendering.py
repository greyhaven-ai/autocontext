"""Tests for shared human-facing artifact rendering."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _finding(title: str = "Validation <failed>") -> Any:
    from autocontext.analytics.trace_reporter import TraceFinding

    return TraceFinding(
        finding_id="finding-1",
        finding_type="weakness",
        title=title,
        description="The strategy emitted <bad> output.",
        evidence_event_ids=["e-1", "e-2"],
        severity="high",
        category="failure_motif",
    )


def test_trace_writeup_markdown_is_rendered_from_shared_view_model() -> None:
    from autocontext.analytics.artifact_rendering import (
        render_trace_writeup_markdown,
        trace_writeup_view,
    )
    from autocontext.analytics.trace_reporter import FailureMotif, RecoveryPath, TraceWriteup

    writeup = TraceWriteup(
        writeup_id="writeup-1",
        run_id="run-1",
        generation_index=None,
        findings=[_finding()],
        failure_motifs=[
            FailureMotif(
                motif_id="motif-1",
                pattern_name="validation_failure",
                occurrence_count=2,
                evidence_event_ids=["e-1", "e-2"],
                description="Repeated validation failures.",
            ),
        ],
        recovery_paths=[
            RecoveryPath(
                recovery_id="recovery-1",
                failure_event_id="e-1",
                recovery_event_id="e-3",
                path_event_ids=["e-1", "e-2", "e-3"],
                description="Retry recovered the run.",
            ),
        ],
        summary="Trace-grounded summary.",
        created_at="2026-05-11T12:00:00Z",
        metadata={"scenario": "billing_bot", "scenario_family": "agent_task"},
    )

    view = trace_writeup_view(writeup)

    assert view.run_id == "run-1"
    assert view.context == "billing_bot | agent_task"
    assert render_trace_writeup_markdown(view) == writeup.to_markdown()


def test_trace_writeup_html_escapes_model_content_and_links_evidence() -> None:
    from autocontext.analytics.trace_reporter import TraceWriteup

    writeup = TraceWriteup(
        writeup_id="writeup-1",
        run_id="run-<1>",
        generation_index=None,
        findings=[_finding("Dangerous <script> title")],
        failure_motifs=[],
        recovery_paths=[],
        summary="Summary with <script>alert(1)</script>",
        created_at="2026-05-11T12:00:00Z",
        metadata={"scenario": "billing_bot"},
    )

    html = writeup.to_html()

    assert "<script>" not in html
    assert "&lt;script&gt;" in html
    assert 'id="evidence-e-1"' in html
    assert "Dangerous &lt;script&gt; title" in html


def test_weakness_report_html_uses_same_domain_view() -> None:
    from autocontext.analytics.artifact_rendering import (
        render_weakness_report_html,
        weakness_report_view,
    )
    from autocontext.analytics.trace_reporter import WeaknessReport

    report = WeaknessReport(
        report_id="weakness-1",
        run_id="run-1",
        weaknesses=[_finding()],
        failure_motifs=[],
        recovery_analysis="Recovered via retry.",
        recommendations=["Add a validator", "Review <unsafe> summaries"],
        created_at="2026-05-11T12:00:00Z",
        metadata={"scenario": "billing_bot"},
    )

    view = weakness_report_view(report)
    html = render_weakness_report_html(view)

    assert "Weakness Report: run-1" in html
    assert "Add a validator" in html
    assert "Review &lt;unsafe&gt; summaries" in html
    assert report.to_html() == html


def _trace_for_timeline() -> Any:
    from autocontext.analytics.run_trace import ActorRef, CausalEdge, ResourceRef, RunTrace, TraceEvent

    actor = ActorRef(actor_type="role", actor_id="analyst", actor_name="Analyst")
    resource = ResourceRef(
        resource_type="artifact",
        resource_id="analysis-1",
        resource_name="analysis",
        resource_path="knowledge/billing/analysis/gen_1.md",
    )
    events = [
        TraceEvent(
            event_id="e-1",
            run_id="run-1",
            generation_index=1,
            sequence_number=1,
            timestamp="2026-05-11T12:00:01Z",
            category="failure",
            event_type="validation_failure",
            actor=actor,
            resources=[resource],
            summary="Validation failed <badly>",
            detail={},
            parent_event_id=None,
            cause_event_ids=[],
            evidence_ids=[],
            severity="error",
            stage="match",
            outcome="failed",
            duration_ms=10,
        ),
        TraceEvent(
            event_id="e-2",
            run_id="run-1",
            generation_index=1,
            sequence_number=2,
            timestamp="2026-05-11T12:00:02Z",
            category="recovery",
            event_type="retry_recovered",
            actor=actor,
            resources=[resource],
            summary="Retry recovered",
            detail={},
            parent_event_id=None,
            cause_event_ids=["e-1"],
            evidence_ids=["e-1"],
            severity="info",
            stage="match",
            outcome="success",
            duration_ms=12,
        ),
    ]
    return RunTrace(
        trace_id="trace-run-1",
        run_id="run-1",
        generation_index=None,
        schema_version="1.0.0",
        events=events,
        causal_edges=[CausalEdge(source_event_id="e-1", target_event_id="e-2", relation="recovers")],
        created_at="2026-05-11T12:00:00Z",
        metadata={"scenario": "billing_bot"},
    )


def test_persist_run_inspection_writes_json_and_html(tmp_path: Path) -> None:
    from autocontext.loop.trace_artifacts import persist_run_inspection

    trace = _trace_for_timeline()
    analytics_root = tmp_path / "analytics"
    trace_path = analytics_root / "traces" / "trace-run-1.json"

    persist_run_inspection(trace, analytics_root, trace_path)

    json_path = analytics_root / "inspections" / "trace-run-1.json"
    html_path = analytics_root / "inspections" / "trace-run-1.html"
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    html = html_path.read_text(encoding="utf-8")

    assert payload["run_id"] == "run-1"
    assert "Runtime Timeline: run-1" in html
    assert "Validation failed &lt;badly&gt;" in html
    assert 'data-category="failure"' in html


# -- AC-749: per-generation summaries in TimelineInspectionView + HTML --


def test_timeline_inspection_view_exposes_per_generation_summaries() -> None:
    """AC-749: the view extractor must surface the same per-generation
    inspection data that the JSON payload already carries, so the HTML can
    render per-generation summary blocks for failure/recovery comparison
    without inventing a new analytics model."""
    from autocontext.analytics.artifact_rendering import timeline_inspection_view

    trace = _trace_for_timeline()
    view = timeline_inspection_view(trace)

    # Only one generation in the fixture (generation_index=1 across the two
    # events). The view must expose a tuple of generation summaries that
    # accurately count failures/recoveries within that generation.
    assert hasattr(view, "generation_summaries")
    summaries = view.generation_summaries
    assert len(summaries) == 1
    summary = summaries[0]
    assert summary.generation_index == 1
    assert summary.failure_count == 1
    assert summary.recovery_count == 1


def test_render_timeline_inspection_html_includes_generation_section() -> None:
    """The HTML body must surface the per-generation summary so operators
    can scan generation-level failure/recovery counts without parsing the
    JSON payload."""
    from autocontext.analytics.artifact_rendering import (
        render_timeline_inspection_html,
        timeline_inspection_view,
    )

    trace = _trace_for_timeline()
    view = timeline_inspection_view(trace)
    html = render_timeline_inspection_html(view)

    # Generations section header + at least one row labelled with the
    # generation index. Exact markup is implementation detail; we only
    # pin the presence + a data attribute consumers can hook onto.
    assert ">Generations<" in html
    assert 'data-generation-index="1"' in html
    # The per-generation counts must be present in the rendered output.
    # We use generic substrings so wording can change without breaking
    # the test, but the numeric counts and the dim labels are pinned.
    assert 'data-generation-failure-count="1"' in html, (
        "rendered HTML must expose the generation's failure count for filtering / inspection"
    )
    assert 'data-generation-recovery-count="1"' in html


# -- AC-749: `autoctx analytics render-timeline` CLI subcommand --


def test_analytics_render_timeline_writes_html_from_stored_trace(tmp_path: Path) -> None:
    """`autoctx analytics render-timeline --trace-id <id>` loads a persisted
    `RunTrace` from the `TraceStore`, runs the existing view extractor and
    HTML renderer, and writes the resulting HTML to `--output` (or a
    default location under the analytics root). The CLI is thin glue --
    no new analytics model -- so the test pins the I/O contract only."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from autocontext.analytics.run_trace import TraceStore
    from autocontext.cli import app
    from autocontext.config.settings import AppSettings

    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    # Persist the fixture trace through the real TraceStore so the CLI
    # exercises the production load path. Analytics dir convention is
    # `<knowledge_root>/analytics` (per cli_analytics.py / server/writeup.py).
    trace = _trace_for_timeline()
    analytics_root = settings.knowledge_root / "analytics"
    store = TraceStore(analytics_root)
    store.persist(trace)

    runner = CliRunner()
    output_path = tmp_path / "out.html"

    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(
            app,
            [
                "analytics",
                "render-timeline",
                "--trace-id",
                trace.trace_id,
                "--output",
                str(output_path),
            ],
        )

    assert result.exit_code == 0, result.output
    assert output_path.exists()
    html = output_path.read_text(encoding="utf-8")
    # Same content contract as the run-end-time renderer.
    assert "Runtime Timeline: run-1" in html
    assert 'data-generation-index="1"' in html


def test_analytics_render_timeline_rejects_trace_id_path_traversal(tmp_path: Path) -> None:
    """AC-749 review (PR #943 P2): user-supplied --trace-id must not let an
    attacker escape the analytics/traces directory. The previous version
    path-joined ``trace_id`` directly, so ``trace_id='../external'`` would
    load ``analytics/external.json`` and, because the default output path
    was derived from the loaded ``trace.trace_id``, write
    ``analytics/external.html`` outside the documented inspections dir.
    We plant a fully-valid RunTrace whose own ``trace_id`` field also
    contains the traversal so both halves of the exploit can fire, then
    assert the CLI rejects the input before touching the filesystem."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from autocontext.cli import app
    from autocontext.config.settings import AppSettings

    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    # Plant a fully-valid RunTrace at the would-be traversal target so a
    # successful exploit would actually load + render it. analytics/
    # external.json sits one level above the traces dir at analytics/traces/.
    analytics_root = settings.knowledge_root / "analytics"
    (analytics_root / "traces").mkdir(parents=True, exist_ok=True)
    poisoned = _trace_for_timeline().model_copy(update={"trace_id": "../external"})
    external = analytics_root / "external.json"
    external.write_text(json.dumps(poisoned.to_dict()), encoding="utf-8")

    runner = CliRunner()
    bad_html = analytics_root / "external.html"

    for bad_id in ("../external", "foo/bar", "..", "."):
        with patch("autocontext.cli.load_settings", return_value=settings):
            result = runner.invoke(
                app,
                ["analytics", "render-timeline", "--trace-id", bad_id],
            )
        assert result.exit_code != 0, f"expected non-zero exit for trace id {bad_id!r}, got: {result.output}"
        assert not bad_html.exists(), f"HTML written outside inspections dir for trace id {bad_id!r}"
        # Also pin that the error message mentions the trace id, so we
        # know the validator fired rather than some downstream accident.
        assert "trace id" in result.output.lower() or "trace-id" in result.output.lower(), (
            f"expected a trace-id validation error for {bad_id!r}, got: {result.output}"
        )


def test_analytics_render_timeline_default_output_uses_validated_trace_id(tmp_path: Path) -> None:
    """AC-749 review (PR #943 P2): the default output path must be derived
    from the validated requested id, not from any field reflected back by
    the loaded trace. We persist a trace whose ``trace_id`` happens to be a
    valid leaf (so it round-trips through ``TraceStore``) and verify the
    default HTML path lands under the inspections dir as documented."""
    from unittest.mock import patch

    from typer.testing import CliRunner

    from autocontext.analytics.run_trace import TraceStore
    from autocontext.cli import app
    from autocontext.config.settings import AppSettings

    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )

    trace = _trace_for_timeline()
    analytics_root = settings.knowledge_root / "analytics"
    TraceStore(analytics_root).persist(trace)

    runner = CliRunner()
    with patch("autocontext.cli.load_settings", return_value=settings):
        result = runner.invoke(
            app,
            ["analytics", "render-timeline", "--trace-id", trace.trace_id],
        )

    assert result.exit_code == 0, result.output
    expected = analytics_root / "inspections" / f"{trace.trace_id}.html"
    assert expected.exists(), f"expected default HTML at {expected}, output: {result.output}"


def test_scenario_curation_html_is_read_only_and_exportable() -> None:
    from autocontext.analytics.artifact_rendering import (
        CurationItemView,
        ScenarioCurationView,
        render_scenario_curation_html,
    )

    view = ScenarioCurationView(
        scenario_name="billing_bot",
        active_lessons=[
            CurationItemView(
                title="lesson_1",
                body="Always verify posted charges.",
                source="lessons.json:generation=3",
            ),
        ],
        stale_lessons=[],
        superseded_lessons=[],
        hints=[CurationItemView(title="Hints", body="Prefer concise escalation.", source="hints.md")],
        dead_ends=[],
        weakness_findings=[
            CurationItemView(title="Validation Failure", body="Missing account state.", source="run-1"),
        ],
        progress_reports=[],
    )

    html = render_scenario_curation_html(view)

    assert "Scenario Curation: billing_bot" in html
    assert "Read-only derived artifact" in html
    assert "Always verify posted charges." in html
    assert 'data-export-format="markdown"' in html
