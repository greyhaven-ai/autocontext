from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Literal, TypeAlias

from autocontext.analytics.cross_runtime_trace_findings import (
    CrossRuntimeFailureMotif,
    CrossRuntimeTraceFinding,
    CrossRuntimeTraceFindingReport,
)

TraceGateOperatorState: TypeAlias = Literal[
    "missing_report",
    "incomplete_analysis",
    "no_findings",
    "ready",
]
TraceGateAnalysisState: TypeAlias = Literal["complete", "incomplete"]
TraceEvidenceLink: TypeAlias = dict[str, str]
TraceGateOperatorView: TypeAlias = dict[str, Any]


def build_trace_gate_operator_view(
    *,
    run_id: str,
    report: CrossRuntimeTraceFindingReport | Mapping[str, Any] | None,
    proposals: Sequence[Mapping[str, Any]] | None = None,
    analysis_state: TraceGateAnalysisState = "complete",
) -> TraceGateOperatorView:
    parsed_report = _parse_report(report)
    parsed_proposals = list(proposals or [])
    state = _resolve_state(parsed_report, parsed_proposals, analysis_state)
    linked_proposal_ids = _proposal_ids_by_finding(parsed_proposals)
    return {
        "schema_version": "1",
        "run_id": run_id,
        "state": state,
        "summary": _summary_for_state(state, parsed_report),
        "report": _summarize_report(parsed_report) if parsed_report is not None else None,
        "findings": [
            _finding_view(finding, linked_proposal_ids)
            for finding in (parsed_report.findings if parsed_report is not None else [])
        ],
        "failure_modes": [
            _failure_mode_view(motif) for motif in (parsed_report.failure_motifs if parsed_report is not None else [])
        ],
        "proposals": [_proposal_view(proposal) for proposal in parsed_proposals],
        "gate_decisions": [decision for proposal in parsed_proposals for decision in _decision_views(proposal)],
    }


def render_trace_gate_operator_view_lines(view: Mapping[str, Any]) -> list[str]:
    lines = [f"Trace gates for {view['run_id']} — {view['state']}", _read_str(view.get("summary"))]
    findings = _read_list(view.get("findings"))
    lines.append("Findings:")
    if findings:
        for finding in findings:
            evidence = _format_evidence_labels(_read_list(finding.get("evidence_refs")))
            proposal_text = _format_list(_read_str_list(finding.get("linked_proposal_ids")))
            lines.append(
                f"- [{finding['severity']}/{finding['category']}] {finding['title']} "
                f"evidence={evidence} proposals={proposal_text}"
            )
    else:
        lines.append("- none")

    modes = _read_list(view.get("failure_modes"))
    lines.append("Recurring failure modes:")
    if modes:
        for mode in modes:
            evidence = _format_evidence_labels(_read_list(mode.get("representative_evidence_refs")))
            lines.append(f"- {mode['category']} x{mode['occurrence_count']} evidence={evidence}")
    else:
        lines.append("- none")

    proposal_views = _read_list(view.get("proposals"))
    lines.append("Proposals:")
    if proposal_views:
        for proposal in proposal_views:
            lines.append(
                f"- {proposal['proposal_id']} {proposal['status']} target={proposal['target_surface']} "
                f"patches={proposal['patch_count']} — {proposal['summary']}"
            )
    else:
        lines.append("- none")

    decisions = _read_list(view.get("gate_decisions"))
    lines.append("Gate decisions:")
    if decisions:
        for decision in decisions:
            validation_mode = _read_str(decision.get("validation_mode")) or "unknown"
            lines.append(f"- {decision['proposal_id']} {decision['status']} {validation_mode} — {decision['reason']}")
    else:
        lines.append("- none")
    return lines


def _parse_report(
    report: CrossRuntimeTraceFindingReport | Mapping[str, Any] | None,
) -> CrossRuntimeTraceFindingReport | None:
    if report is None:
        return None
    if isinstance(report, CrossRuntimeTraceFindingReport):
        return report
    return CrossRuntimeTraceFindingReport.model_validate(report)


def _resolve_state(
    report: CrossRuntimeTraceFindingReport | None,
    proposals: Sequence[Mapping[str, Any]],
    analysis_state: TraceGateAnalysisState,
) -> TraceGateOperatorState:
    if analysis_state == "incomplete":
        return "incomplete_analysis"
    if report is None:
        return "missing_report"
    if not report.findings and not proposals:
        return "no_findings"
    return "ready"


def _summary_for_state(
    state: TraceGateOperatorState,
    report: CrossRuntimeTraceFindingReport | None,
) -> str:
    if state == "incomplete_analysis":
        return "Trace analysis is still running; findings and gate decisions may be incomplete."
    if state == "missing_report":
        return "No trace finding report is available for this run yet."
    if state == "no_findings":
        return report.summary if report is not None else "No findings or proposals are available for this run."
    return report.summary if report is not None else "Trace findings and gate decisions are ready."


