"""Primitive validation for durable remote-execution value objects."""

from __future__ import annotations

import math
from typing import Any, cast

_EXECUTION_STATUSES = frozenset(
    {"success", "timeout", "provider_error", "task_error", "artifact_error", "cleanup_error"}
)


def normalized_exact_float(
    value: object,
    *,
    label: str,
    allow_subclasses: bool = False,
) -> float:
    """Return a finite canonical float without silently rounding an integer."""

    valid_type = (
        not isinstance(value, bool) and isinstance(value, (int, float))
        if allow_subclasses
        else type(value) in {int, float}
    )
    if not valid_type:
        raise TypeError(f"{label} must be a number")
    try:
        normalized = float(cast(int | float, value))
    except OverflowError as exc:
        raise ValueError(f"{label} must be finite") from exc
    if not math.isfinite(normalized):
        raise ValueError(f"{label} must be finite")
    if isinstance(value, int) and int(normalized) != value:
        raise ValueError(f"{label} integer must be exactly representable as a float")
    return 0.0 if normalized == 0.0 else normalized


def normalize_resource_usage(value: Any) -> None:
    for field_name, label, optional in (
        ("wall_seconds", "remote usage wall_seconds", False),
        ("cpu_seconds", "remote usage cpu_seconds", True),
        ("peak_memory_mb", "remote usage peak_memory_mb", True),
        ("accelerator_seconds", "remote usage accelerator_seconds", True),
        ("accelerator_peak_memory_mb", "remote usage accelerator_peak_memory_mb", True),
    ):
        raw = getattr(value, field_name)
        if optional and raw is None:
            continue
        normalized = normalized_exact_float(raw, label=label)
        if normalized < 0:
            raise ValueError("remote resource usage must be non-negative and finite")
        object.__setattr__(value, field_name, normalized)


def validate_input_provenance(value: Any) -> None:
    _require_string(value.name, label="remote provenance input name", nonempty=True)
    _require_sha256(value.sha256, label="remote provenance input sha256")
    _require_nonnegative_int(value.size_bytes, label="remote provenance input size_bytes")
    _require_string(value.media_type, label="remote provenance input media_type", nonempty=True)


def validate_resolved_environment(value: Any) -> None:
    for field_name in ("image", "region", "accelerator_kind", "runtime"):
        _require_string(
            getattr(value, field_name),
            label=f"remote resolved environment {field_name}",
        )
    _require_nonnegative_int(
        value.accelerator_count,
        label="remote resolved environment accelerator_count",
    )


def validate_execution_provenance(value: Any) -> None:
    for field_name in (
        "image",
        "image_digest",
        "package_sha256",
        "fixture_digest",
        "fixture_state_sha256",
        "fixture_observation_sha256",
        "request_sha256",
        "requested_region",
        "requested_accelerator_kind",
    ):
        _require_string(
            getattr(value, field_name),
            label=f"remote provenance {field_name}",
        )
    if value.seed is not None:
        _require_int(value.seed, label="remote provenance seed")
    _require_nonnegative_int(
        value.requested_accelerator_count,
        label="remote provenance requested_accelerator_count",
    )
    if value.requested_accelerator_memory_gb is not None:
        memory = normalized_exact_float(
            value.requested_accelerator_memory_gb,
            label="remote provenance requested_accelerator_memory_gb",
        )
        if memory <= 0:
            raise ValueError("remote provenance requested_accelerator_memory_gb must be positive")
        object.__setattr__(value, "requested_accelerator_memory_gb", memory)
    for telemetry in value.required_telemetry:
        _require_string(telemetry, label="remote provenance required telemetry", nonempty=True)


def validate_cleanup_outcome(value: Any) -> None:
    _require_bool(value.attempted, label="remote cleanup attempted")
    _require_bool(value.succeeded, label="remote cleanup succeeded")
    _require_string(value.resource_id, label="remote cleanup resource_id")
    _require_string(value.detail, label="remote cleanup detail")


def validate_execution_result(value: Any) -> None:
    _require_string(value.task_id, label="remote result task_id", nonempty=True)
    _require_string(value.provider, label="remote result provider", nonempty=True)
    _require_execution_status(value.status, label="remote result status")
    for field_name in ("stdout", "stderr", "error", "session_id"):
        _require_string(getattr(value, field_name), label=f"remote result {field_name}")
    if value.exit_code is not None:
        _require_int(value.exit_code, label="remote result exit_code")
    _require_bool(value.retryable, label="remote result retryable")
    if value.retryable and value.status != "provider_error":
        raise ValueError("only provider errors may be retryable")
    if value.retryable and not (value.cleanup.attempted and value.cleanup.succeeded):
        raise ValueError("retryable remote errors require verified cleanup")


def validate_ledger_entry(value: Any) -> None:
    _require_string(value.task_id, label="external evaluation ledger task_id", nonempty=True)
    _require_string(value.provider, label="external evaluation ledger provider", nonempty=True)
    _require_execution_status(value.status, label="external evaluation ledger status")
    _require_bool(value.candidate_succeeded, label="external evaluation ledger candidate_succeeded")
    _require_bool(value.infrastructure_succeeded, label="external evaluation ledger infrastructure_succeeded")
    if value.exit_code is not None:
        _require_int(value.exit_code, label="external evaluation ledger exit_code")
    _require_string(value.detail, label="external evaluation ledger detail")
    _require_bool(value.retryable, label="external evaluation ledger retryable")
    _require_string(value.attempt_id, label="external evaluation ledger attempt_id")
    if value.attempt_id:
        _require_sha256(value.attempt_id, label="external evaluation ledger attempt_id")


def _require_string(value: object, *, label: str, nonempty: bool = False) -> str:
    if type(value) is not str:
        raise TypeError(f"{label} must be a string")
    if nonempty and not value.strip():
        raise ValueError(f"{label} must be non-empty")
    return value


def _require_bool(value: object, *, label: str) -> bool:
    if type(value) is not bool:
        raise TypeError(f"{label} must be boolean")
    return value


def _require_int(value: object, *, label: str) -> int:
    if type(value) is not int:
        raise TypeError(f"{label} must be an integer")
    return value


def _require_nonnegative_int(value: object, *, label: str) -> int:
    integer = _require_int(value, label=label)
    if integer < 0:
        raise ValueError(f"{label} must be non-negative")
    return integer


def _require_sha256(value: object, *, label: str) -> str:
    digest = _require_string(value, label=label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _require_execution_status(value: object, *, label: str) -> str:
    status = _require_string(value, label=label)
    if status not in _EXECUTION_STATUSES:
        raise ValueError(f"unsupported {label} {status!r}")
    return status
