"""Provider-neutral remote execution session contract (AC-978)."""

from __future__ import annotations

import base64
import binascii
import hashlib
import json
import math
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Literal, Protocol, TypeAlias

RemoteLifecyclePolicy: TypeAlias = Literal[
    "ephemeral_per_eval",
    "reuse_matched_trials",
    "warm_snapshot",
]
RemoteExecutionStatus: TypeAlias = Literal[
    "success",
    "timeout",
    "provider_error",
    "task_error",
    "artifact_error",
    "cleanup_error",
]
RemoteNetworkPolicy: TypeAlias = Literal["deny", "allow"]
RemoteSecretsPolicy: TypeAlias = Literal["deny", "scoped_grants"]
RemoteTelemetryKind: TypeAlias = Literal[
    "hardware_identity",
    "accelerator_usage",
    "accelerator_peak_memory",
]
_REMOTE_TELEMETRY_KINDS = frozenset(
    {"hardware_identity", "accelerator_usage", "accelerator_peak_memory"}
)


@dataclass(frozen=True, slots=True)
class RemoteAcceleratorRequest:
    kind: str
    count: int = 1
    memory_gb: float | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or self.count < 1:
            raise ValueError("accelerator kind must be non-empty and count must be positive")
        if self.memory_gb is not None and (not math.isfinite(self.memory_gb) or self.memory_gb <= 0):
            raise ValueError("accelerator memory must be positive and finite when supplied")


@dataclass(frozen=True, slots=True)
class RemoteResourceRequest:
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_gb: float = 5.0
    accelerator: RemoteAcceleratorRequest | None = None

    def __post_init__(self) -> None:
        values = (self.cpu_cores, self.memory_gb, self.disk_gb)
        if any(not math.isfinite(value) or value <= 0 for value in values):
            raise ValueError("remote CPU, memory, and disk requests must be positive and finite")


@dataclass(frozen=True, slots=True)
class RemoteExecutionRequirements:
    """Provider-neutral, identity-bound placement and telemetry requirements."""

    image: str
    resources: RemoteResourceRequest = field(default_factory=RemoteResourceRequest)
    region: str | None = None
    required_telemetry: frozenset[RemoteTelemetryKind] = frozenset()

    def __post_init__(self) -> None:
        if not self.image.strip():
            raise ValueError("remote execution image must be non-empty")
        if self.region is not None and not self.region.strip():
            raise ValueError("remote execution region must be non-empty when supplied")
        telemetry = frozenset(self.required_telemetry)
        unknown = telemetry - _REMOTE_TELEMETRY_KINDS
        if unknown:
            raise ValueError(f"unknown remote telemetry requirements: {', '.join(sorted(unknown))}")
        object.__setattr__(self, "required_telemetry", telemetry)