def _summarize_report(report: CrossRuntimeTraceFindingReport) -> dict[str, str | int]:
    return {
        "report_id": report.report_id,
        "trace_id": report.trace_id,
        "source_harness": report.source_harness,
        "created_at": report.created_at,
        "finding_count": len(report.findings),
        "failure_mode_count": len(report.failure_motifs),
    }


def _finding_view(
    finding: CrossRuntimeTraceFinding,
    linked_proposal_ids: Mapping[str, list[str]],
) -> dict[str, Any]:
    evidence_refs = [_trace_message_evidence_link(index) for index in finding.evidence_message_indexes]
    return {
        "finding_id": finding.finding_id,
        "category": finding.category,
        "severity": finding.severity,
        "title": finding.title,
        "description": finding.description,
        "evidence_count": len(evidence_refs),
        "evidence_refs": evidence_refs,
        "linked_proposal_ids": linked_proposal_ids.get(finding.finding_id, []),
    }


def _failure_mode_view(motif: CrossRuntimeFailureMotif) -> dict[str, Any]:
    return {
        "motif_id": motif.motif_id,
        "category": motif.category,
        "occurrence_count": motif.occurrence_count,
        "description": motif.description,
        "representative_evidence_refs": [_trace_message_evidence_link(index) for index in motif.evidence_message_indexes],
    }


def _proposal_view(proposal: Mapping[str, Any]) -> dict[str, Any]:
    proposed_edit = _read_mapping(_read_key(proposal, "proposedEdit", "proposed_edit"))
    decision = _read_mapping(proposal.get("decision"))
    return {
        "proposal_id": _read_str(proposal.get("id")),
        "status": _read_str(proposal.get("status")),
        "target_surface": _read_str(_read_key(proposal, "targetSurface", "target_surface")),
        "summary": _read_str(proposed_edit.get("summary")),
        "finding_ids": _read_str_list(_read_key(proposal, "findingIds", "finding_ids")),
        "patch_count": len(_read_list(proposed_edit.get("patches"))),
        "rollback_criteria": _read_str_list(_read_key(proposal, "rollbackCriteria", "rollback_criteria")),
        "gate_status": _read_str(decision.get("status")),
        "gate_reason": _read_str(decision.get("reason")),
    }


def _decision_views(proposal: Mapping[str, Any]) -> list[dict[str, Any]]:
    decision = _read_mapping(proposal.get("decision"))
    if not decision:
        return []
    validation = _read_mapping(decision.get("validation"))
    evidence_refs = [
        _artifact_evidence_link(ref) for ref in _read_str_list(_read_key(validation, "evidenceRefs", "evidence_refs"))
    ]
    return [
        {
            "proposal_id": _read_str(proposal.get("id")),
            "status": _read_str(decision.get("status")),
            "reason": _read_str(decision.get("reason")),
            "validation_mode": _read_str(validation.get("mode")),
            "suite_id": _read_str(_read_key(validation, "suiteId", "suite_id")),
            "decided_at": _read_str(_read_key(decision, "decidedAt", "decided_at")),
            "evidence_refs": evidence_refs,
        }
    ]


def _proposal_ids_by_finding(proposals: Sequence[Mapping[str, Any]]) -> dict[str, list[str]]:
    ids: dict[str, list[str]] = {}
    for proposal in proposals:
        proposal_id = _read_str(proposal.get("id"))
        for finding_id in _read_str_list(_read_key(proposal, "findingIds", "finding_ids")):
            ids.setdefault(finding_id, []).append(proposal_id)
    return ids


def _trace_message_evidence_link(index: int) -> TraceEvidenceLink:
    return {
        "kind": "trace_message",
        "ref": f"msg:{index}",
        "label": f"msg #{index}",
        "href": f"#msg-{index}",
    }


def _artifact_evidence_link(ref: str) -> TraceEvidenceLink:
    return {
        "kind": "artifact",
        "ref": ref,
        "label": _artifact_label(ref),
        "href": ref,
    }


def _artifact_label(ref: str) -> str:
    return next((part for part in reversed(ref.replace("\\", "/").split("/")) if part), ref)


def _format_evidence_labels(refs: Sequence[Mapping[str, Any]]) -> str:
    labels = [_read_str(ref.get("label")) for ref in refs if _read_str(ref.get("label"))]
    return ", ".join(labels) if labels else "none"


def _format_list(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "none"


def _read_key(mapping: Mapping[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping[key]
    return None


def _read_mapping(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _read_list(value: Any) -> list[Mapping[str, Any]]:
    return [item for item in value if isinstance(item, Mapping)] if isinstance(value, list) else []


def _read_str_list(value: Any) -> list[str]:
    return [item for item in value if isinstance(item, str)] if isinstance(value, list) else []


def _read_str(value: Any) -> str:
    return value if isinstance(value, str) else ""
