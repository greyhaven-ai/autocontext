"""Shared renderers for human-facing analytics artifacts.

Structured reports and traces remain the source of truth. This module owns the
presentation view models and deterministic Markdown/HTML rendering used by
operator-facing surfaces.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html import escape
from typing import Any

from autocontext.analytics.html_artifact_shell import TIMELINE_FILTER_SCRIPT, html_document


@dataclass(frozen=True, slots=True)
class FindingView:
    title: str
    description: str
    finding_type: str
    severity: str
    category: str
    evidence_event_ids: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class FailureMotifView:
    pattern_name: str
    occurrence_count: int
    evidence_event_ids: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class RecoveryPathView:
    failure_event_id: str
    recovery_event_id: str
    path_event_ids: tuple[str, ...] = ()
    description: str = ""


@dataclass(frozen=True, slots=True)
class TraceWriteupView:
    run_id: str
    summary: str
    scenario: str = ""
    scenario_family: str = ""
    findings: tuple[FindingView, ...] = ()
    failure_motifs: tuple[FailureMotifView, ...] = ()
    recovery_paths: tuple[RecoveryPathView, ...] = ()

    @property
    def context(self) -> str:
        return " | ".join(part for part in [self.scenario, self.scenario_family] if part)


@dataclass(frozen=True, slots=True)
class WeaknessReportView:
    run_id: str
    scenario: str
    weaknesses: tuple[FindingView, ...] = ()
    failure_motifs: tuple[FailureMotifView, ...] = ()
    recovery_analysis: str = ""
    recommendations: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class TimelineEventView:
    event_id: str
    sequence_number: int
    generation_index: int | None
    timestamp: str
    category: str
    stage: str
    event_type: str
    actor_id: str
    severity: str
    summary: str
    outcome: str
    artifact_links: tuple[str, ...] = ()
    evidence_ids: tuple[str, ...] = ()
    children_count: int = 0
    highlight: bool = False


@dataclass(frozen=True, slots=True)
class TimelineInspectionView:
    trace_id: str
    run_id: str
    created_at: str
    summary: str
    item_count: int
    error_count: int
    recovery_count: int
    events: tuple[TimelineEventView, ...] = ()
    failure_paths: tuple[tuple[str, ...], ...] = ()
    recovery_paths: tuple[tuple[str, ...], ...] = ()


@dataclass(frozen=True, slots=True)
class CurationItemView:
    title: str
    body: str
    source: str = ""


@dataclass(frozen=True, slots=True)
class ScenarioCurationView:
    scenario_name: str
    active_lessons: list[CurationItemView] = field(default_factory=list)
    stale_lessons: list[CurationItemView] = field(default_factory=list)
    superseded_lessons: list[CurationItemView] = field(default_factory=list)
    hints: list[CurationItemView] = field(default_factory=list)
    dead_ends: list[CurationItemView] = field(default_factory=list)
    weakness_findings: list[CurationItemView] = field(default_factory=list)
    progress_reports: list[CurationItemView] = field(default_factory=list)


def trace_writeup_view(writeup: Any) -> TraceWriteupView:
    metadata = _metadata(writeup)
    return TraceWriteupView(
        run_id=str(getattr(writeup, "run_id", "")),
        summary=str(getattr(writeup, "summary", "")),
        scenario=str(metadata.get("scenario", "")),
        scenario_family=str(metadata.get("scenario_family", "")),
        findings=tuple(_finding_view(finding) for finding in getattr(writeup, "findings", [])),
        failure_motifs=tuple(_failure_motif_view(motif) for motif in getattr(writeup, "failure_motifs", [])),
        recovery_paths=tuple(_recovery_path_view(path) for path in getattr(writeup, "recovery_paths", [])),
    )


def weakness_report_view(report: Any) -> WeaknessReportView:
    metadata = _metadata(report)
    return WeaknessReportView(
        run_id=str(getattr(report, "run_id", "")),
        scenario=str(metadata.get("scenario", "")),
        weaknesses=tuple(_finding_view(finding) for finding in getattr(report, "weaknesses", [])),
        failure_motifs=tuple(_failure_motif_view(motif) for motif in getattr(report, "failure_motifs", [])),
        recovery_analysis=str(getattr(report, "recovery_analysis", "")),
        recommendations=tuple(str(rec) for rec in getattr(report, "recommendations", [])),
    )


def timeline_inspection_view(trace: Any) -> TimelineInspectionView:
    from autocontext.analytics.timeline_inspector import StateInspector, TimelineBuilder

    inspector = StateInspector()
    builder = TimelineBuilder()
    run_inspection = inspector.inspect_run(trace)
    entries = builder.build(trace)
    event_views = tuple(
        TimelineEventView(
            event_id=entry.event.event_id,
            sequence_number=entry.event.sequence_number,
            generation_index=entry.event.generation_index,
            timestamp=entry.event.timestamp,
            category=entry.event.category,
            stage=entry.event.stage,
            event_type=entry.event.event_type,
            actor_id=entry.event.actor.actor_id,
            severity=entry.event.severity,
            summary=entry.event.summary,
            outcome=str(entry.event.outcome or ""),
            artifact_links=tuple(entry.artifact_links),
            evidence_ids=tuple(entry.event.evidence_ids),
            children_count=entry.children_count,
            highlight=entry.highlight,
        )
        for entry in entries
    )
    return TimelineInspectionView(
        trace_id=str(trace.trace_id),
        run_id=str(trace.run_id),
        created_at=str(trace.created_at),
        summary=run_inspection.summary,
        item_count=len(event_views),
        error_count=run_inspection.failure_count,
        recovery_count=run_inspection.recovery_count,
        events=event_views,
        failure_paths=tuple(tuple(event.event_id for event in path) for path in inspector.find_failure_paths(trace)),
        recovery_paths=tuple(tuple(event.event_id for event in path) for path in inspector.find_recovery_paths(trace)),
    )


def scenario_curation_view_from_artifacts(artifacts: Any, scenario_name: str, *, max_reports: int = 2) -> ScenarioCurationView:
    lessons = artifacts.lesson_store.read_lessons(scenario_name)
    current_generation = artifacts.lesson_store.current_generation(scenario_name)
    active_lessons = [
        CurationItemView(
            title=lesson.id,
            body=lesson.text,
            source=f"lessons.json:generation={lesson.meta.generation}",
        )
        for lesson in artifacts.lesson_store.get_applicable_lessons(scenario_name, current_generation=current_generation)
    ]
    stale_lessons = [
        CurationItemView(
            title=lesson.id,
            body=lesson.text,
            source=f"lessons.json:last_validated_gen={lesson.meta.last_validated_gen}",
        )
        for lesson in artifacts.lesson_store.get_stale_lessons(scenario_name, current_generation=current_generation)
    ]
    superseded_lessons = [
        CurationItemView(
            title=lesson.id,
            body=lesson.text,
            source=f"lessons.json:superseded_by={lesson.meta.superseded_by}",
        )
        for lesson in lessons
        if lesson.is_superseded()
    ]
    hints = _markdown_items("Hints", artifacts.read_hints(scenario_name), "hints.md")
    dead_ends = _markdown_items("Dead ends", artifacts.read_dead_ends(scenario_name), "dead_ends.md")
    weakness_findings = _weakness_items(artifacts.read_latest_weakness_reports(scenario_name, max_reports=max_reports))
    progress_reports = _markdown_report_items(
        "Progress report",
        artifacts.read_latest_progress_reports(scenario_name, max_reports=max_reports),
    )
    return ScenarioCurationView(
        scenario_name=scenario_name,
        active_lessons=active_lessons,
        stale_lessons=stale_lessons,
        superseded_lessons=superseded_lessons,
        hints=hints,
        dead_ends=dead_ends,
        weakness_findings=weakness_findings,
        progress_reports=progress_reports,
    )


def render_trace_writeup_markdown(view: TraceWriteupView) -> str:
    lines = [f"# Run Summary: {view.run_id}", ""]
    if view.context:
        lines.append(f"**Context:** {view.context}")
        lines.append("")

    lines.append("## Trace Summary")
    lines.append(view.summary)
    lines.append("")

    lines.append("## Findings")
    if view.findings:
        for finding in view.findings:
            evidence = ", ".join(finding.evidence_event_ids) or "none"
            lines.append(
                f"- **{finding.title}** [{finding.finding_type}/{finding.severity}] "
                f"{finding.description} (evidence: {evidence})"
            )
    else:
        lines.append("No notable findings.")
    lines.append("")

    lines.append("## Failure Motifs")
    if view.failure_motifs:
        for motif in view.failure_motifs:
            lines.append(f"- **{motif.pattern_name}**: {motif.occurrence_count} occurrence(s)")
    else:
        lines.append("No recurring failure motifs.")
    lines.append("")

    lines.append("## Recovery Paths")
    if view.recovery_paths:
        for recovery in view.recovery_paths:
            lines.append(
                f"- {recovery.failure_event_id} -> {recovery.recovery_event_id} "
                f"({len(recovery.path_event_ids)} events)"
            )
    else:
        lines.append("No recovery paths observed.")

    return "\n".join(lines)


def render_weakness_report_markdown(view: WeaknessReportView) -> str:
    lines = [
        f"# Weakness Report: {view.run_id}",
        f"**Scenario:** {view.scenario or 'unknown'}",
        "",
    ]
    if not view.weaknesses:
        lines.append("No weaknesses identified.")
    else:
        lines.append(f"**Summary:** {len(view.weaknesses)} weakness(es) detected")
        lines.append("")
        for weakness in view.weaknesses:
            evidence = ", ".join(weakness.evidence_event_ids) or "none"
            lines.append(f"## [{weakness.severity.upper()}] {weakness.title}")
            lines.append(weakness.description)
            lines.append(f"- Category: {weakness.category}")
            lines.append(f"- Evidence events: {evidence}")
            lines.append("")

    lines.append("## Recovery Analysis")
    lines.append(view.recovery_analysis or "No recovery analysis available.")
    lines.append("")
    lines.append("## Recommendations")
    if view.recommendations:
        for recommendation in view.recommendations:
            lines.append(f"- {recommendation}")
    else:
        lines.append("- No immediate recommendations.")
    return "\n".join(lines)


def render_trace_writeup_html(view: TraceWriteupView) -> str:
    context = f'<p class="muted">{_h(view.context)}</p>' if view.context else ""
    findings = _render_findings_html(view.findings, empty="No notable findings.")
    motifs = _render_motifs_html(view.failure_motifs)
    recoveries = _render_recovery_paths_html(view.recovery_paths)
    body = f"""