@dataclass(frozen=True, slots=True)
class RemoteProviderCapabilities:
    """Configured provider placement capabilities used before paid dispatch."""

    images: frozenset[str] = frozenset()
    regions: frozenset[str] = frozenset()
    accelerator_limits: Mapping[str, int] = field(default_factory=dict)
    telemetry: frozenset[RemoteTelemetryKind] = frozenset()
    accelerator_memory_selection: bool = False

    def __post_init__(self) -> None:
        images = frozenset(self.images)
        regions = frozenset(self.regions)
        telemetry = frozenset(self.telemetry)
        if any(not value.strip() for value in images | regions):
            raise ValueError("remote provider capability names must be non-empty")
        unknown = telemetry - _REMOTE_TELEMETRY_KINDS
        if unknown:
            raise ValueError(f"unknown remote provider telemetry: {', '.join(sorted(unknown))}")
        limits = dict(self.accelerator_limits)
        if any(
            not kind.strip() or isinstance(limit, bool) or not isinstance(limit, int) or limit < 1
            for kind, limit in limits.items()
        ):
            raise ValueError("remote accelerator capability limits require non-empty kinds and positive counts")
        object.__setattr__(self, "images", images)
        object.__setattr__(self, "regions", regions)
        object.__setattr__(self, "telemetry", telemetry)
        object.__setattr__(self, "accelerator_limits", MappingProxyType(limits))

    def mismatch_reason(self, requirements: RemoteExecutionRequirements) -> str:
        accelerator = requirements.resources.accelerator
        if self.images and requirements.image not in self.images:
            return f"image {requirements.image!r} is not in the configured provider capability set"
        if requirements.region is not None and requirements.region not in self.regions:
            return f"region {requirements.region!r} is not in the configured provider capability set"
        missing_telemetry = requirements.required_telemetry - self.telemetry
        if missing_telemetry:
            return f"required telemetry is unavailable: {', '.join(sorted(missing_telemetry))}"
        if accelerator is None:
            return ""
        if not self.images:
            return "no accelerator-compatible images are configured for the provider"
        limit = self.accelerator_limits.get(accelerator.kind)
        if limit is None:
            return f"accelerator kind {accelerator.kind!r} is not configured for the provider"
        if accelerator.count > limit:
            return (
                f"accelerator count {accelerator.count} exceeds the configured {accelerator.kind!r} "
                f"provider limit of {limit}"
            )
        if accelerator.memory_gb is not None and not self.accelerator_memory_selection:
            return "the provider does not support selecting accelerator memory"
        return ""


@dataclass(frozen=True, slots=True)
class RemoteInputArtifact:
    name: str
    content: bytes
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        _validate_artifact_name(self.name)
        if not isinstance(self.content, bytes):
            raise TypeError("remote input artifact content must be bytes")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("remote input artifact media_type must be non-empty")


@dataclass(frozen=True, slots=True)
class RemoteOutputArtifact:
    name: str
    content: bytes
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        _validate_artifact_name(self.name)
        if not isinstance(self.content, bytes):
            raise TypeError("remote output artifact content must be bytes")
        if not isinstance(self.media_type, str) or not self.media_type.strip():
            raise ValueError("remote output artifact media_type must be non-empty")


@dataclass(frozen=True, slots=True)
class RemoteSecretGrant:
    """Opaque host-plane reference; never carries a secret value."""

    name: str
    grant_id: str
    expires_at: float

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.grant_id.strip():
            raise ValueError("secret grant name and id must be non-empty")
        if not math.isfinite(self.expires_at):
            raise ValueError("secret grant expiry must be finite")


