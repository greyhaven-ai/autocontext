"""Stable attempt identities and prerelease numeric compatibility."""

from __future__ import annotations

import itertools
import math
from dataclasses import replace
from typing import Any, cast

from autocontext.execution._external_eval_outbox_codec import (
    canonical_json,
    expect_int,
    expect_number,
    expect_object,
    expect_str,
    sha256,
)
from autocontext.execution.remote_execution import (
    RemoteExecutionRequest,
    RemoteExecutionResult,
    _remote_request_identity_payload,
    _remote_request_identity_payload_sha256,
    remote_request_provenance,
    remote_request_sha256,
)


class LegacyNumericIdentityConflictError(RuntimeError):
    """Raised when compatibility expansion cannot safely identify one row."""


def external_eval_attempt_id(provider: str, request: RemoteExecutionRequest) -> str:
    if type(provider) is not str:
        raise TypeError("external evaluation provider must be a string")
    if type(request) is not RemoteExecutionRequest:
        raise TypeError("external evaluation request must be a RemoteExecutionRequest")
    if not provider.strip():
        raise ValueError("external evaluation provider must be non-empty")
    return attempt_id_from_digest(provider, request.task_id, remote_request_sha256(request))


def attempt_id_from_digest(provider: str, task_id: str, request_sha256: str) -> str:
    return sha256(
        canonical_json(
            {
                "provider": provider,
                "task_id": task_id,
                "request_sha256": request_sha256,
            }
        )
    )


def legacy_numeric_request_sha256_candidates(
    request: RemoteExecutionRequest,
    stored_request_payload: Any,
) -> frozenset[str]:
    """Return exact legacy hashes supported by a checksummed stored number encoding."""

    values = expect_object(
        stored_request_payload,
        "external-evaluation request payload",
        {
            "schema_version",
            "provider",
            "task_id",
            "request_sha256",
            "provenance",
            "lifecycle",
            "timeout_seconds",
            "resources",
            "region",
            "required_telemetry",
            "network_policy",
            "expected_outputs",
            "metadata",
        },
    )
    resources = expect_object(
        values["resources"],
        "request resources",
        {"cpu_cores", "memory_gb", "disk_gb", "accelerator"},
    )
    stored_numbers = (
        values["timeout_seconds"],
        resources["cpu_cores"],
        resources["memory_gb"],
        resources["disk_gb"],
    )
    current_numbers = (
        request.timeout_seconds,
        request.resources.cpu_cores,
        request.resources.memory_gb,
        request.resources.disk_gb,
    )
    labels = (
        "request timeout_seconds",
        "request cpu_cores",
        "request memory_gb",
        "request disk_gb",
    )
    if any(
        not _legacy_number_matches_request_float(stored, current, label=label)
        for stored, current, label in zip(stored_numbers, current_numbers, labels, strict=True)
    ):
        return frozenset()

    stored_accelerator = resources["accelerator"]
    current_accelerator = request.resources.accelerator
    if (stored_accelerator is None) != (current_accelerator is None):
        return frozenset()
    stored_accelerator_values: dict[str, Any] | None = None
    if stored_accelerator is not None:
        stored_accelerator_values = expect_object(
            stored_accelerator,
            "request accelerator",
            {"kind", "count", "memory_gb"},
        )
        assert current_accelerator is not None
        stored_memory = stored_accelerator_values["memory_gb"]
        memory_matches = (
            stored_memory is None
            if current_accelerator.memory_gb is None
            else stored_memory is not None
            and _legacy_number_matches_request_float(
                stored_memory,
                current_accelerator.memory_gb,
                label="request accelerator memory_gb",
            )
        )
        if (
            expect_str(stored_accelerator_values["kind"], "request accelerator kind") != current_accelerator.kind
            or expect_int(stored_accelerator_values["count"], "request accelerator count") != current_accelerator.count
            or not memory_matches
        ):
            return frozenset()

    payload = _remote_request_identity_payload(request)
    payload["timeout_seconds"] = values["timeout_seconds"]
    identity_resources = cast(dict[str, Any], payload["resources"])
    identity_resources["cpu_cores"] = resources["cpu_cores"]
    identity_resources["memory_gb"] = resources["memory_gb"]
    identity_resources["disk_gb"] = resources["disk_gb"]
    if stored_accelerator_values is not None:
        identity_accelerator = cast(dict[str, Any], identity_resources["accelerator"])
        identity_accelerator["memory_gb"] = stored_accelerator_values["memory_gb"]

    grant_payloads = cast(list[dict[str, Any]], payload["secret_grants"])
    grant_expiry_choices: list[tuple[int | float, ...]] = []
    for grant in request.secret_grants:
        choices: tuple[int | float, ...] = (grant.expires_at,)
        if grant.expires_at == 0.0:
            choices = (0.0, 0, -0.0)
        elif grant.expires_at.is_integer():
            choices = (grant.expires_at, int(grant.expires_at))
        grant_expiry_choices.append(choices)

    reuse_choices: tuple[int | float, ...] = (request.max_reuse_tasks,)
    try:
        legacy_float_reuse = float(request.max_reuse_tasks)
    except OverflowError:
        pass
    else:
        if math.isfinite(legacy_float_reuse) and int(legacy_float_reuse) == request.max_reuse_tasks:
            reuse_choices = (request.max_reuse_tasks, legacy_float_reuse)
    choice_count = len(reuse_choices)
    for choices in grant_expiry_choices:
        choice_count *= len(choices)
    if choice_count > 4096:
        raise LegacyNumericIdentityConflictError(
            f"remote evaluation {request.task_id!r} has too many ambiguous legacy numeric encodings"
        )

    digests: set[str] = set()
    for expiries in itertools.product(*grant_expiry_choices):
        for grant_payload, expiry in zip(grant_payloads, expiries, strict=True):
            grant_payload["expires_at"] = expiry
        for reuse_bound in reuse_choices:
            payload["max_reuse_tasks"] = reuse_bound
            digests.add(_remote_request_identity_payload_sha256(payload))
    return frozenset(digests)


def _legacy_number_matches_request_float(value: Any, current: float, *, label: str) -> bool:
    normalized = expect_number(value, label)
    if type(value) is int and int(normalized) != value:
        return False
    return (0.0 if normalized == 0.0 else normalized) == current


def result_matches_request(
    provider: str,
    request: RemoteExecutionRequest,
    result: RemoteExecutionResult,
    *,
    request_sha256: str | None = None,
) -> bool:
    if result.provider != provider or result.task_id != request.task_id:
        return False
    expected = replace(
        remote_request_provenance(request, resolved=result.provenance.resolved),
        request_sha256=request_sha256 or remote_request_sha256(request),
    )
    return result.provenance == expected
