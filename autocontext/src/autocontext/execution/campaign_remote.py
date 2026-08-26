"""Remote placement models and scheduler adapters for campaign plans."""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from typing import Any, cast

from pydantic import Field, model_validator

from autocontext.config.settings import AppSettings
from autocontext.context_bundles.models import stable_digest
from autocontext.execution._immutable_json import thaw_json
from autocontext.execution.campaign_scheduler_adapters import campaign_result_from_remote
from autocontext.execution.campaign_scheduler_models import (
    CampaignJobResult,
    SchedulerBudget,
    SchedulerResources,
)
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteResourceRequest,
    RemoteTelemetryKind,
)
from autocontext.util.models import StrictModel

_PRIME_CREATION_POLL_BOUND_SECONDS = 2.0
_PRIME_CLEANUP_BOUND_SECONDS = 30.0


class CampaignPlanAccelerator(StrictModel):
    kind: str = Field(min_length=1)
    count: int = Field(default=1, ge=1)


def _default_remote_telemetry() -> list[RemoteTelemetryKind]:
    return ["hardware_identity"]


class CampaignPlanRemoteRequirements(StrictModel):
    image: str = ""
    cpu_cores: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    memory_gb: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    disk_gb: float | None = Field(default=None, gt=0.0, allow_inf_nan=False)
    accelerator: CampaignPlanAccelerator | None = None
    region: str = ""
    required_telemetry: list[RemoteTelemetryKind] = Field(default_factory=_default_remote_telemetry)

    @model_validator(mode="after")
    def _validate_remote_requirements(self) -> CampaignPlanRemoteRequirements:
        if self.region and not self.region.strip():
            raise ValueError("campaign remote region must be non-empty when supplied")
        if len(self.required_telemetry) != len(set(self.required_telemetry)):
            raise ValueError("campaign remote telemetry requirements must be unique")
        if self.accelerator is not None and "hardware_identity" not in self.required_telemetry:
            raise ValueError("accelerator campaigns must require hardware_identity telemetry")
        return self


@dataclass(frozen=True, slots=True)
class CampaignExecutionEnvelope:
    """Conservative time bounds for one scheduler attempt."""

    provider_seconds: float
    wall_seconds: float


def campaign_execution_envelope(
    settings: AppSettings | None,
    command_timeout_seconds: float,
    *,
    remote_execution: bool,
) -> CampaignExecutionEnvelope:
    """Include Prime's configured provisioning retries in campaign admission.

    ``prime-sandboxes`` 0.2.27 waits at most two seconds between creation
    polls, while the adapter bounds every cleanup attempt at thirty seconds.
    Failed provisioning attempts are cleaned before the configured linear
    backoff, so backoff contributes to wall time but not paid accelerator time.
    """

    if settings is None or settings.executor_mode != "primeintellect" or not remote_execution:
        return CampaignExecutionEnvelope(command_timeout_seconds, command_timeout_seconds)
    attempts = settings.primeintellect_max_retries + 1
    creation_wait = settings.primeintellect_wait_attempts * _PRIME_CREATION_POLL_BOUND_SECONDS
    command_seconds = max(1, math.ceil(command_timeout_seconds))
    provider_seconds = command_seconds + attempts * (creation_wait + _PRIME_CLEANUP_BOUND_SECONDS)
    retry_backoff = settings.primeintellect_backoff_seconds * (
        settings.primeintellect_max_retries * (settings.primeintellect_max_retries + 1) / 2
    )
    wall_seconds = provider_seconds + retry_backoff
    if not math.isfinite(provider_seconds) or not math.isfinite(wall_seconds):
        raise ValueError("Prime Intellect campaign execution envelope must be finite")
    return CampaignExecutionEnvelope(provider_seconds, wall_seconds)


def remote_requirements_payload(
    requirements: RemoteExecutionRequirements | None,
) -> dict[str, Any] | None:
    if requirements is None:
        return None
    accelerator = requirements.resources.accelerator
    return {
        "image": requirements.image,
        "resources": {
            "cpu_cores": requirements.resources.cpu_cores,
            "memory_gb": requirements.resources.memory_gb,
            "disk_gb": requirements.resources.disk_gb,
            "accelerator": ({"kind": accelerator.kind, "count": accelerator.count} if accelerator is not None else None),
        },
        "region": requirements.region,
        "required_telemetry": sorted(requirements.required_telemetry),
    }


def job_resources(requirements: RemoteExecutionRequirements | None) -> SchedulerResources:
    if requirements is not None:
        accelerator = requirements.resources.accelerator
        return SchedulerResources(
            cpu_cores=requirements.resources.cpu_cores,
            memory_gb=requirements.resources.memory_gb,
            disk_gb=requirements.resources.disk_gb,
            accelerator_kind=accelerator.kind if accelerator is not None else None,
            accelerator_count=accelerator.count if accelerator is not None else 0,
        )
    return SchedulerResources(cpu_cores=1.0, memory_gb=1.0, disk_gb=1.0)