<header>
  <p class="eyebrow">Trace writeup</p>
  <h1>Run Summary: {_h(view.run_id)}</h1>
  {context}
</header>
<section>
  <h2>Trace Summary</h2>
  <p>{_h(view.summary)}</p>
</section>
<section>
  <h2>Findings</h2>
  {findings}
</section>
<section>
  <h2>Failure Motifs</h2>
  {motifs}
</section>
<section>
  <h2>Recovery Paths</h2>
  {recoveries}
</section>
"""
    return html_document(f"Run Summary: {view.run_id}", body)


def render_weakness_report_html(view: WeaknessReportView) -> str:
    weaknesses = _render_findings_html(view.weaknesses, empty="No weaknesses identified.")
    motifs = _render_motifs_html(view.failure_motifs)
    recommendations = _render_list_html(view.recommendations, empty="No immediate recommendations.")
    body = f"""
<header>
  <p class="eyebrow">Weakness report</p>
  <h1>Weakness Report: {_h(view.run_id)}</h1>
  <p class="muted">Scenario: {_h(view.scenario or 'unknown')}</p>
</header>
<section>
  <h2>Weaknesses</h2>
  {weaknesses}
</section>
<section>
  <h2>Failure Motifs</h2>
  {motifs}
</section>
<section>
  <h2>Recovery Analysis</h2>
  <p>{_h(view.recovery_analysis or 'No recovery analysis available.')}</p>
