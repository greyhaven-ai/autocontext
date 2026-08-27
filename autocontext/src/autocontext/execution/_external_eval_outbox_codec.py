"""Strict, versioned codecs for the durable external-evaluation outbox."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
from dataclasses import asdict
from typing import Any, cast

from autocontext.execution._immutable_json import thaw_json
from autocontext.execution.remote_execution import (
    ExternalEvalLedgerEntry,
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionProvenance,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteExecutionStatus,
    RemoteInputProvenance,
    RemoteOutputArtifact,
    RemoteResolvedEnvironment,
    RemoteResourceUsage,
    remote_request_provenance,
    remote_request_sha256,
)

_REMOTE_EXECUTION_STATUSES = frozenset(
    {"success", "timeout", "provider_error", "task_error", "artifact_error", "cleanup_error"}
)


def request_payload(provider: str, request: RemoteExecutionRequest) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "provider": provider,
        "task_id": request.task_id,
        "request_sha256": remote_request_sha256(request),
        "provenance": thaw_json(asdict(remote_request_provenance(request))),
        "lifecycle": request.lifecycle,
        "timeout_seconds": request.timeout_seconds,
        "resources": asdict(request.resources),
        "region": request.region,
        "required_telemetry": sorted(request.required_telemetry),
        "network_policy": request.network_policy,
        "expected_outputs": list(request.expected_outputs),
        "metadata": dict(request.metadata),
    }


def result_payload(result: RemoteExecutionResult) -> dict[str, Any]:
    if type(result) is not RemoteExecutionResult:
        raise TypeError("external-evaluation result must be a RemoteExecutionResult")
    return {
        "schema_version": 1,
        "task_id": result.task_id,
        "provider": result.provider,
        "status": result.status,
        "stdout": result.stdout,
        "stderr": result.stderr,
        "exit_code": result.exit_code,
        "artifacts": [
            {
                "name": artifact.name,
                "content_base64": base64.b64encode(artifact.content).decode("ascii"),
                "media_type": artifact.media_type,
            }
            for artifact in result.artifacts
        ],
        "events": [
            {
                "sequence": event.sequence,
                "event_type": event.event_type,
                "message": event.message,
                "fields": thaw_json(event.fields),
            }
            for event in result.events
        ],
        "usage": asdict(result.usage),
        "cleanup": asdict(result.cleanup),
        "error": result.error,
        "session_id": result.session_id,
        "provenance": thaw_json(asdict(result.provenance)),
        "retryable": result.retryable,
    }


def result_from_payload(payload: Any) -> RemoteExecutionResult:
    values = expect_object(
        payload,
        "external-evaluation result payload",
        {
            "schema_version",
            "task_id",
            "provider",
            "status",
            "stdout",
            "stderr",
            "exit_code",
            "artifacts",
            "events",
            "usage",
            "cleanup",
            "error",
            "session_id",
            "provenance",
            "retryable",
        },
    )
    _expect_schema_version(values, "external-evaluation result payload")
    artifacts = []
    try:
        for value in expect_list(values["artifacts"], "external-evaluation result artifacts"):
            item = expect_object(
                value,
                "external-evaluation result artifact",
                {"name", "content_base64", "media_type"},
            )
            artifacts.append(
                RemoteOutputArtifact(
                    name=expect_str(item["name"], "artifact name", nonempty=True),
                    content=base64.b64decode(
                        expect_str(item["content_base64"], "artifact content_base64"),
                        validate=True,
                    ),
                    media_type=expect_str(item["media_type"], "artifact media_type", nonempty=True),
                )
            )
    except (ValueError, binascii.Error) as exc:
        raise ValueError("invalid external-evaluation result artifacts") from exc
    events = []
    for value in expect_list(values["events"], "external-evaluation result events"):
        event = expect_object(
            value,
            "external-evaluation result event",
            {"sequence", "event_type", "message", "fields"},
        )
        events.append(
            RemoteExecutionEvent(
                sequence=expect_int(event["sequence"], "event sequence"),
                event_type=expect_str(event["event_type"], "event type", nonempty=True),
                message=expect_str(event["message"], "event message"),
                fields=expect_json_object(event["fields"], "external-evaluation event fields"),
            )
        )
    return RemoteExecutionResult(
        task_id=expect_str(values["task_id"], "result task_id", nonempty=True),
        provider=expect_str(values["provider"], "result provider", nonempty=True),
        status=_expect_execution_status(values["status"]),
        stdout=expect_str(values["stdout"], "result stdout"),
        stderr=expect_str(values["stderr"], "result stderr"),
        exit_code=_expect_optional_int(values["exit_code"], "result exit_code"),
        artifacts=tuple(artifacts),
        events=tuple(events),
        usage=_usage_from_payload(values["usage"]),
        cleanup=_cleanup_from_payload(values["cleanup"]),
        error=expect_str(values["error"], "result error"),
        session_id=expect_str(values["session_id"], "result session_id"),
        provenance=_provenance_from_payload(values["provenance"]),
        retryable=expect_bool(values["retryable"], "result retryable"),
    )


def ledger_payload(entry: ExternalEvalLedgerEntry) -> dict[str, Any]:
    if type(entry) is not ExternalEvalLedgerEntry:
        raise TypeError("external-evaluation ledger must be an ExternalEvalLedgerEntry")
    values = cast(dict[str, Any], thaw_json(asdict(entry)))
    return {"schema_version": 1, **values}


def ledger_from_payload(payload: Any) -> ExternalEvalLedgerEntry:
    values = expect_object(
        payload,
        "external-evaluation ledger payload",
        {
            "schema_version",
            "task_id",
            "provider",
            "status",
            "candidate_succeeded",
            "infrastructure_succeeded",
            "exit_code",
            "usage",
            "cleanup",
            "detail",
            "provenance",
            "retryable",
            "attempt_id",
        },
    )
    _expect_schema_version(values, "external-evaluation ledger payload")
    return ExternalEvalLedgerEntry(
        task_id=expect_str(values["task_id"], "ledger task_id", nonempty=True),
        provider=expect_str(values["provider"], "ledger provider", nonempty=True),
        status=_expect_execution_status(values["status"]),
        candidate_succeeded=expect_bool(values["candidate_succeeded"], "ledger candidate_succeeded"),
        infrastructure_succeeded=expect_bool(
            values["infrastructure_succeeded"], "ledger infrastructure_succeeded"
        ),
        exit_code=_expect_optional_int(values["exit_code"], "ledger exit_code"),
        usage=_usage_from_payload(values["usage"]),
        cleanup=_cleanup_from_payload(values["cleanup"]),
        detail=expect_str(values["detail"], "ledger detail"),
        provenance=_provenance_from_payload(values["provenance"]),
        retryable=expect_bool(values["retryable"], "ledger retryable"),
        attempt_id=expect_sha256(values["attempt_id"], "ledger attempt_id"),
    )


def _provenance_from_payload(payload: Any) -> RemoteExecutionProvenance:
    values = expect_object(
        payload,
        "external-evaluation provenance payload",
        {
            "image",
            "image_digest",
            "package_sha256",
            "inputs",
            "seed",
            "fixture_digest",
            "fixture_state_sha256",
            "fixture_observation_sha256",
            "request_sha256",
            "requested_region",
            "requested_accelerator_kind",
            "requested_accelerator_count",
            "requested_accelerator_memory_gb",
            "required_telemetry",
            "resolved",
        },
    )
    inputs = []
    for value in expect_list(values["inputs"], "provenance inputs"):
        item = expect_object(value, "provenance input", {"name", "sha256", "size_bytes", "media_type"})
        inputs.append(
            RemoteInputProvenance(
                name=expect_str(item["name"], "provenance input name", nonempty=True),
                sha256=expect_sha256(item["sha256"], "provenance input sha256"),
                size_bytes=expect_int(item["size_bytes"], "provenance input size_bytes"),
                media_type=expect_str(item["media_type"], "provenance input media_type", nonempty=True),
            )
        )
    resolved = expect_object(
        values["resolved"],
        "resolved environment",
        {"image", "region", "accelerator_kind", "accelerator_count", "runtime"},
    )
    telemetry = tuple(
        expect_str(value, "required telemetry kind", nonempty=True)
        for value in expect_list(values["required_telemetry"], "required telemetry")
    )
    return RemoteExecutionProvenance(
        image=expect_str(values["image"], "provenance image"),
        image_digest=expect_str(values["image_digest"], "provenance image_digest"),
        package_sha256=expect_str(values["package_sha256"], "provenance package_sha256"),
        inputs=tuple(inputs),
        seed=_expect_optional_int(values["seed"], "provenance seed"),
        fixture_digest=expect_str(values["fixture_digest"], "provenance fixture_digest"),
        fixture_state_sha256=expect_str(values["fixture_state_sha256"], "provenance fixture_state_sha256"),
        fixture_observation_sha256=expect_str(
            values["fixture_observation_sha256"], "provenance fixture_observation_sha256"
        ),
        request_sha256=expect_str(values["request_sha256"], "provenance request_sha256"),
        requested_region=expect_str(values["requested_region"], "provenance requested_region"),
        requested_accelerator_kind=expect_str(
            values["requested_accelerator_kind"], "provenance requested_accelerator_kind"
        ),
        requested_accelerator_count=expect_int(
            values["requested_accelerator_count"], "provenance requested_accelerator_count"
        ),
        requested_accelerator_memory_gb=expect_optional_exact_number(
            values["requested_accelerator_memory_gb"], "provenance requested_accelerator_memory_gb"
        ),
        required_telemetry=telemetry,
        resolved=RemoteResolvedEnvironment(
            image=expect_str(resolved["image"], "resolved image"),
            region=expect_str(resolved["region"], "resolved region"),
            accelerator_kind=expect_str(resolved["accelerator_kind"], "resolved accelerator_kind"),
            accelerator_count=expect_int(resolved["accelerator_count"], "resolved accelerator_count"),
            runtime=expect_str(resolved["runtime"], "resolved runtime"),
        ),
    )


def request_status_payload(payload: Any) -> dict[str, Any]:
    values = expect_object(
        payload,
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
    _expect_schema_version(values, "external-evaluation request payload")
    provider = expect_str(values["provider"], "request provider", nonempty=True)
    task_id = expect_str(values["task_id"], "request task_id", nonempty=True)
    request_sha256 = expect_sha256(values["request_sha256"], "request sha256")
    _provenance_from_payload(values["provenance"])
    lifecycle = expect_str(values["lifecycle"], "request lifecycle")
    if lifecycle not in {"ephemeral_per_eval", "reuse_matched_trials", "warm_snapshot"}:
        raise ValueError(f"unsupported request lifecycle {lifecycle!r}")
    network_policy = expect_str(values["network_policy"], "request network_policy")
    if network_policy not in {"deny", "allow"}:
        raise ValueError(f"unsupported request network_policy {network_policy!r}")
    timeout_seconds = _expect_positive_number(values["timeout_seconds"], "request timeout_seconds")
    resources = expect_object(
        values["resources"],
        "request resources",
        {"cpu_cores", "memory_gb", "disk_gb", "accelerator"},
    )
    cpu_cores = _expect_positive_number(resources["cpu_cores"], "request cpu_cores")
    memory_gb = _expect_positive_number(resources["memory_gb"], "request memory_gb")
    disk_gb = _expect_positive_number(resources["disk_gb"], "request disk_gb")
    accelerator_kind = ""
    accelerator_count = 0
    accelerator = resources["accelerator"]
    if accelerator is not None:
        accelerator_values = expect_object(
            accelerator,
            "request accelerator",
            {"kind", "count", "memory_gb"},
        )
        accelerator_kind = expect_str(accelerator_values["kind"], "request accelerator kind", nonempty=True)
        accelerator_count = expect_int(accelerator_values["count"], "request accelerator count")
        if accelerator_count < 1:
            raise ValueError("request accelerator count must be positive")
        memory = expect_optional_number(accelerator_values["memory_gb"], "request accelerator memory_gb")
        if memory is not None and memory <= 0:
            raise ValueError("request accelerator memory_gb must be positive")
    region = values["region"]
    if region is not None:
        expect_str(region, "request region", nonempty=True)
    for telemetry_kind in expect_list(values["required_telemetry"], "request required_telemetry"):
        expect_str(telemetry_kind, "request telemetry kind", nonempty=True)
    for output in expect_list(values["expected_outputs"], "request expected_outputs"):
        expect_str(output, "request expected output", nonempty=True)
    metadata = expect_json_object(values["metadata"], "request metadata")
    if any(not key or type(value) is not str for key, value in metadata.items()):
        raise ValueError("request metadata requires non-empty string keys and string values")
    return {
        "provider": provider,
        "task_id": task_id,
        "request_sha256": request_sha256,
        "timeout_seconds": timeout_seconds,
        "cpu_cores": cpu_cores,
        "memory_gb": memory_gb,
        "disk_gb": disk_gb,
        "accelerator_kind": accelerator_kind,
        "accelerator_count": accelerator_count,
    }


def _usage_from_payload(payload: Any) -> RemoteResourceUsage:
    values = expect_object(
        payload,
        "resource usage",
        {"wall_seconds", "cpu_seconds", "peak_memory_mb", "accelerator_seconds", "accelerator_peak_memory_mb"},
    )
    return RemoteResourceUsage(
        wall_seconds=expect_exact_number(values["wall_seconds"], "usage wall_seconds"),
        cpu_seconds=expect_optional_exact_number(values["cpu_seconds"], "usage cpu_seconds"),
        peak_memory_mb=expect_optional_exact_number(values["peak_memory_mb"], "usage peak_memory_mb"),
        accelerator_seconds=expect_optional_exact_number(values["accelerator_seconds"], "usage accelerator_seconds"),
        accelerator_peak_memory_mb=expect_optional_exact_number(
            values["accelerator_peak_memory_mb"], "usage accelerator_peak_memory_mb"
        ),
    )


def _cleanup_from_payload(payload: Any) -> RemoteCleanupOutcome:
    values = expect_object(payload, "cleanup outcome", {"attempted", "succeeded", "resource_id", "detail"})
    return RemoteCleanupOutcome(
        attempted=expect_bool(values["attempted"], "cleanup attempted"),
        succeeded=expect_bool(values["succeeded"], "cleanup succeeded"),
        resource_id=expect_str(values["resource_id"], "cleanup resource_id"),
        detail=expect_str(values["detail"], "cleanup detail"),
    )


def expect_object(value: Any, label: str, keys: set[str]) -> dict[str, Any]:
    if type(value) is not dict:
        raise ValueError(f"{label} must be an object")
    actual_keys = set(value)
    if actual_keys != keys or any(type(key) is not str for key in actual_keys):
        raise ValueError(f"{label} has invalid fields")
    return value


def expect_json_object(value: Any, label: str) -> dict[str, Any]:
    if type(value) is not dict or any(type(key) is not str for key in value):
        raise ValueError(f"{label} must be an object with string keys")
    for item in value.values():
        _validate_json_value(item, label)
    return value


def _validate_json_value(value: Any, label: str) -> None:
    if value is None or type(value) in {str, bool, int}:
        return
    if type(value) is float:
        if not math.isfinite(value):
            raise ValueError(f"{label} contains a non-finite number")
        return
    if type(value) is list:
        for item in value:
            _validate_json_value(item, label)
        return
    if type(value) is dict and all(type(key) is str for key in value):
        for item in value.values():
            _validate_json_value(item, label)
        return
    raise ValueError(f"{label} contains a non-JSON value")


def expect_list(value: Any, label: str) -> list[Any]:
    if type(value) is not list:
        raise ValueError(f"{label} must be an array")
    return value


def _expect_schema_version(values: dict[str, Any], label: str) -> None:
    if expect_int(values["schema_version"], f"{label} schema_version") != 1:
        raise ValueError(f"unsupported {label}")


def expect_str(value: Any, label: str, *, nonempty: bool = False) -> str:
    if type(value) is not str or (nonempty and not value):
        qualifier = "non-empty " if nonempty else ""
        raise ValueError(f"{label} must be a {qualifier}string")
    return value


def expect_bool(value: Any, label: str) -> bool:
    if type(value) is not bool:
        raise ValueError(f"{label} must be a boolean")
    return value


def sqlite_bool(value: Any, *, label: str) -> bool:
    if type(value) is not int or value not in {0, 1}:
        raise ValueError(f"{label} must be 0 or 1")
    return value == 1


def expect_int(value: Any, label: str) -> int:
    if type(value) is not int:
        raise ValueError(f"{label} must be an integer")
    return value


def _expect_optional_int(value: Any, label: str) -> int | None:
    if value is None:
        return None
    return expect_int(value, label)


def expect_number(value: Any, label: str) -> float:
    if type(value) not in {int, float}:
        raise ValueError(f"{label} must be a finite number")
    try:
        number = float(value)
    except OverflowError as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def expect_exact_number(value: Any, label: str) -> float:
    number = expect_number(value, label)
    if type(value) is int and int(number) != value:
        raise ValueError(f"{label} integer must be exactly representable as a float")
    return 0.0 if number == 0.0 else number


def _expect_positive_number(value: Any, label: str) -> float:
    number = expect_number(value, label)
    if number <= 0:
        raise ValueError(f"{label} must be positive")
    return number


def expect_optional_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return expect_number(value, label)


def expect_optional_exact_number(value: Any, label: str) -> float | None:
    if value is None:
        return None
    return expect_exact_number(value, label)


def expect_sha256(value: Any, label: str) -> str:
    digest = expect_str(value, label)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError(f"{label} must be a lowercase SHA-256 digest")
    return digest


def _expect_execution_status(value: Any) -> RemoteExecutionStatus:
    status = expect_str(value, "remote execution status")
    if status not in _REMOTE_EXECUTION_STATUSES:
        raise ValueError(f"unsupported remote execution status {status!r}")
    return cast(RemoteExecutionStatus, status)


def load_json(raw: str, *, label: str) -> Any:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError) as exc:
        raise ValueError(f"{label} is not valid JSON") from exc


def checked_payload(raw: Any, expected_sha256: Any, *, label: str) -> str:
    if not isinstance(raw, str) or not isinstance(expected_sha256, str) or sha256(raw) != expected_sha256:
        raise ValueError(f"{label} checksum mismatch")
    return raw


def canonical_json(payload: Any) -> str:
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()
