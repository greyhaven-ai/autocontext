"""Sealed packet factories for live scheduler and context-bundle checkpoints."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal, TypeAlias

from autocontext.audit.campaign_audit_boundary import (
    BOUNDARY_PROVENANCE,
    _seal_evidence_boundary,
    redacted_identity,
    sanitized_artifact_uri,
    validate_evidence_boundary,
)
from autocontext.audit.campaign_auditor import (
    AuditBundleLineage,
    AuditCheckpoint,
    AuditEvidenceReference,
    AuditGateDecision,
    AuditMetricSummary,
    AuditProtocolLane,
    CampaignAuditEvidencePacket,
)
from autocontext.context_bundles.models import stable_digest
from autocontext.sharing.redactor import redact_content

MetricClassification: TypeAlias = Literal[
    "candidate_result",
    "candidate_failure",
    "infrastructure_failure",
    "canceled",
    "budget_exhausted",
]


@dataclass(frozen=True, slots=True)
class CampaignAuditPacketIdentity:
    campaign_id: str
    run_id: str
    scenario_name: str
    artifact_uri: str
    artifact_digest: str | None = None
    evaluator_epoch: str = "unavailable"
    verifier_contract_ref: str = "unavailable"

    def __post_init__(self) -> None:
        required = (self.campaign_id, self.run_id, self.scenario_name, self.artifact_uri)
        if not all(value.strip() for value in required):
            raise ValueError("campaign audit packet identity fields must be non-empty")


class CampaignCheckpointPacketFactory:
    """Convert known live-checkpoint shapes into sealed, metadata-only packets."""

    def __init__(self, identity: CampaignAuditPacketIdentity, *, max_items_per_section: int = 100) -> None:
        if max_items_per_section < 1:
            raise ValueError("max_items_per_section must be positive")
        self.identity = identity
        self.max_items_per_section = max_items_per_section

    def context_pre_promotion(
        self,
        candidate: Any,
        comparison: Any,
        trials: tuple[Any, ...],
    ) -> CampaignAuditEvidencePacket:
        """Adapter matching ``ContextBundlePrePromotionAuditor`` packet_factory."""

        candidate_data = candidate.to_dict()
        comparison_data = comparison.to_dict()
        trial_data = [trial.to_dict() for trial in trials]
        cohort = trial_data[0].get("cohort", "unavailable") if trial_data else "unavailable"
        return self(
            "pre_promotion",
            {"candidate": candidate_data, "comparison": comparison_data, "trials": trial_data, "cohort": cohort},
        )

    def __call__(
        self,
        checkpoint: AuditCheckpoint,
        evidence: Mapping[str, Any],
    ) -> CampaignAuditEvidencePacket:
        evidence_campaign = evidence.get("campaign_id")
        if isinstance(evidence_campaign, str) and evidence_campaign != self.identity.campaign_id:
            raise ValueError("checkpoint evidence belongs to a different campaign")
        if isinstance(evidence.get("candidate"), Mapping) or "trials" in evidence:
            packet = self._context_packet(checkpoint, evidence)
        else:
            packet = self._scheduler_packet(checkpoint, evidence)
        validate_evidence_boundary(packet)
        return packet

    def _context_packet(
        self,
        checkpoint: AuditCheckpoint,
        evidence: Mapping[str, Any],
    ) -> CampaignAuditEvidencePacket:
        candidate = _mapping(evidence.get("candidate"))
        all_trials = tuple(_mapping(item) for item in _sequence(evidence.get("trials")))
        trials = _bounded_trial_mappings(all_trials, self.max_items_per_section)
        comparison = _mapping(evidence.get("comparison"))
        candidate_digest = _text(candidate.get("digest"), "unavailable")
        epoch = _text(candidate.get("evaluator_epoch"), self.identity.evaluator_epoch)
        cohort = _text(evidence.get("cohort"), "unavailable")
        metrics = [
            AuditMetricSummary(
                trial_id=_text(trial.get("pair_key"), stable_digest({"trial": index, "fixture": trial.get("fixture_digest")})),
                branch_id=candidate_digest,
                candidate_digest=_text(trial.get("candidate_digest"), candidate_digest),
                cohort=_text(trial.get("cohort"), cohort),
                evaluator_epoch=_text(trial.get("evaluator_epoch"), epoch),
                verifier_digest=stable_digest({"contract": self.identity.verifier_contract_ref}),
                fixture_digest=_text(trial.get("fixture_digest"), "unavailable"),
                score=_finite_score(trial.get("candidate_score")),
                valid=trial.get("candidate_valid") is True,
                classification="candidate_result" if trial.get("candidate_valid") is True else "candidate_failure",
                infrastructure_error=False,
                reconstruction_ref=sanitized_artifact_uri(self.identity.artifact_uri),
                experiment_id=None,
            )
            for index, trial in enumerate(trials)
        ]
        lane_counts: dict[str, int] = {}
        for trial in trials:
            lane = _text(trial.get("lane"), "matched")
            lane_counts[lane] = lane_counts.get(lane, 0) + 1
        decision = _text(comparison.get("decision"), checkpoint)
        return _sealed_packet(
            self.identity,
            checkpoint=checkpoint,
            terminal_state=decision,
            protocol_lanes=[
                AuditProtocolLane(
                    lane_id=lane,
                    verifier_contract_ref=redact_content(self.identity.verifier_contract_ref),
                    seed_count=count,
                )
                for lane, count in sorted(lane_counts.items())
            ],
            bundle_lineage=[
                AuditBundleLineage(
                    bundle_digest=candidate_digest,
                    parent_digest=_optional_text(candidate.get("parent_digest")),
                    evaluator_epoch=epoch,
                )
            ],
            metric_summaries=_bounded_audit_metrics(metrics, self.max_items_per_section),
            gate_decisions=[
                AuditGateDecision(
                    gate_id=stable_digest({"candidate": candidate_digest, "checkpoint": checkpoint}),
                    decision=decision,
                    claim="Categorical context-bundle comparison decision.",
                    evidence_level="paired_shadow",
                    evaluator_epoch=epoch,
                    cohort=cohort,
                    artifact_ref=sanitized_artifact_uri(self.identity.artifact_uri),
                )
            ],
            integrity_alerts=_truncation_and_comparability_alerts(all_trials, self.max_items_per_section),
        )

    def _scheduler_packet(
        self,
        checkpoint: AuditCheckpoint,
        evidence: Mapping[str, Any],
    ) -> CampaignAuditEvidencePacket:
        raw_jobs = tuple(_mapping(item) for item in _sequence(evidence.get("jobs")))
        all_jobs = raw_jobs or (evidence,)
        jobs = _bounded_scheduler_mappings(all_jobs, self.max_items_per_section)
        metrics: list[AuditMetricSummary] = []
        alerts = _scheduler_integrity_alerts(all_jobs, self.max_items_per_section)
        lane_counts: dict[tuple[str, str], int] = {}
        for index, job in enumerate(jobs):
            result = _mapping(job.get("scored_result") or job.get("accounting_result") or job.get("result"))
            status = _text(job.get("status"), _text(evidence.get("status"), "unknown"))
            outcome = _text(result.get("outcome"), "")
            classification, infrastructure_error = _scheduler_classification(status, outcome)
            if infrastructure_error:
                alerts.append("infrastructure_integrity_alert")
            job_id = _text(job.get("job_id"), _text(evidence.get("job_id"), f"job-{index}"))
            lane_id = _text(job.get("lane_id"), _text(evidence.get("lane_id"), "scheduler"))
            verifier_contract = _text(
                job.get("verifier_contract_ref"),
                _text(evidence.get("verifier_contract_ref"), self.identity.verifier_contract_ref),
            )
            seeds = _sequence(job.get("seeds"))
            lane_key = (lane_id, verifier_contract)
            lane_counts[lane_key] = lane_counts.get(lane_key, 0) + max(1, len(seeds))
            metrics.append(
                _scheduler_metric(
                    self.identity,
                    evidence,
                    job,
                    result,
                    job_id=job_id,
                    trial_id=job_id,
                    status=status,
                    classification=classification,
                    infrastructure_error=infrastructure_error,
                )
            )
            late_result = _mapping(job.get("unscored_late_result"))
            if late_result:
                late_outcome = _text(late_result.get("outcome"), "")
                late_classification, late_infrastructure = _scheduler_classification("stale", late_outcome)
                metrics.append(
                    _scheduler_metric(
                        self.identity,
                        evidence,
                        job,
                        late_result,
                        job_id=job_id,
                        trial_id=f"{job_id}:late",
                        status="stale",
                        classification=late_classification,
                        infrastructure_error=late_infrastructure,
                        force_invalid=True,
                    )
                )
        terminal_state = _text(evidence.get("status"), checkpoint)
        return _sealed_packet(
            self.identity,
            checkpoint=checkpoint,
            terminal_state=terminal_state,
            protocol_lanes=[
                AuditProtocolLane(
                    lane_id=lane_id,
                    verifier_contract_ref=redact_content(verifier_contract),
                    seed_count=seed_count,
                )
                for (lane_id, verifier_contract), seed_count in sorted(lane_counts.items())
            ],
            bundle_lineage=[],
            metric_summaries=_bounded_audit_metrics(metrics, self.max_items_per_section),
            gate_decisions=[],
            integrity_alerts=sorted(set(alerts)),
        )


def _sealed_packet(
    identity: CampaignAuditPacketIdentity,
    *,
    checkpoint: AuditCheckpoint,
    terminal_state: str,
    protocol_lanes: list[AuditProtocolLane],
    bundle_lineage: list[AuditBundleLineage],
    metric_summaries: list[AuditMetricSummary],
    gate_decisions: list[AuditGateDecision],
    integrity_alerts: list[str],
) -> CampaignAuditEvidencePacket:
    packet = CampaignAuditEvidencePacket(
        hidden_holdout_answers_included=False,
        boundary_provenance=BOUNDARY_PROVENANCE,
        boundary_digest="pending",
        campaign_id=redacted_identity(identity.campaign_id, "campaign"),
        run_id=redacted_identity(identity.run_id, "run"),
        scenario_name=redacted_identity(identity.scenario_name, "scenario"),
        checkpoint=checkpoint,
        terminal_state=redact_content(terminal_state),
        protocol_lanes=protocol_lanes,
        bundle_lineage=bundle_lineage,
        metric_summaries=metric_summaries,
        gate_decisions=gate_decisions,
        negative_results=[],
        integrity_alerts=integrity_alerts,
        artifact_refs=[
            AuditEvidenceReference(
                uri=sanitized_artifact_uri(identity.artifact_uri),
                digest=redact_content(identity.artifact_digest) if identity.artifact_digest else None,
                summary="Artifact contents excluded; durable metadata reference only.",
            )
        ],
    )
    return packet.model_copy(update={"boundary_digest": _seal_evidence_boundary(packet.to_dict())})


def _scheduler_metric(
    identity: CampaignAuditPacketIdentity,
    evidence: Mapping[str, Any],
    job: Mapping[str, Any],
    result: Mapping[str, Any],
    *,
    job_id: str,
    trial_id: str,
    status: str,
    classification: MetricClassification,
    infrastructure_error: bool,
    force_invalid: bool = False,
) -> AuditMetricSummary:
    consumed = _mapping(result.get("consumed"))
    metadata = _mapping(result.get("metadata"))
    candidate_digest = _text(
        job.get("candidate_digest"),
        stable_digest({"campaign": identity.campaign_id, "job": job_id}),
    )
    reconstruction_ref = _optional_text(result.get("output_ref"))
    if reconstruction_ref:
        reconstruction_ref = sanitized_artifact_uri(reconstruction_ref)
    else:
        reconstruction_ref = sanitized_artifact_uri(identity.artifact_uri)
    return AuditMetricSummary(
        trial_id=redacted_identity(trial_id, "trial"),
        branch_id=redacted_identity(
            _text(job.get("branch_id"), _text(evidence.get("branch_id"), "unavailable")),
            "branch",
        ),
        candidate_digest=redacted_identity(candidate_digest, "candidate"),
        cohort=redacted_identity(
            _text(job.get("cohort_id"), _text(evidence.get("cohort_id"), "unavailable")),
            "cohort",
        ),
        evaluator_epoch=_text(
            job.get("evaluator_epoch"),
            _text(evidence.get("evaluator_epoch"), identity.evaluator_epoch),
        ),
        verifier_digest=stable_digest(
            {
                "contract": _text(
                    job.get("verifier_contract_ref"),
                    _text(evidence.get("verifier_contract_ref"), identity.verifier_contract_ref),
                )
            }
        ),
        fixture_digest=_text(
            job.get("fixture_digest"),
            _text(evidence.get("fixture_digest"), "unavailable"),
        ),
        score=_finite_score(metadata.get("score")),
        valid=(not force_invalid and status == "succeeded" and result.get("outcome") == "candidate_success"),
        classification=classification,
        infrastructure_error=infrastructure_error,
        reconstruction_ref=reconstruction_ref,
        experiment_id=_optional_text(metadata.get("experiment_id")),
        attempt=_nonnegative_int(job.get("attempt", job.get("attempts", 0))),
        consumed_tokens=_nonnegative_int(consumed.get("tokens")),
        consumed_wall_seconds=_nonnegative_float(consumed.get("wall_seconds")),
        consumed_compute_units=_nonnegative_float(consumed.get("compute_units")),
        consumed_jobs=_nonnegative_int(consumed.get("jobs")),
        cleanup_succeeded=_optional_bool(result.get("cleanup_succeeded")),
    )


def _scheduler_classification(status: str, outcome: str) -> tuple[MetricClassification, bool]:
    if status == "canceled":
        return "canceled", False
    if status == "budget_exhausted":
        return "budget_exhausted", False
    if outcome == "candidate_success":
        return "candidate_result", False
    if outcome == "candidate_failure" or status == "candidate_failed":
        return "candidate_failure", False
    return "infrastructure_failure", True


def _bounded_trial_mappings(
    trials: tuple[Mapping[str, Any], ...],
    limit: int,
) -> tuple[Mapping[str, Any], ...]:
    return _bounded_mappings(
        trials,
        limit,
        keys=("evaluator_epoch", "cohort", "lane", "candidate_valid"),
    )


def _bounded_scheduler_mappings(
    jobs: tuple[Mapping[str, Any], ...],
    limit: int,
) -> tuple[Mapping[str, Any], ...]:
    return _bounded_mappings(
        jobs,
        limit,
        keys=("evaluator_epoch", "cohort_id", "status", "lane_id", "verifier_contract_ref"),
    )


def _bounded_mappings(
    items: tuple[Mapping[str, Any], ...],
    limit: int,
    *,
    keys: tuple[str, ...],
) -> tuple[Mapping[str, Any], ...]:
    if len(items) <= limit:
        return items
    representatives: list[int] = []
    signatures: set[tuple[object, ...]] = set()
    for index, item in enumerate(items):
        signature = tuple(_comparable_value(item.get(key)) for key in keys)
        result = _mapping(item.get("scored_result") or item.get("result"))
        if result:
            signature += (
                _comparable_value(result.get("outcome")),
                _comparable_value(result.get("cleanup_succeeded")),
            )
        if signature in signatures:
            continue
        signatures.add(signature)
        representatives.append(index)
    chosen = set(representatives[:limit])
    for index in range(len(items)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return tuple(items[index] for index in sorted(chosen))


def _bounded_audit_metrics(
    metrics: list[AuditMetricSummary],
    limit: int,
) -> list[AuditMetricSummary]:
    if len(metrics) <= limit:
        return metrics
    representatives: list[int] = []
    signatures: set[tuple[object, ...]] = set()
    for index, metric in enumerate(metrics):
        signature = (
            metric.evaluator_epoch,
            metric.cohort,
            metric.classification,
            metric.infrastructure_error,
            metric.cleanup_succeeded,
            metric.trial_id.endswith(":late"),
        )
        if signature in signatures:
            continue
        signatures.add(signature)
        representatives.append(index)
    chosen = set(representatives[:limit])
    for index in range(len(metrics)):
        if len(chosen) >= limit:
            break
        chosen.add(index)
    return [metrics[index] for index in sorted(chosen)]


def _truncation_and_comparability_alerts(
    trials: tuple[Mapping[str, Any], ...],
    limit: int,
) -> list[str]:
    alerts: list[str] = []
    if len(trials) > limit:
        alerts.append("evidence_truncated")
    if len({_text(trial.get("evaluator_epoch"), "unavailable") for trial in trials}) > 1:
        alerts.append("evaluator_epoch_mismatch")
    if len({_text(trial.get("cohort"), "unavailable") for trial in trials}) > 1:
        alerts.append("non_comparable_cohorts")
    return alerts


def _scheduler_integrity_alerts(
    jobs: tuple[Mapping[str, Any], ...],
    limit: int,
) -> list[str]:
    alerts: list[str] = []
    if len(jobs) > limit:
        alerts.append("evidence_truncated")
    if len({_text(job.get("evaluator_epoch"), "unavailable") for job in jobs}) > 1:
        alerts.append("evaluator_epoch_mismatch")
    if len({_text(job.get("cohort_id"), "unavailable") for job in jobs}) > 1:
        alerts.append("non_comparable_cohorts")
    results = [
        result
        for job in jobs
        for result in (
            _mapping(job.get("scored_result") or job.get("result")),
            _mapping(job.get("unscored_late_result")),
        )
        if result
    ]
    if any(result and result.get("cleanup_succeeded") is False for result in results):
        alerts.append("cleanup_failure")
    return alerts


def _comparable_value(value: object) -> object:
    if isinstance(value, (str, int, float, bool, type(None))):
        return value
    return stable_digest(value)


def _nonnegative_int(value: object) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0
    return max(0, int(value))


def _nonnegative_float(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return 0.0
    result = float(value)
    return result if math.isfinite(result) and result >= 0 else 0.0


def _optional_bool(value: object) -> bool | None:
    return value if isinstance(value, bool) else None


def _mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _sequence(value: object) -> Sequence[object]:
    return value if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)) else ()


def _text(value: object, fallback: str) -> str:
    return redact_content(value) if isinstance(value, str) and value else fallback


def _optional_text(value: object) -> str | None:
    return redact_content(value) if isinstance(value, str) and value else None


def _finite_score(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    score = float(value)
    return score if math.isfinite(score) else None


__all__ = ["CampaignAuditPacketIdentity", "CampaignCheckpointPacketFactory"]