</section>
<section>
  <h2>Recommendations</h2>
  {recommendations}
</section>
"""
    return html_document(f"Weakness Report: {view.run_id}", body)


def render_markdown_document_html(title: str, markdown: str) -> str:
    body = f"""
<header>
  <p class="eyebrow">Markdown fallback</p>
  <h1>{_h(title)}</h1>
</header>
<section>
  <pre class="markdown-fallback">{_h(markdown)}</pre>
</section>
"""
    return html_document(title, body)


def render_timeline_inspection_html(view: TimelineInspectionView) -> str:
    events = "\n".join(_render_timeline_event_html(event) for event in view.events)
    if not events:
        events = '<p class="empty">No timeline events.</p>'
    failure_paths = _render_path_list(view.failure_paths, "No failure paths.")
    recovery_paths = _render_path_list(view.recovery_paths, "No recovery paths.")
    body = f"""
<header>
  <p class="eyebrow">Timeline inspection</p>
  <h1>Runtime Timeline: {_h(view.run_id)}</h1>
  <p class="muted">Trace {_h(view.trace_id)} | {_h(view.created_at)}</p>
</header>
<section class="metric-row">
  <div><strong>{view.item_count}</strong><span>events</span></div>
  <div><strong>{view.error_count}</strong><span>failures</span></div>
  <div><strong>{view.recovery_count}</strong><span>recoveries</span></div>
