"""Event payload codecs for campaign scheduler state replay."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict
from typing import Any

from autocontext.execution.campaign_scheduler_models import (
    CampaignEvidenceGrant,
    CampaignJobRequest,
    CampaignJobResult,
    CampaignLease,
    EvaluationLaneIdentity,
    SchedulerBudget,
    SchedulerResources,
    WorkerDescriptor,
)


def mapping(value: object) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise TypeError("expected a mapping")
    return {str(key): item for key, item in value.items()}


def sequence(value: object) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)):
        raise TypeError("expected a sequence")
    return value


def as_float(value: object) -> float:
    if not isinstance(value, (str, int, float)):
        raise TypeError("expected a number")
    return float(value)


def budget_from(value: object) -> SchedulerBudget:
    return SchedulerBudget(**mapping(value))


def _resources_from(value: object) -> SchedulerResources:
    return SchedulerResources(**mapping(value))


def _lane_from(value: object) -> EvaluationLaneIdentity:
    data = mapping(value)
    return EvaluationLaneIdentity(
        lane_id=str(data["lane_id"]),
        fixture_digest=str(data["fixture_digest"]),
        seeds=tuple(str(seed) for seed in data["seeds"]),
        evaluator_epoch=str(data["evaluator_epoch"]),
        verifier_contract_ref=str(data["verifier_contract_ref"]),
    )


def worker_to_dict(worker: WorkerDescriptor) -> dict[str, Any]:
    return {
        **asdict(worker),
        "capabilities": sorted(worker.capabilities),
        "sandbox_features": sorted(worker.sandbox_features),
    }


def worker_from(value: object) -> WorkerDescriptor:
    data = mapping(value)
    return WorkerDescriptor(
        worker_id=str(data["worker_id"]),
        runtime=str(data["runtime"]),
        resources=_resources_from(data["resources"]),
        capabilities=frozenset(str(item) for item in data.get("capabilities", [])),
        sandbox_features=frozenset(str(item) for item in data.get("sandbox_features", [])),
        locality=str(data.get("locality", "local")),
        concurrency=int(data.get("concurrency", 1)),
        environment_labels={str(key): str(item) for key, item in mapping(data.get("environment_labels", {})).items()},
    )


def job_to_dict(job: CampaignJobRequest) -> dict[str, Any]:
    return {
        **asdict(job),
        "required_capabilities": sorted(job.required_capabilities),
        "lane": asdict(job.lane),
    }


def job_from(value: object) -> CampaignJobRequest:
    data = mapping(value)
    return CampaignJobRequest(
        job_id=str(data["job_id"]),
        idempotency_key=str(data["idempotency_key"]),
        campaign_id=str(data["campaign_id"]),
        branch_id=str(data["branch_id"]),
        job_kind=str(data["job_kind"]),  # type: ignore[arg-type]
        lane=_lane_from(data["lane"]),
        resources=_resources_from(data["resources"]),
        required_capabilities=frozenset(str(item) for item in data.get("required_capabilities", [])),
        reservation=budget_from(data["reservation"]),
        max_attempts=int(data["max_attempts"]),
        cohort_id=str(data.get("cohort_id", "")),
        prefer_warm_reuse=bool(data.get("prefer_warm_reuse", False)),
        evidence_grant_ids=tuple(str(item) for item in data.get("evidence_grant_ids", [])),
        payload=mapping(data.get("payload", {})),
    )


def lease_from(value: object) -> CampaignLease:
    data = mapping(value)
    return CampaignLease(
        lease_id=str(data["lease_id"]),
        job_id=str(data["job_id"]),
        worker_id=str(data["worker_id"]),
        attempt=int(data["attempt"]),
        issued_at=float(data["issued_at"]),
        expires_at=float(data["expires_at"]),
        environment_fingerprint=str(data["environment_fingerprint"]),
        lifecycle=str(data["lifecycle"]),  # type: ignore[arg-type]
        reuse_key=str(data["reuse_key"]),
    )


def result_to_dict(result: CampaignJobResult) -> dict[str, Any]:
    return {**asdict(result), "consumed": asdict(result.consumed)}


def result_from(value: object) -> CampaignJobResult:
    data = mapping(value)
    return CampaignJobResult(
        outcome=str(data["outcome"]),  # type: ignore[arg-type]
        consumed=budget_from(data["consumed"]),
        output_ref=str(data.get("output_ref", "")),
        detail=str(data.get("detail", "")),
        cleanup_succeeded=bool(data.get("cleanup_succeeded", True)),
        metadata=mapping(data.get("metadata", {})),
    )


def evidence_from(value: object) -> CampaignEvidenceGrant:
    data = mapping(value)
    return CampaignEvidenceGrant(
        grant_id=str(data["grant_id"]),
        campaign_id=str(data["campaign_id"]),
        from_branch_id=str(data["from_branch_id"]),
        to_branch_id=str(data["to_branch_id"]),
        evidence_ref=str(data["evidence_ref"]),
        token_cost=int(data["token_cost"]),
    )
