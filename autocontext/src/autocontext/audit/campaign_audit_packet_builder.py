"""Bounded packet construction for completed campaign-mode reports."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from autocontext.audit.campaign_audit_boundary import (
    BOUNDARY_PROVENANCE,
    _seal_evidence_boundary,
    redacted_fields,
    redacted_identity,
    sanitized_artifact_uri,
)
from autocontext.sharing.redactor import redact_content

if TYPE_CHECKING:
    from autocontext.analytics.campaign_mode_report import CampaignModeReport
    from autocontext.audit.campaign_auditor import (
        AuditMetricSummary,
        CampaignAuditEvidencePacket,
    )


def build_campaign_audit_packet(
    report: CampaignModeReport,
    *,
    checkpoint: Any,
    bundle_lineage: list[dict[str, Any]],
    metric_summaries: list[dict[str, Any]],
    gate_decisions: list[dict[str, Any]],
    negative_results: list[dict[str, Any]],
    artifact_refs: list[dict[str, Any]],
    hidden_holdout_answers_included: bool,
    integrity_alerts: list[str] | None = None,
    max_items_per_section: int = 100,
) -> CampaignAuditEvidencePacket:
    """Build a whitelisted packet that omits holdout answers and credential-bearing fields."""

    from autocontext.audit.campaign_auditor import (
        AuditBundleLineage,
        AuditProtocolLane,
        CampaignAuditEvidencePacket,
    )

    if max_items_per_section < 1:
        raise ValueError("max_items_per_section must be positive")
    lanes = [
        AuditProtocolLane(
            lane_id=redact_content(lane.lane_id),
            verifier_contract_ref=redact_content(lane.verifier_contract_ref),
            seed_count=len(lane.seeds),
        )
        for lane in report.eval_lanes[:max_items_per_section]
    ]
    all_metrics = [_safe_metric_summary(item) for item in metric_summaries]
    bounded_metrics = _bounded_metric_summaries(all_metrics, max_items_per_section)
    packet_alerts = [_classify_integrity_alert(item) for item in (integrity_alerts or [])[:max_items_per_section]]
    if any(
        len(section) > max_items_per_section
        for section in (
            report.eval_lanes,
            bundle_lineage,
            metric_summaries,
            gate_decisions,
            negative_results,
            artifact_refs,
            integrity_alerts or [],
        )
    ):
        packet_alerts.append("evidence_truncated")
    if len({metric.evaluator_epoch for metric in all_metrics}) > 1:
        packet_alerts.append("evaluator_epoch_mismatch")
    if len({metric.cohort for metric in all_metrics}) > 1:
        packet_alerts.append("non_comparable_cohorts")
    packet = CampaignAuditEvidencePacket(
        hidden_holdout_answers_included=hidden_holdout_answers_included,
        boundary_provenance=BOUNDARY_PROVENANCE,
        boundary_digest="pending",
        campaign_id=redacted_identity(report.campaign_id, "campaign"),
        run_id=redacted_identity(report.run_id, "run"),
        scenario_name=redacted_identity(report.scenario_name, "scenario"),
        checkpoint=checkpoint,
        terminal_state=report.terminal_state,
        protocol_lanes=lanes,
        bundle_lineage=[
            AuditBundleLineage.from_dict(redacted_fields(item, AuditBundleLineage))
            for item in bundle_lineage[:max_items_per_section]
        ],
        metric_summaries=bounded_metrics,
        gate_decisions=[_safe_gate_decision(item) for item in gate_decisions[:max_items_per_section]],
        negative_results=[_safe_negative_result(item) for item in negative_results[:max_items_per_section]],
        integrity_alerts=sorted(set(packet_alerts)),
        artifact_refs=[_safe_evidence_reference(item) for item in artifact_refs[:max_items_per_section]],
    )
    return packet.model_copy(update={"boundary_digest": _seal_evidence_boundary(packet.to_dict())})


def _safe_metric_summary(data: dict[str, Any]) -> AuditMetricSummary:
    from autocontext.audit.campaign_auditor import AuditMetricSummary

    fields = redacted_fields(data, AuditMetricSummary)
    if isinstance(data.get("reconstruction_ref"), str):
        fields["reconstruction_ref"] = sanitized_artifact_uri(data["reconstruction_ref"])
    return AuditMetricSummary.from_dict(fields)


def _safe_gate_decision(data: dict[str, Any]) -> Any:
    from autocontext.audit.campaign_auditor import AuditGateDecision

    fields = redacted_fields(data, AuditGateDecision)
    if isinstance(data.get("artifact_ref"), str):
        fields["artifact_ref"] = sanitized_artifact_uri(data["artifact_ref"])
    decision = str(fields.get("decision", ""))
    claim = str(fields.get("claim", ""))
    if "caus" in claim.lower():
        fields["claim"] = "Causal claim text withheld; categorical claim retained."
    elif decision in {"accepted", "promote", "kept"}:
        fields["claim"] = "Promotion claim text withheld; categorical decision retained."
    else:
        fields["claim"] = "Free-text claim withheld from auditor packet."
    return AuditGateDecision.from_dict(fields)


def _safe_negative_result(data: dict[str, Any]) -> Any:
    from autocontext.audit.campaign_auditor import AuditNegativeResult

    fields = redacted_fields(data, AuditNegativeResult)
    fields["reason"] = "Free-text negative-result reason withheld from auditor packet."
    references = data.get("evidence_refs")
    if isinstance(references, list):
        fields["evidence_refs"] = [
            sanitized_artifact_uri(reference)
            for reference in references
            if isinstance(reference, str) and reference
        ]
    return AuditNegativeResult.from_dict(fields)


def _safe_evidence_reference(data: dict[str, Any]) -> Any:
    from autocontext.audit.campaign_auditor import AuditEvidenceReference

    fields = redacted_fields(data, AuditEvidenceReference)
    if isinstance(data.get("uri"), str):
        fields["uri"] = sanitized_artifact_uri(data["uri"])
    fields["summary"] = "Artifact contents and free-text summary excluded; metadata reference only."
    return AuditEvidenceReference.from_dict(fields)


def _classify_integrity_alert(value: str) -> str:
    lowered = value.lower()
    if "evidence_truncated" in lowered:
        return "evidence_truncated"
    if "evaluator_epoch_mismatch" in lowered:
        return "evaluator_epoch_mismatch"
    if "non_comparable_cohorts" in lowered:
        return "non_comparable_cohorts"
    if "cleanup" in lowered:
        return "cleanup_failure"
    if "leakage" in lowered or "holdout answer" in lowered or "answer key" in lowered:
        return "data_leakage"
    if "infrastructure" in lowered:
        return "infrastructure_integrity_alert"
    return "integrity_alert"


def _bounded_metric_summaries(
    metrics: list[AuditMetricSummary],
    limit: int,
) -> list[AuditMetricSummary]:
    """Bound metrics while retaining representatives of integrity-relevant strata."""

    if len(metrics) <= limit:
        return metrics
    representative_indices: list[int] = []
    signatures: set[tuple[object, ...]] = set()
    for index, metric in enumerate(metrics):
        signature = (
            metric.evaluator_epoch,
            metric.cohort,
            metric.classification,
            metric.infrastructure_error,
            metric.cleanup_succeeded,
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        representative_indices.append(index)
    chosen = set(representative_indices[:limit])
    for index in range(len(metrics)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [metrics[index] for index in sorted(chosen)]


__all__ = ["build_campaign_audit_packet"]