@dataclass(frozen=True, slots=True)
class RemoteExecutionRequest:
    task_id: str
    image: str
    command: str
    resources: RemoteResourceRequest = field(default_factory=RemoteResourceRequest)
    region: str | None = None
    required_telemetry: frozenset[RemoteTelemetryKind] = frozenset()
    timeout_seconds: float = 30.0
    network_policy: RemoteNetworkPolicy = "deny"
    secrets_policy: RemoteSecretsPolicy = "deny"
    secret_grants: tuple[RemoteSecretGrant, ...] = ()
    input_artifacts: tuple[RemoteInputArtifact, ...] = ()
    expected_outputs: tuple[str, ...] = ()
    lifecycle: RemoteLifecyclePolicy = "ephemeral_per_eval"
    environment: Mapping[str, str] = field(default_factory=dict)
    snapshot_id: str | None = None
    max_reuse_tasks: int = 1
    metadata: Mapping[str, str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.image.strip() or not self.command.strip():
            raise ValueError("remote task_id, image, and command must be non-empty")
        requirements = RemoteExecutionRequirements(
            image=self.image,
            resources=self.resources,
            region=self.region,
            required_telemetry=frozenset(self.required_telemetry),
        )
        object.__setattr__(self, "region", requirements.region)
        object.__setattr__(self, "required_telemetry", requirements.required_telemetry)
        if not math.isfinite(self.timeout_seconds) or self.timeout_seconds <= 0 or self.max_reuse_tasks < 1:
            raise ValueError("remote timeout must be positive and finite and reuse bound must be positive")
        if self.network_policy not in {"deny", "allow"}:
            raise ValueError(f"unknown remote network policy: {self.network_policy}")
        if self.secrets_policy not in {"deny", "scoped_grants"}:
            raise ValueError(f"unknown remote secrets policy: {self.secrets_policy}")
        if self.lifecycle not in {"ephemeral_per_eval", "reuse_matched_trials", "warm_snapshot"}:
            raise ValueError(f"unknown remote lifecycle policy: {self.lifecycle}")
        if self.secrets_policy == "deny" and self.secret_grants:
            raise ValueError("secret grants require secrets_policy='scoped_grants'")
        for grant in self.secret_grants:
            if grant.expires_at <= time.time():
                raise ValueError(f"secret grant is expired: {grant.name}")
        input_names = [artifact.name for artifact in self.input_artifacts]
        if len(input_names) != len(set(input_names)):
            raise ValueError("remote input artifact names must be unique")
        if len(self.expected_outputs) != len(set(self.expected_outputs)):
            raise ValueError("remote expected output artifact names must be unique")
        for name in self.expected_outputs:
            _validate_artifact_name(name)
        if self.lifecycle == "warm_snapshot" and not self.snapshot_id:
            raise ValueError("warm_snapshot lifecycle requires snapshot_id")
        if any(
            not isinstance(name, str)
            or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None
            or not isinstance(value, str)
            for name, value in self.environment.items()
        ):
            raise ValueError("remote environment names must be POSIX identifiers and values must be strings")
        if any(
            not isinstance(name, str) or not isinstance(value, str) or not name
            for name, value in self.metadata.items()
        ):
            raise ValueError("remote metadata names and values must be strings with non-empty names")
        _prepared_fixture_provenance(self.metadata)
        # The request is replay provenance. Retaining caller-owned dictionaries
        # would let another thread mutate the provider command or attestation
        # after validation but before dispatch.
        object.__setattr__(self, "environment", MappingProxyType(dict(self.environment)))
        object.__setattr__(self, "metadata", MappingProxyType(dict(self.metadata)))

    @property
    def requirements(self) -> RemoteExecutionRequirements:
        return RemoteExecutionRequirements(
            image=self.image,
            resources=self.resources,
            region=self.region,
            required_telemetry=self.required_telemetry,
        )


@dataclass(frozen=True, slots=True)
class RemoteExecutionEvent:
    sequence: int
    event_type: str
    message: str = ""
    fields: Mapping[str, object] = field(default_factory=dict)


@dataclass(frozen=True, slots=True)
class RemoteResourceUsage:
    wall_seconds: float = 0.0
    cpu_seconds: float | None = None
    peak_memory_mb: float | None = None
    accelerator_seconds: float | None = None
    accelerator_peak_memory_mb: float | None = None

    def __post_init__(self) -> None:
        values = (
            self.wall_seconds,
            self.cpu_seconds,
            self.peak_memory_mb,
            self.accelerator_seconds,
            self.accelerator_peak_memory_mb,
        )
        if any(value is not None and (not math.isfinite(value) or value < 0) for value in values):
            raise ValueError("remote resource usage must be non-negative and finite")


@dataclass(frozen=True, slots=True)
class RemoteInputProvenance:
    name: str
    sha256: str
    size_bytes: int
    media_type: str


@dataclass(frozen=True, slots=True)
class RemoteResolvedEnvironment:
    image: str = ""
    region: str = ""
    accelerator_kind: str = ""
    accelerator_count: int = 0
    runtime: str = ""


@dataclass(frozen=True, slots=True)
class RemoteExecutionProvenance:
    request_sha256: str = ""
    image: str = ""
    image_digest: str = ""
    package_sha256: str = ""
    inputs: tuple[RemoteInputProvenance, ...] = ()
    seed: int | None = None
    fixture_digest: str = ""
    fixture_state_sha256: str = ""
    fixture_observation_sha256: str = ""
    requested_region: str = ""
    requested_accelerator_kind: str = ""
    requested_accelerator_count: int = 0
    requested_accelerator_memory_gb: float | None = None
    required_telemetry: tuple[str, ...] = ()
    resolved: RemoteResolvedEnvironment = field(default_factory=RemoteResolvedEnvironment)


@dataclass(frozen=True, slots=True)
class RemoteCleanupOutcome:
    attempted: bool
    succeeded: bool
    resource_id: str = ""
    detail: str = ""


@dataclass(frozen=True, slots=True)
class ExternalEvalLedgerEntry:
    task_id: str
    provider: str
    status: RemoteExecutionStatus
    candidate_succeeded: bool
    infrastructure_succeeded: bool
    exit_code: int | None
    usage: RemoteResourceUsage
    cleanup: RemoteCleanupOutcome
    detail: str = ""
    provenance: RemoteExecutionProvenance = field(default_factory=RemoteExecutionProvenance)


@dataclass(frozen=True, slots=True)
class RemoteExecutionResult:
    task_id: str
    provider: str
    status: RemoteExecutionStatus
    stdout: str = ""
    stderr: str = ""
    exit_code: int | None = None
    artifacts: tuple[RemoteOutputArtifact, ...] = ()
    events: tuple[RemoteExecutionEvent, ...] = ()
    usage: RemoteResourceUsage = field(default_factory=RemoteResourceUsage)
    cleanup: RemoteCleanupOutcome = field(default_factory=lambda: RemoteCleanupOutcome(False, False))
    error: str = ""
    session_id: str = ""
    provenance: RemoteExecutionProvenance = field(default_factory=RemoteExecutionProvenance)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def artifact(self, name: str) -> RemoteOutputArtifact | None:
        return next((artifact for artifact in self.artifacts if artifact.name == name), None)

    def to_ledger_entry(self) -> ExternalEvalLedgerEntry:
        infrastructure_succeeded = self.status not in {
            "provider_error",
            "timeout",
            "artifact_error",
            "cleanup_error",
        } and not any(event.event_type == "provider_client_exit_error" for event in self.events)
        return ExternalEvalLedgerEntry(
            task_id=self.task_id,
            provider=self.provider,
            status=self.status,
            candidate_succeeded=self.status == "success",
            infrastructure_succeeded=infrastructure_succeeded,
            exit_code=self.exit_code,
            usage=self.usage,
            cleanup=self.cleanup,
            detail=self.error,
            provenance=self.provenance,
        )


class RemoteExecutionAdapter(Protocol):
    def execute_request(self, request: RemoteExecutionRequest) -> RemoteExecutionResult:
        """Execute one provider-neutral task request."""
        ...


ExternalEvalLedgerSink: TypeAlias = Callable[[ExternalEvalLedgerEntry], None]


def parse_remote_stdout(
    request: RemoteExecutionRequest,
    *,
    provider: str,
    stdout: str,
    stderr: str,
    exit_code: int,
    usage: RemoteResourceUsage,
    cleanup: RemoteCleanupOutcome,
    session_id: str,
) -> RemoteExecutionResult:
    """Parse optional NDJSON events and a final typed result envelope."""

    events: list[RemoteExecutionEvent] = []
    final_payload: Mapping[str, object] = {}
    provenance = remote_request_provenance(request)
    for line in stdout.splitlines():
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(parsed, Mapping):
            continue
        if parsed.get("type") == "event":
            events.append(
                RemoteExecutionEvent(
                    sequence=len(events) + 1,
                    event_type=str(parsed.get("event", "message")),
                    message=str(parsed.get("message", "")),
                    fields={str(key): value for key, value in parsed.items() if key not in {"type", "event", "message"}},
                )
            )
        else:
            final_payload = parsed
    if exit_code != 0:
        task_error = stderr.strip() or f"task exited with status {exit_code}"
        declared_bootstrap_exit = request.metadata.get("bootstrap_exit_code")
        bootstrap_failed = declared_bootstrap_exit is not None and str(exit_code) == str(declared_bootstrap_exit)
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status=("cleanup_error" if not cleanup.succeeded else "artifact_error" if bootstrap_failed else "task_error"),
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=_cleanup_failure_detail(task_error, cleanup) if not cleanup.succeeded else task_error,
            session_id=session_id,
            provenance=provenance,
        )

    try:
        artifacts = _parse_artifacts(final_payload.get("artifacts"))
    except (TypeError, ValueError) as exc:
        artifact_error = f"malformed output artifacts: {type(exc).__name__}: {exc}"
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status="cleanup_error" if not cleanup.succeeded else "artifact_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=_cleanup_failure_detail(artifact_error, cleanup) if not cleanup.succeeded else artifact_error,
            session_id=session_id,
            provenance=provenance,
        )
    names = {artifact.name for artifact in artifacts}
    missing = [name for name in request.expected_outputs if name not in names]
    if missing:
        artifact_error = f"missing declared output artifacts: {', '.join(missing)}"
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status="cleanup_error" if not cleanup.succeeded else "artifact_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifacts=artifacts,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=_cleanup_failure_detail(artifact_error, cleanup) if not cleanup.succeeded else artifact_error,
            session_id=session_id,
            provenance=provenance,
        )
    malformed_scenario = _malformed_scenario_output(request, final_payload)
    if malformed_scenario:
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status="cleanup_error" if not cleanup.succeeded else "artifact_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifacts=artifacts,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=(
                _cleanup_failure_detail(malformed_scenario, cleanup)
                if not cleanup.succeeded
                else malformed_scenario
            ),
            session_id=session_id,
            provenance=provenance,
        )
    status: RemoteExecutionStatus = "success" if cleanup.succeeded else "cleanup_error"
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider=provider,
        status=status,
        stdout=stdout,
        stderr=stderr,
        exit_code=exit_code,
        artifacts=artifacts,
        events=tuple(events),
        usage=usage,
        cleanup=cleanup,
        error="" if cleanup.succeeded else cleanup.detail,
        session_id=session_id,
        provenance=provenance,
    )


