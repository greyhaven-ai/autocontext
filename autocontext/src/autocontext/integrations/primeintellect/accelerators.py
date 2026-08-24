"""Capability validation and resolved accelerator evidence for Prime."""

from __future__ import annotations

import importlib.metadata
import math
from collections.abc import Mapping
from typing import Any

from autocontext.execution.remote_execution import (
    RemoteExecutionRequest,
    RemoteResolvedEnvironment,
    RemoteResourceUsage,
    remote_request_sha256,
)


class ProviderCapabilityDriftError(RuntimeError):
    """Raised when the provisioned resource differs from the validated request."""


def resolved_environment(sandbox: Any) -> RemoteResolvedEnvironment:
    try:
        runtime = f"prime-sandboxes/{importlib.metadata.version('prime-sandboxes')}"
    except importlib.metadata.PackageNotFoundError:
        runtime = "prime-sandboxes/unknown"
    return RemoteResolvedEnvironment(
        image=str(getattr(sandbox, "docker_image", "") or ""),
        region=str(getattr(sandbox, "region", "") or ""),
        accelerator_kind=str(getattr(sandbox, "gpu_type", "") or ""),
        accelerator_count=int(getattr(sandbox, "gpu_count", 0) or 0),
        runtime=runtime,
    )


def validate_resolved_environment(
    request: RemoteExecutionRequest,
    resolved: RemoteResolvedEnvironment,
) -> None:
    accelerator = request.resources.accelerator
    if resolved.image and resolved.image != request.image:
        raise ProviderCapabilityDriftError(f"provider resolved image {resolved.image!r}, expected {request.image!r}")
    if request.region is not None and resolved.region != request.region:
        raise ProviderCapabilityDriftError(
            f"provider resolved region {resolved.region or '<missing>'!r}, expected {request.region!r}"
        )
    if accelerator is None:
        if resolved.accelerator_kind or resolved.accelerator_count:
            raise ProviderCapabilityDriftError("provider attached an accelerator to a CPU-only request")
        return
    if not resolved.image:
        raise ProviderCapabilityDriftError("provider response omitted the resolved accelerator image")
    if resolved.accelerator_kind != accelerator.kind:
        raise ProviderCapabilityDriftError(
            f"provider resolved accelerator {resolved.accelerator_kind or '<missing>'!r}, expected {accelerator.kind!r}"
        )
    if resolved.accelerator_count != accelerator.count:
        raise ProviderCapabilityDriftError(
            f"provider resolved accelerator count {resolved.accelerator_count}, expected {accelerator.count}"
        )


def resource_usage(response: Any | None, *, wall_seconds: float) -> RemoteResourceUsage:
    return RemoteResourceUsage(
        wall_seconds=wall_seconds,
        cpu_seconds=_optional_provider_metric(response, "cpu_seconds"),
        peak_memory_mb=_optional_provider_metric(response, "peak_memory_mb"),
        accelerator_seconds=_optional_provider_metric(response, "accelerator_seconds", "gpu_seconds"),
        accelerator_peak_memory_mb=_optional_provider_metric(
            response,
            "accelerator_peak_memory_mb",
            "gpu_peak_memory_mb",
        ),
    )


def validate_required_telemetry(
    request: RemoteExecutionRequest,
    resolved: RemoteResolvedEnvironment,
    usage: RemoteResourceUsage,
) -> None:
    missing: list[str] = []
    if "hardware_identity" in request.required_telemetry and not resolved.image:
        missing.append("hardware_identity")
    if "accelerator_usage" in request.required_telemetry and usage.accelerator_seconds is None:
        missing.append("accelerator_usage")
    if "accelerator_peak_memory" in request.required_telemetry and usage.accelerator_peak_memory_mb is None:
        missing.append("accelerator_peak_memory")
    if missing:
        raise ProviderCapabilityDriftError(f"provider omitted required telemetry: {', '.join(sorted(missing))}")


def create_kwargs(
    request: RemoteExecutionRequest,
    *,
    timeout_minutes: int,
    network_access: bool,
) -> dict[str, Any]:
    resources = request.resources
    kwargs: dict[str, Any] = {
        "name": f"autocontext-{_safe_name(request.task_id)}",
        "docker_image": request.image,
        "cpu_cores": resources.cpu_cores,
        "memory_gb": resources.memory_gb,
        "disk_size_gb": resources.disk_gb,
        "timeout_minutes": max(timeout_minutes, max(1, int(request.timeout_seconds // 60) + 1)),
        "network_access": request.network_policy == "allow" and network_access,
        "idempotency_key": remote_request_sha256(request),
    }
    if resources.accelerator is not None:
        kwargs.update({"gpu_type": resources.accelerator.kind, "gpu_count": resources.accelerator.count})
    if request.region is not None:
        kwargs["region"] = request.region
    if request.snapshot_id:
        kwargs["snapshot_id"] = request.snapshot_id
    if request.secret_grants:
        kwargs["secret_grants"] = [grant.grant_id for grant in request.secret_grants]
    return kwargs


def _optional_provider_metric(response: Any | None, *names: str) -> float | None:
    if response is None:
        return None
    for name in names:
        value = response.get(name) if isinstance(response, Mapping) else getattr(response, name, None)
        if value is None:
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or value < 0:
            raise ProviderCapabilityDriftError(f"provider returned invalid {name} telemetry")
        return float(value)
    return None


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "task"


__all__ = [
    "ProviderCapabilityDriftError",
    "create_kwargs",
    "resolved_environment",
    "resource_usage",
    "validate_required_telemetry",
    "validate_resolved_environment",
]
