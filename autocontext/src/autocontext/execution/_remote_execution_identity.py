"""Hashing and provenance derivation for remote execution requests."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from autocontext.execution.remote_execution import (
        RemoteExecutionProvenance,
        RemoteExecutionRequest,
        RemoteResolvedEnvironment,
    )


def build_request_provenance(
    request: RemoteExecutionRequest,
    *,
    resolved: RemoteResolvedEnvironment | None = None,
) -> RemoteExecutionProvenance:
    """Derive immutable replay provenance from request contents, not provider output."""

    from autocontext.execution.remote_execution import (
        RemoteExecutionProvenance,
        RemoteInputProvenance,
        RemoteResolvedEnvironment,
    )

    inputs = tuple(
        RemoteInputProvenance(
            name=artifact.name,
            sha256=hashlib.sha256(artifact.content).hexdigest(),
            size_bytes=len(artifact.content),
            media_type=artifact.media_type,
        )
        for artifact in request.input_artifacts
    )
    packaged = next((item.sha256 for item in inputs if item.name == "autocontext-scenario.pyz"), "")
    package_sha256 = packaged or str(request.metadata.get("package_sha256", ""))
    seed_value = request.metadata.get("seed")
    try:
        seed = int(seed_value) if seed_value is not None else None
    except (TypeError, ValueError):
        seed = None
    image_digest = request.image.rsplit("@sha256:", 1)[-1] if "@sha256:" in request.image else ""
    fixture_digest, fixture_state_sha256, fixture_observation_sha256 = prepared_fixture_provenance(request.metadata)
    accelerator = request.resources.accelerator
    return RemoteExecutionProvenance(
        request_sha256=request_sha256(request),
        image=request.image,
        image_digest=image_digest,
        package_sha256=package_sha256,
        inputs=inputs,
        seed=seed,
        fixture_digest=fixture_digest,
        fixture_state_sha256=fixture_state_sha256,
        fixture_observation_sha256=fixture_observation_sha256,
        requested_region=request.region or "",
        requested_accelerator_kind=accelerator.kind if accelerator is not None else "",
        requested_accelerator_count=accelerator.count if accelerator is not None else 0,
        requested_accelerator_memory_gb=accelerator.memory_gb if accelerator is not None else None,
        required_telemetry=tuple(sorted(request.required_telemetry)),
        resolved=resolved or RemoteResolvedEnvironment(),
    )


def request_identity_payload(request: RemoteExecutionRequest) -> dict[str, Any]:
    accelerator = request.resources.accelerator
    return {
        "task_id": request.task_id,
        "image": request.image,
        "command_sha256": hashlib.sha256(request.command.encode("utf-8")).hexdigest(),
        "resources": {
            "cpu_cores": request.resources.cpu_cores,
            "memory_gb": request.resources.memory_gb,
            "disk_gb": request.resources.disk_gb,
            "accelerator": (
                {
                    "kind": accelerator.kind,
                    "count": accelerator.count,
                    "memory_gb": accelerator.memory_gb,
                }
                if accelerator is not None
                else None
            ),
        },
        "region": request.region,
        "required_telemetry": sorted(request.required_telemetry),
        "timeout_seconds": request.timeout_seconds,
        "network_policy": request.network_policy,
        "secrets_policy": request.secrets_policy,
        "secret_grants": [
            {"name": grant.name, "grant_id": grant.grant_id, "expires_at": grant.expires_at}
            for grant in request.secret_grants
        ],
        "inputs": [
            {
                "name": artifact.name,
                "sha256": hashlib.sha256(artifact.content).hexdigest(),
                "media_type": artifact.media_type,
            }
            for artifact in request.input_artifacts
        ],
        "expected_outputs": list(request.expected_outputs),
        "lifecycle": request.lifecycle,
        "environment": dict(sorted(request.environment.items())),
        "snapshot_id": request.snapshot_id,
        "max_reuse_tasks": request.max_reuse_tasks,
        "metadata": dict(sorted(request.metadata.items())),
    }


def request_identity_payload_sha256(payload: Mapping[str, object]) -> str:
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def request_sha256(request: RemoteExecutionRequest) -> str:
    return request_identity_payload_sha256(request_identity_payload(request))


def requests_are_reuse_compatible(requests: Sequence[RemoteExecutionRequest]) -> bool:
    """Return whether every request can safely share one provisioned sandbox.

    Reused sessions retain filesystem, credentials, environment, and snapshot
    state, so every provisioned-sandbox field must agree across the cohort.
    """

    if not requests:
        return False
    first = requests[0]
    return all(
        request.lifecycle == "reuse_matched_trials"
        and request.image == first.image
        and request.resources == first.resources
        and request.region == first.region
        and request.required_telemetry == first.required_telemetry
        and request.network_policy == first.network_policy
        and request.secrets_policy == first.secrets_policy
        and request.secret_grants == first.secret_grants
        and request.input_artifacts == first.input_artifacts
        and dict(request.environment) == dict(first.environment)
        and request.snapshot_id == first.snapshot_id
        for request in requests
    ) and len(requests) <= min(request.max_reuse_tasks for request in requests)


def prepared_fixture_provenance(metadata: Mapping[str, str]) -> tuple[str, str, str]:
    """Return a complete prepared-fixture attestation or reject partial metadata."""

    keys = ("fixture_digest", "fixture_state_sha256", "fixture_observation_sha256")
    raw_values = tuple(metadata.get(key) for key in keys)
    present = tuple(value is not None for value in raw_values)
    if any(present) and not all(present):
        missing = ", ".join(key for key, supplied in zip(keys, present, strict=True) if not supplied)
        raise ValueError(f"prepared fixture provenance is incomplete; missing: {missing}")
    if not any(present):
        return "", "", ""
    values: list[str] = []
    for key, raw_value in zip(keys, raw_values, strict=True):
        if (
            not isinstance(raw_value, str)
            or len(raw_value) != 64
            or any(character not in "0123456789abcdef" for character in raw_value)
        ):
            raise ValueError(f"prepared fixture provenance must use lowercase sha256 hex: {key}")
        values.append(raw_value)
    return values[0], values[1], values[2]