</section>
<section>
  <h2>Summary</h2>
  <p>{_h(view.summary)}</p>
</section>
<section>
  <h2>Filters</h2>
  <div class="filters" aria-label="Timeline filters">
    <label>Category <input data-filter="category" placeholder="failure"></label>
    <label>Stage <input data-filter="stage" placeholder="match"></label>
    <label>Severity <input data-filter="severity" placeholder="error"></label>
    <label>Generation <input data-filter="generation" placeholder="1"></label>
  </div>
</section>
<section>
  <h2>Events</h2>
  <div class="timeline">{events}</div>
</section>
<section class="grid-two">
  <div>
    <h2>Failure Paths</h2>
    {failure_paths}
  </div>
  <div>
    <h2>Recovery Paths</h2>
    {recovery_paths}
  </div>
</section>
"""
    return html_document(f"Runtime Timeline: {view.run_id}", body, script=TIMELINE_FILTER_SCRIPT)


def render_scenario_curation_html(view: ScenarioCurationView) -> str:
    export_text = _curation_export_markdown(view)
    body = f"""
<header>
  <p class="eyebrow">Read-only derived artifact</p>
  <h1>Scenario Curation: {_h(view.scenario_name)}</h1>
  <p class="muted">Review accumulated scenario knowledge without mutating source artifacts.</p>
</header>
{_render_curation_section("Active Lessons", view.active_lessons)}
{_render_curation_section("Stale Lessons", view.stale_lessons)}
{_render_curation_section("Superseded Lessons", view.superseded_lessons)}
{_render_curation_section("Hints", view.hints)}
{_render_curation_section("Dead Ends", view.dead_ends)}
{_render_curation_section("Weakness Findings", view.weakness_findings)}
{_render_curation_section("Progress Reports", view.progress_reports)}
<section>
  <h2>Export</h2>
  <pre data-export-format="markdown">{_h(export_text)}</pre>