def worker_resources(
    requirements: RemoteExecutionRequirements | None,
    concurrency: int,
) -> SchedulerResources:
    per_job = job_resources(requirements)
    return SchedulerResources(
        cpu_cores=per_job.cpu_cores * concurrency,
        memory_gb=per_job.memory_gb * concurrency,
        disk_gb=per_job.disk_gb * concurrency,
        accelerator_kind=per_job.accelerator_kind,
        accelerator_count=per_job.accelerator_count * concurrency,
    )


def job_capabilities(requirements: RemoteExecutionRequirements | None) -> frozenset[str]:
    capabilities = {"scenario_evaluation"}
    if requirements is None:
        return frozenset(capabilities)
    digest = stable_digest(remote_requirements_payload(requirements))
    capabilities.add(f"remote_requirements:{digest}")
    if requirements.resources.accelerator is not None:
        capabilities.add("accelerator")
    capabilities.update(f"telemetry:{name}" for name in requirements.required_telemetry)
    return frozenset(capabilities)


def remote_result_dict(result: RemoteExecutionResult) -> dict[str, Any]:
    return {
        "task_id": result.task_id,
        "provider": result.provider,
        "status": result.status,
        "exit_code": result.exit_code,
        "usage": asdict(result.usage),
        "cleanup": asdict(result.cleanup),
        "error": result.error,
        "session_id": result.session_id,
        "provenance": asdict(result.provenance),
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "message": event.message,
                "fields": thaw_json(event.fields),
            }
            for event in result.events
        ],
        "retryable": result.retryable,
    }


def campaign_result_with_reservation(
    remote: RemoteExecutionResult,
    reservation: SchedulerBudget,
    *,
    output_ref: str,
) -> CampaignJobResult:
    base = campaign_result_from_remote(remote)
    accelerator_requested = remote.provenance.requested_accelerator_count > 0
    if remote.usage.accelerator_seconds is not None:
        compute_units = remote.usage.accelerator_seconds
    elif accelerator_requested:
        compute_units = max(
            reservation.compute_units,
            remote.usage.wall_seconds * remote.provenance.requested_accelerator_count,
        )
    else:
        compute_units = remote.usage.cpu_seconds if remote.usage.cpu_seconds is not None else reservation.compute_units
    return CampaignJobResult(
        outcome=base.outcome,
        consumed=SchedulerBudget(
            tokens=reservation.tokens,
            wall_seconds=remote.usage.wall_seconds,
            compute_units=compute_units,
            jobs=max(1, reservation.jobs),
            shared_evidence_tokens=reservation.shared_evidence_tokens,
        ),
        output_ref=output_ref,
        detail=base.detail,
        cleanup_succeeded=base.cleanup_succeeded,
        metadata=base.metadata,
        retryable=base.retryable,
    )


def remote_requirements_from_payload(
    payload: Mapping[str, object],
) -> RemoteExecutionRequirements | None:
    raw = payload.get("remote_requirements")
    if raw is None:
        return None
    if not isinstance(raw, Mapping):
        raise TypeError("campaign remote requirements must be a mapping")
    resources = raw.get("resources")
    if not isinstance(resources, Mapping):
        raise TypeError("campaign remote resources must be a mapping")
    accelerator_data = resources.get("accelerator")
    accelerator = None
    if accelerator_data is not None:
        if not isinstance(accelerator_data, Mapping):
            raise TypeError("campaign accelerator requirements must be a mapping")
        accelerator = RemoteAcceleratorRequest(
            kind=str(accelerator_data["kind"]),
            count=int(accelerator_data["count"]),
        )
    telemetry = raw.get("required_telemetry", [])
    if not isinstance(telemetry, Sequence) or isinstance(telemetry, (str, bytes, bytearray)):
        raise TypeError("campaign remote telemetry requirements must be an array")
    return RemoteExecutionRequirements(
        image=str(raw["image"]),
        resources=RemoteResourceRequest(
            cpu_cores=float(resources["cpu_cores"]),
            memory_gb=float(resources["memory_gb"]),
            disk_gb=float(resources["disk_gb"]),
            accelerator=accelerator,
        ),
        region=str(raw["region"]) if raw.get("region") is not None else None,
        required_telemetry=frozenset(cast(RemoteTelemetryKind, str(item)) for item in telemetry),
    )


__all__ = [
    "CampaignExecutionEnvelope",
    "CampaignPlanAccelerator",
    "CampaignPlanRemoteRequirements",
    "campaign_execution_envelope",
    "campaign_result_with_reservation",
    "job_capabilities",
    "job_resources",
    "remote_requirements_from_payload",
    "remote_requirements_payload",
    "remote_result_dict",
    "worker_resources",
]