def remote_request_provenance(
    request: RemoteExecutionRequest,
    *,
    resolved: RemoteResolvedEnvironment | None = None,
) -> RemoteExecutionProvenance:
    """Derive immutable replay provenance from request contents, not provider output."""

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
    fixture_digest, fixture_state_sha256, fixture_observation_sha256 = _prepared_fixture_provenance(
        request.metadata
    )
    accelerator = request.resources.accelerator
    return RemoteExecutionProvenance(
        request_sha256=remote_request_sha256(request),
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
        requested_accelerator_memory_gb=(accelerator.memory_gb if accelerator is not None else None),
        required_telemetry=tuple(sorted(request.required_telemetry)),
        resolved=resolved or RemoteResolvedEnvironment(),
    )


def remote_request_sha256(request: RemoteExecutionRequest) -> str:
    """Hash the exact non-secret provider request and content-addressed inputs."""

    accelerator = request.resources.accelerator
    payload = {
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
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _prepared_fixture_provenance(metadata: Mapping[str, str]) -> tuple[str, str, str]:
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


def _malformed_scenario_output(request: RemoteExecutionRequest, payload: Mapping[str, object]) -> str:
    if request.metadata.get("task_kind") != "scenario_match":
        return ""
    result = payload.get("result")
    replay = payload.get("replay")
    if not isinstance(result, Mapping) or not isinstance(replay, Mapping):
        return "malformed scenario output: result and replay objects are required"
    score = result.get("score")
    if isinstance(score, bool) or not isinstance(score, (int, float)) or not math.isfinite(float(score)):
        return "malformed scenario output: result.score must be finite"
    if not isinstance(result.get("summary"), str):
        return "malformed scenario output: result.summary must be a string"
    if not isinstance(result.get("replay"), list):
        return "malformed scenario output: result.replay must be an array"
    if not isinstance(result.get("metrics"), Mapping) or not isinstance(result.get("validation_errors"), list):
        return "malformed scenario output: result metrics and validation_errors are required"
    replay_seed = replay.get("seed")
    if not isinstance(replay.get("scenario"), str) or isinstance(replay_seed, bool) or not isinstance(replay_seed, int):
        return "malformed scenario output: replay scenario and seed are required"
    if not isinstance(replay.get("narrative"), str) or not isinstance(replay.get("timeline"), list):
        return "malformed scenario output: replay narrative and timeline are required"
    expected_scenario = request.metadata.get("scenario")
    if expected_scenario is not None and replay.get("scenario") != expected_scenario:
        return "malformed scenario output: replay scenario provenance mismatch"
    expected_seed = request.metadata.get("seed")
    if expected_seed is not None and str(replay.get("seed")) != str(expected_seed):
        return "malformed scenario output: replay seed provenance mismatch"
    return ""


def _parse_artifacts(raw: object) -> tuple[RemoteOutputArtifact, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, Mapping):
        raise ValueError("artifacts must be an object")
    artifacts: list[RemoteOutputArtifact] = []
    for name, item in raw.items():
        if not isinstance(name, str):
            raise ValueError("artifact names must be strings")
        _validate_artifact_name(name)
        if isinstance(item, str):
            artifacts.append(RemoteOutputArtifact(name=name, content=item.encode("utf-8"), media_type="text/plain"))
            continue
        if isinstance(item, Mapping) and isinstance(item.get("base64"), str):
            try:
                content = base64.b64decode(item["base64"], validate=True)
            except (binascii.Error, ValueError) as exc:
                raise ValueError(f"artifact has invalid base64 content: {name}") from exc
            media_type = item.get("media_type", "application/octet-stream")
            if not isinstance(media_type, str):
                raise ValueError(f"artifact media_type must be a string: {name}")
            artifacts.append(
                RemoteOutputArtifact(
                    name=name,
                    content=content,
                    media_type=media_type,
                )
            )
            continue
        raise ValueError(f"artifact value must be text or a base64 envelope: {name}")
    return tuple(artifacts)


def _cleanup_failure_detail(primary_error: str, cleanup: RemoteCleanupOutcome) -> str:
    cleanup_error = cleanup.detail.strip() or "remote resource cleanup failed"
    return f"{primary_error}; cleanup failed: {cleanup_error}" if primary_error else cleanup_error


def requests_are_reuse_compatible(requests: Sequence[RemoteExecutionRequest]) -> bool:
    """Return whether every request can safely share one provisioned sandbox.

    Prime-style reusable sessions are created from the first request.  Every
    value that can leave filesystem, credential, environment, or snapshot state
    behind must therefore be identical across the cohort. Commands, task ids,
    output declarations, timeouts, and opaque metadata may differ because they
    do not alter the provisioned sandbox contract.
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


def _validate_artifact_name(name: str) -> None:
    if not isinstance(name, str) or not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"artifact path must stay relative to the task root: {name!r}")


__all__ = [
    "ExternalEvalLedgerEntry",
    "ExternalEvalLedgerSink",
    "RemoteAcceleratorRequest",
    "RemoteCleanupOutcome",
    "RemoteExecutionAdapter",
    "RemoteExecutionEvent",
    "RemoteExecutionRequirements",
    "RemoteExecutionProvenance",
    "RemoteExecutionRequest",
    "RemoteExecutionResult",
    "RemoteExecutionStatus",
    "RemoteInputArtifact",
    "RemoteInputProvenance",
    "RemoteLifecyclePolicy",
    "RemoteNetworkPolicy",
    "RemoteOutputArtifact",
    "RemoteProviderCapabilities",
    "RemoteResolvedEnvironment",
    "RemoteResourceRequest",
    "RemoteResourceUsage",
    "RemoteSecretGrant",
    "RemoteSecretsPolicy",
    "RemoteTelemetryKind",
    "parse_remote_stdout",
    "remote_request_provenance",
    "remote_request_sha256",
    "requests_are_reuse_compatible",
]
