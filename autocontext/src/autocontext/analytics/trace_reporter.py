"""Trace-grounded writeups and weakness reports (AC-264).

Consumes canonical run-event traces (AC-262) to produce structured,
evidence-backed writeups and weakness reports. Treats structured events
as the source of truth and prose as a rendering layer.

Key types:
- TraceFinding: a structured finding backed by trace evidence
- FailureMotif: a recurring failure pattern grouped by event_type
- RecoveryPath: a failure→recovery chain with intermediate events
- TraceWriteup: complete writeup with findings, motifs, recovery paths
- WeaknessReport: weakness-focused report with recommendations
- TraceReporter: extraction and generation logic (no LLM needed)
- ReportStore: JSON-file persistence for reports
"""

from __future__ import annotations

import uuid
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from autocontext.analytics.run_trace import RunTrace, TraceEvent
from autocontext.util.json_io import read_json, write_json


@dataclass(slots=True)
class TraceFinding:
    """A structured finding backed by trace evidence."""

    finding_id: str
    finding_type: str  # weakness, strength, pattern, turning_point
    title: str
    description: str
    evidence_event_ids: list[str]
    severity: str  # low, medium, high, critical
    category: str  # failure_motif, recovery_path, turning_point, recurring_pattern
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "finding_id": self.finding_id,
            "finding_type": self.finding_type,
            "title": self.title,
            "description": self.description,
            "evidence_event_ids": self.evidence_event_ids,
            "severity": self.severity,
            "category": self.category,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceFinding:
        return cls(
            finding_id=data["finding_id"],
            finding_type=data["finding_type"],
            title=data.get("title", ""),
            description=data.get("description", ""),
            evidence_event_ids=data.get("evidence_event_ids", []),
            severity=data.get("severity", "medium"),
            category=data.get("category", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class FailureMotif:
    """A recurring failure pattern grouped by event_type."""

    motif_id: str
    pattern_name: str
    occurrence_count: int
    evidence_event_ids: list[str]
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "motif_id": self.motif_id,
            "pattern_name": self.pattern_name,
            "occurrence_count": self.occurrence_count,
            "evidence_event_ids": self.evidence_event_ids,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> FailureMotif:
        return cls(
            motif_id=data["motif_id"],
            pattern_name=data["pattern_name"],
            occurrence_count=data.get("occurrence_count", 0),
            evidence_event_ids=data.get("evidence_event_ids", []),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class RecoveryPath:
    """A failure-to-recovery chain with intermediate events."""

    recovery_id: str
    failure_event_id: str
    recovery_event_id: str
    path_event_ids: list[str]
    description: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "recovery_id": self.recovery_id,
            "failure_event_id": self.failure_event_id,
            "recovery_event_id": self.recovery_event_id,
            "path_event_ids": self.path_event_ids,
            "description": self.description,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RecoveryPath:
        return cls(
            recovery_id=data["recovery_id"],
            failure_event_id=data["failure_event_id"],
            recovery_event_id=data["recovery_event_id"],
            path_event_ids=data.get("path_event_ids", []),
            description=data.get("description", ""),
            metadata=data.get("metadata", {}),
        )


@dataclass(slots=True)
class TraceWriteup:
    """Complete trace-grounded writeup."""

    writeup_id: str
    run_id: str
    generation_index: int | None
    findings: list[TraceFinding]
    failure_motifs: list[FailureMotif]
    recovery_paths: list[RecoveryPath]
    summary: str
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "writeup_id": self.writeup_id,
            "run_id": self.run_id,
            "generation_index": self.generation_index,
            "findings": [f.to_dict() for f in self.findings],
            "failure_motifs": [m.to_dict() for m in self.failure_motifs],
            "recovery_paths": [r.to_dict() for r in self.recovery_paths],
            "summary": self.summary,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceWriteup:
        return cls(
            writeup_id=data["writeup_id"],
            run_id=data["run_id"],
            generation_index=data.get("generation_index"),
            findings=[TraceFinding.from_dict(f) for f in data.get("findings", [])],
            failure_motifs=[FailureMotif.from_dict(m) for m in data.get("failure_motifs", [])],
            recovery_paths=[RecoveryPath.from_dict(r) for r in data.get("recovery_paths", [])],
            summary=data.get("summary", ""),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )

    def to_markdown(self) -> str:
        scenario = str(self.metadata.get("scenario", ""))
        family = str(self.metadata.get("scenario_family", ""))
        lines = [f"# Run Summary: {self.run_id}", ""]
        if scenario or family:
            context = " | ".join(part for part in [scenario, family] if part)
            lines.append(f"**Context:** {context}")
            lines.append("")

        lines.append("## Trace Summary")
        lines.append(self.summary)
        lines.append("")

        lines.append("## Findings")
        if self.findings:
            for finding in self.findings:
                evidence = ", ".join(finding.evidence_event_ids) or "none"
                lines.append(
                    f"- **{finding.title}** [{finding.finding_type}/{finding.severity}] "
                    f"{finding.description} (evidence: {evidence})"
                )
        else:
            lines.append("No notable findings.")
        lines.append("")

        lines.append("## Failure Motifs")
        if self.failure_motifs:
            for motif in self.failure_motifs:
                lines.append(
                    f"- **{motif.pattern_name}**: {motif.occurrence_count} occurrence(s)"
                )
        else:
            lines.append("No recurring failure motifs.")
        lines.append("")

        lines.append("## Recovery Paths")
        if self.recovery_paths:
            for recovery in self.recovery_paths:
                lines.append(
                    f"- {recovery.failure_event_id} -> {recovery.recovery_event_id} "
                    f"({len(recovery.path_event_ids)} events)"
                )
        else:
            lines.append("No recovery paths observed.")

        return "\n".join(lines)


@dataclass(slots=True)
class WeaknessReport:
    """Weakness-focused report with recommendations."""

    report_id: str
    run_id: str
    weaknesses: list[TraceFinding]
    failure_motifs: list[FailureMotif]
    recovery_analysis: str
    recommendations: list[str]
    created_at: str
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "report_id": self.report_id,
            "run_id": self.run_id,
            "weaknesses": [w.to_dict() for w in self.weaknesses],
            "failure_motifs": [m.to_dict() for m in self.failure_motifs],
            "recovery_analysis": self.recovery_analysis,
            "recommendations": self.recommendations,
            "created_at": self.created_at,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> WeaknessReport:
        return cls(
            report_id=data["report_id"],
            run_id=data["run_id"],
            weaknesses=[TraceFinding.from_dict(w) for w in data.get("weaknesses", [])],
            failure_motifs=[FailureMotif.from_dict(m) for m in data.get("failure_motifs", [])],
            recovery_analysis=data.get("recovery_analysis", ""),
            recommendations=data.get("recommendations", []),
            created_at=data.get("created_at", ""),
            metadata=data.get("metadata", {}),
        )

    def to_markdown(self) -> str:
        scenario = str(self.metadata.get("scenario", ""))
        lines = [
            f"# Weakness Report: {self.run_id}",
            f"**Scenario:** {scenario or 'unknown'}",
            "",
        ]
        if not self.weaknesses:
            lines.append("No weaknesses identified.")
        else:
            lines.append(f"**Summary:** {len(self.weaknesses)} weakness(es) detected")
            lines.append("")
            for weakness in self.weaknesses:
                evidence = ", ".join(weakness.evidence_event_ids) or "none"
                lines.append(f"## [{weakness.severity.upper()}] {weakness.title}")
                lines.append(weakness.description)
                lines.append(f"- Category: {weakness.category}")
                lines.append(f"- Evidence events: {evidence}")
                lines.append("")

        lines.append("## Recovery Analysis")
        lines.append(self.recovery_analysis or "No recovery analysis available.")
        lines.append("")
        lines.append("## Recommendations")
        if self.recommendations:
            for recommendation in self.recommendations:
                lines.append(f"- {recommendation}")
        else:
            lines.append("- No immediate recommendations.")
        return "\n".join(lines)


def _uid() -> str:
    return uuid.uuid4().hex[:8]


class TraceReporter:
    """Extracts findings and generates reports from run traces."""

    def extract_findings(self, trace: RunTrace) -> list[TraceFinding]:
        """Extract structured findings from trace events."""
        findings: list[TraceFinding] = []

        for event in trace.events:
            is_failure_like = event.category == "failure" or (
                event.category == "validation" and str(event.outcome) == "failed"
            )
            if is_failure_like:
                evidence = self._collect_evidence(trace, event)
                findings.append(TraceFinding(
                    finding_id=f"finding-{_uid()}",
                    finding_type="weakness",
                    title=f"{event.event_type} in {event.stage} stage",
                    description=event.summary,
                    evidence_event_ids=evidence,
                    severity=self._map_severity(event.severity),
                    category="failure_motif",
                ))
            elif event.category == "recovery":
                evidence = self._collect_evidence(trace, event)
                findings.append(TraceFinding(
                    finding_id=f"finding-{_uid()}",
                    finding_type="strength",
                    title=f"Recovery via {event.event_type} in {event.stage}",
                    description=event.summary,
                    evidence_event_ids=evidence,
                    severity="low",
                    category="recovery_path",
                ))

        return findings

    def extract_failure_motifs(self, trace: RunTrace) -> list[FailureMotif]:
        """Group failure events by event_type into motifs."""
        failure_events = [
            event for event in trace.events
            if event.category == "failure"
            or (event.category == "validation" and str(event.outcome) == "failed")
        ]
        if not failure_events:
            return []

        grouped: defaultdict[str, list[TraceEvent]] = defaultdict(list)
        for evt in failure_events:
            grouped[evt.event_type].append(evt)

        motifs: list[FailureMotif] = []
        for pattern_name, events in sorted(grouped.items()):
            motifs.append(FailureMotif(
                motif_id=f"motif-{_uid()}",
                pattern_name=pattern_name,
                occurrence_count=len(events),
                evidence_event_ids=[e.event_id for e in events],
                description=f"{pattern_name} occurred {len(events)} time(s)",
            ))

        return motifs

    def extract_recovery_paths(self, trace: RunTrace) -> list[RecoveryPath]:
        """Find failure→recovery chains in the trace."""
        recovery_events = [e for e in trace.events if e.category == "recovery"]
        if not recovery_events:
            return []

        event_map = {e.event_id: e for e in trace.events}
        paths: list[RecoveryPath] = []

        for recovery in recovery_events:
            # Walk causes to find the originating failure
            failure_id = self._find_cause_failure(trace, event_map, recovery)
            if failure_id is None:
                continue

            # Collect path events between failure and recovery
            path_ids = self._collect_path(event_map, failure_id, recovery.event_id)

            paths.append(RecoveryPath(
                recovery_id=f"recovery-{_uid()}",
                failure_event_id=failure_id,
                recovery_event_id=recovery.event_id,
                path_event_ids=path_ids,
                description=f"Recovery from {failure_id} to {recovery.event_id}",
            ))

        return paths

    def generate_writeup(self, trace: RunTrace) -> TraceWriteup:
        """Generate a complete trace-grounded writeup."""
        now = datetime.now(UTC).isoformat()
        findings = self.extract_findings(trace)
        motifs = self.extract_failure_motifs(trace)
        recovery_paths = self.extract_recovery_paths(trace)

        summary = self._compose_writeup_summary(trace, findings, motifs, recovery_paths)

        return TraceWriteup(
            writeup_id=f"writeup-{_uid()}",
            run_id=trace.run_id,
            generation_index=trace.generation_index,
            findings=findings,
            failure_motifs=motifs,
            recovery_paths=recovery_paths,
            summary=summary,
            created_at=now,
            metadata={**dict(trace.metadata), "report_source": "trace_grounded"},
        )

    def generate_weakness_report(self, trace: RunTrace) -> WeaknessReport:
        """Generate a weakness-focused report with recommendations."""
        now = datetime.now(UTC).isoformat()
        findings = self.extract_findings(trace)
        weaknesses = [f for f in findings if f.finding_type == "weakness"]
        motifs = self.extract_failure_motifs(trace)
        recovery_paths = self.extract_recovery_paths(trace)

        recovery_analysis = self._compose_recovery_analysis(recovery_paths)
        recommendations = self._compose_recommendations(weaknesses, motifs)

        return WeaknessReport(
            report_id=f"weakness-{_uid()}",
            run_id=trace.run_id,
            weaknesses=weaknesses,
            failure_motifs=motifs,
            recovery_analysis=recovery_analysis,
            recommendations=recommendations,
            created_at=now,
            metadata={**dict(trace.metadata), "report_source": "trace_grounded"},
        )

    # --- private helpers ---

    def _collect_evidence(self, trace: RunTrace, event: TraceEvent) -> list[str]:
        """Collect evidence event IDs from explicit evidence, causal ancestry, and the event itself."""
        event_map = {evt.event_id: evt for evt in trace.events}
        parent_map = self._parent_map(trace)
        evidence_ids = set(event.evidence_ids)
        evidence_ids.update(self._ancestor_ids(parent_map, event.event_id))
        evidence_ids.add(event.event_id)
        return [
            evt.event_id
            for evt in sorted(
                (event_map[eid] for eid in evidence_ids if eid in event_map),
                key=lambda evt: evt.sequence_number,
            )
        ]

    def _map_severity(self, event_severity: str) -> str:
        mapping = {"critical": "critical", "error": "high", "warning": "medium", "info": "low"}
        return mapping.get(event_severity, "medium")

    def _find_cause_failure(
        self,
        trace: RunTrace,
        event_map: dict[str, TraceEvent],
        recovery: TraceEvent,
    ) -> str | None:
        """Walk causes of a recovery to find the originating failure."""
        visited: set[str] = set()
        parent_map = self._parent_map(trace)
        queue = list(parent_map.get(recovery.event_id, []))
        while queue:
            eid = queue.pop(0)
            if eid in visited:
                continue
            visited.add(eid)
            evt = event_map.get(eid)
            if evt is None:
                continue
            if evt.category == "failure":
                return evt.event_id
            queue.extend(parent_map.get(eid, []))
        return None

    def _collect_path(
        self,
        event_map: dict[str, TraceEvent],
        failure_id: str,
        recovery_id: str,
    ) -> list[str]:
        """Collect ordered event IDs from failure through recovery."""
        failure = event_map.get(failure_id)
        recovery = event_map.get(recovery_id)
        if failure is None or recovery is None:
            return []

        fail_seq = failure.sequence_number
        recov_seq = recovery.sequence_number

        path_events = [
            e for e in event_map.values()
            if fail_seq <= e.sequence_number <= recov_seq
        ]
        path_events.sort(key=lambda e: e.sequence_number)
        return [e.event_id for e in path_events]

    def _compose_writeup_summary(
        self,
        trace: RunTrace,
        findings: list[TraceFinding],
        motifs: list[FailureMotif],
        recovery_paths: list[RecoveryPath],
    ) -> str:
        parts: list[str] = []
        weakness_count = sum(1 for f in findings if f.finding_type == "weakness")
        strength_count = sum(1 for f in findings if f.finding_type == "strength")

        parts.append(f"Run {trace.run_id}: {len(trace.events)} events, "
                      f"{len(findings)} findings.")

        if weakness_count:
            parts.append(f"{weakness_count} weakness(es) identified.")
        if strength_count:
            parts.append(f"{strength_count} strength(s) identified.")
        if motifs:
            motif_names = ", ".join(m.pattern_name for m in motifs)
            parts.append(f"Failure motifs: {motif_names}.")
        if recovery_paths:
            parts.append(f"{len(recovery_paths)} recovery path(s) found.")
        if not findings:
            parts.append("Clean run with no notable findings.")

        return " ".join(parts)

    def _compose_recovery_analysis(self, recovery_paths: list[RecoveryPath]) -> str:
        if not recovery_paths:
            return "No recoveries observed."

        lines: list[str] = [f"{len(recovery_paths)} recovery path(s) observed:"]
        for rp in recovery_paths:
            lines.append(f"  - {rp.failure_event_id} -> {rp.recovery_event_id} "
                         f"({len(rp.path_event_ids)} events)")
        return "\n".join(lines)

    def _compose_recommendations(
        self,
        weaknesses: list[TraceFinding],
        motifs: list[FailureMotif],
    ) -> list[str]:
        if not weaknesses:
            return []

        recs: list[str] = []

        # Count failure types
        type_counts: Counter[str] = Counter()
        for w in weaknesses:
            type_counts[w.title] += 1

        for title, count in type_counts.most_common():
            if count > 1:
                recs.append(f"Investigate recurring: {title} ({count} occurrences)")
            else:
                recs.append(f"Review: {title}")

        # Motif-specific recommendations
        for motif in motifs:
            if motif.occurrence_count >= 2:
                recs.append(f"Address systemic pattern: {motif.pattern_name} "
                            f"({motif.occurrence_count} occurrences)")

        return recs

    def _parent_map(self, trace: RunTrace) -> dict[str, list[str]]:
        """Build a canonical parent map from explicit edges with inline fallbacks."""
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

    def _ancestor_ids(self, parent_map: dict[str, list[str]], event_id: str) -> set[str]:
        """Return all causal ancestors for an event."""
        visited: set[str] = set()
        queue = list(parent_map.get(event_id, []))
        while queue:
            candidate = queue.pop(0)
            if candidate in visited:
                continue
            visited.add(candidate)
            queue.extend(parent_map.get(candidate, []))
        return visited


class ReportStore:
    """Persists writeups and weakness reports as JSON files."""

    def __init__(self, root: Path) -> None:
        self._writeups_dir = root / "writeups"
        self._weakness_dir = root / "weakness_reports"
        self._writeups_dir.mkdir(parents=True, exist_ok=True)
        self._weakness_dir.mkdir(parents=True, exist_ok=True)

    def persist_writeup(self, writeup: TraceWriteup) -> Path:
        path = self._writeups_dir / f"{writeup.writeup_id}.json"
        write_json(path, writeup.to_dict())
        return path

    def load_writeup(self, writeup_id: str) -> TraceWriteup | None:
        path = self._writeups_dir / f"{writeup_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return TraceWriteup.from_dict(data)

    def list_writeups(self) -> list[TraceWriteup]:
        results: list[TraceWriteup] = []
        for path in sorted(self._writeups_dir.glob("*.json")):
            data = read_json(path)
            results.append(TraceWriteup.from_dict(data))
        return results

    def latest_writeup_for_run(self, run_id: str) -> TraceWriteup | None:
        writeups = [writeup for writeup in self.list_writeups() if writeup.run_id == run_id]
        if not writeups:
            return None
        return max(writeups, key=lambda writeup: writeup.created_at)

    def persist_weakness_report(self, report: WeaknessReport) -> Path:
        path = self._weakness_dir / f"{report.report_id}.json"
        write_json(path, report.to_dict())
        return path

    def load_weakness_report(self, report_id: str) -> WeaknessReport | None:
        path = self._weakness_dir / f"{report_id}.json"
        if not path.exists():
            return None
        data = read_json(path)
        return WeaknessReport.from_dict(data)

    def list_weakness_reports(self) -> list[WeaknessReport]:
        results: list[WeaknessReport] = []
        for path in sorted(self._weakness_dir.glob("*.json")):
            data = read_json(path)
            results.append(WeaknessReport.from_dict(data))
        return results

    def latest_weakness_report_for_run(self, run_id: str) -> WeaknessReport | None:
        reports = [report for report in self.list_weakness_reports() if report.run_id == run_id]
        if not reports:
            return None
        return max(reports, key=lambda report: report.created_at)