</section>
"""
    return html_document(f"Scenario Curation: {view.scenario_name}", body)


def _finding_view(finding: Any) -> FindingView:
    return FindingView(
        title=str(getattr(finding, "title", "")),
        description=str(getattr(finding, "description", "")),
        finding_type=str(getattr(finding, "finding_type", "")),
        severity=str(getattr(finding, "severity", "")),
        category=str(getattr(finding, "category", "")),
        evidence_event_ids=tuple(str(eid) for eid in getattr(finding, "evidence_event_ids", [])),
    )


def _failure_motif_view(motif: Any) -> FailureMotifView:
    return FailureMotifView(
        pattern_name=str(getattr(motif, "pattern_name", "")),
        occurrence_count=int(getattr(motif, "occurrence_count", 0) or 0),
        evidence_event_ids=tuple(str(eid) for eid in getattr(motif, "evidence_event_ids", [])),
        description=str(getattr(motif, "description", "")),
    )


def _recovery_path_view(path: Any) -> RecoveryPathView:
    return RecoveryPathView(
        failure_event_id=str(getattr(path, "failure_event_id", "")),
        recovery_event_id=str(getattr(path, "recovery_event_id", "")),
        path_event_ids=tuple(str(eid) for eid in getattr(path, "path_event_ids", [])),
        description=str(getattr(path, "description", "")),
    )


def _metadata(obj: Any) -> dict[str, Any]:
    metadata = getattr(obj, "metadata", {})
    return dict(metadata) if isinstance(metadata, dict) else {}


def _h(value: object) -> str:
    return escape(str(value), quote=True)


def _safe_id(raw: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_-]+", "-", raw).strip("-")
    return safe or "item"


def _render_findings_html(findings: tuple[FindingView, ...], *, empty: str) -> str:
    if not findings:
        return f'<p class="empty">{_h(empty)}</p>'
    return "\n".join(
        f"""
<article class="finding">
  <h3>{_h(finding.title)}</h3>
  <p>
    <span class="badge">{_h(finding.finding_type)}</span>
    <span class="badge severity-{_h(finding.severity)}">{_h(finding.severity)}</span>
    <span class="badge">{_h(finding.category)}</span>
  </p>
  <p>{_h(finding.description)}</p>
  {_render_evidence_html(finding.evidence_event_ids)}
</article>
"""
        for finding in findings
    )


def _render_evidence_html(evidence_ids: tuple[str, ...]) -> str:
    if not evidence_ids:
        return '<p class="muted">Evidence: none</p>'
    items = "".join(f'<li id="evidence-{_safe_id(eid)}"><code>{_h(eid)}</code></li>' for eid in evidence_ids)
    return f'<p class="muted">Evidence</p><ul>{items}</ul>'


def _render_motifs_html(motifs: tuple[FailureMotifView, ...]) -> str:
    if not motifs:
        return '<p class="empty">No recurring failure motifs.</p>'
    return "\n".join(
        f"""
<article class="finding">
  <h3>{_h(motif.pattern_name)}</h3>
  <p>{motif.occurrence_count} occurrence(s)</p>
  {_render_evidence_html(motif.evidence_event_ids)}
</article>
"""
        for motif in motifs
    )


def _render_recovery_paths_html(paths: tuple[RecoveryPathView, ...]) -> str:
    if not paths:
        return '<p class="empty">No recovery paths observed.</p>'
    return "\n".join(
        f"""
<article class="finding">
  <h3><code>{_h(path.failure_event_id)}</code> -> <code>{_h(path.recovery_event_id)}</code></h3>
  <p>{len(path.path_event_ids)} events</p>
  {_render_path_list((path.path_event_ids,), "No path events.")}
</article>
"""
        for path in paths
    )


def _render_list_html(items: tuple[str, ...], *, empty: str) -> str:
    if not items:
        return f'<p class="empty">{_h(empty)}</p>'
    return "<ul>" + "".join(f"<li>{_h(item)}</li>" for item in items) + "</ul>"


def _render_timeline_event_html(event: TimelineEventView) -> str:
    artifact_links = _render_artifact_links(event.artifact_links)
    evidence = _render_evidence_html(event.evidence_ids)
    generation = "" if event.generation_index is None else str(event.generation_index)
    return f"""
<article class="event" data-category="{_h(event.category)}" data-stage="{_h(event.stage)}"
    data-severity="{_h(event.severity)}" data-generation="{_h(generation)}">
  <h3>#{event.sequence_number} {_h(event.event_type)}</h3>
  <p>
    <span class="badge">{_h(event.stage)}</span>
    <span class="badge">{_h(event.category)}</span>
    <span class="badge severity-{_h(event.severity)}">{_h(event.severity)}</span>
    <span class="badge">{_h(event.actor_id)}</span>
  </p>
  <p>{_h(event.summary)}</p>
  <p class="muted">Event <code>{_h(event.event_id)}</code> | Outcome {_h(event.outcome or "unknown")}</p>
  {artifact_links}
  {evidence}
</article>
"""


def _render_artifact_links(links: tuple[str, ...]) -> str:
    if not links:
        return ""
    items = "".join(f"<li><code>{_h(link)}</code></li>" for link in links)
    return f'<p class="muted">Artifacts</p><ul>{items}</ul>'


def _render_path_list(paths: tuple[tuple[str, ...], ...], empty: str) -> str:
    if not paths:
        return f'<p class="empty">{_h(empty)}</p>'
    return "<ul>" + "".join(
        "<li>" + " -> ".join(f"<code>{_h(event_id)}</code>" for event_id in path) + "</li>"
        for path in paths
    ) + "</ul>"


def _render_curation_section(title: str, items: list[CurationItemView]) -> str:
    if not items:
        content = '<p class="empty">No items.</p>'
    else:
        content = "\n".join(
            f"""
<article class="curation-item">
  <h3>{_h(item.title)}</h3>
  <p>{_h(item.body)}</p>
  <p class="muted">{_h(item.source)}</p>
</article>
"""
            for item in items
        )
    return f"""
<section>
  <h2>{_h(title)}</h2>
  {content}
</section>
"""


def _curation_export_markdown(view: ScenarioCurationView) -> str:
    sections = [
        ("Active Lessons", view.active_lessons),
        ("Stale Lessons", view.stale_lessons),
        ("Superseded Lessons", view.superseded_lessons),
        ("Hints", view.hints),
        ("Dead Ends", view.dead_ends),
        ("Weakness Findings", view.weakness_findings),
        ("Progress Reports", view.progress_reports),
    ]
    lines = [f"# Scenario Curation: {view.scenario_name}", "", "Read-only derived artifact.", ""]
    for title, items in sections:
        lines.append(f"## {title}")
        if not items:
            lines.append("No items.")
        else:
            for item in items:
                source = f" [{item.source}]" if item.source else ""
                lines.append(f"- **{item.title}**{source}: {item.body}")
        lines.append("")
    return "\n".join(lines).strip()


def _markdown_items(title: str, content: str, source: str) -> list[CurationItemView]:
    rendered = content.strip()
    if not rendered:
        return []
    return [CurationItemView(title=title, body=rendered, source=source)]


def _weakness_items(reports: list[object]) -> list[CurationItemView]:
    items: list[CurationItemView] = []
    for report in reports:
        run_id = str(getattr(report, "run_id", "unknown"))
        weaknesses = getattr(report, "weaknesses", None)
        if isinstance(weaknesses, list):
            for weakness in weaknesses:
                items.append(
                    CurationItemView(
                        title=str(getattr(weakness, "title", "Weakness")),
                        body=str(getattr(weakness, "description", "")),
                        source=run_id,
                    )
                )
            continue
        to_markdown = getattr(report, "to_markdown", None)
        if callable(to_markdown):
            items.append(CurationItemView(title=f"Weakness report {run_id}", body=str(to_markdown()), source=run_id))
    return items


def _markdown_report_items(title: str, reports: list[object]) -> list[CurationItemView]:
    items: list[CurationItemView] = []
    for report in reports:
        run_id = str(getattr(report, "run_id", "unknown"))
        to_markdown = getattr(report, "to_markdown", None)
        if callable(to_markdown):
            items.append(CurationItemView(title=f"{title} {run_id}", body=str(to_markdown()), source=run_id))
    return items
