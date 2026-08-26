"""Provider-neutral remote execution session contract (AC-978)."""

from __future__ import annotations

import base64
import binascii
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import Any, Literal, Protocol, TypeAlias

import autocontext.execution._immutable_json as _immutable
import autocontext.execution._remote_execution_identity as _request_identity
import autocontext.execution._remote_execution_validation as _validation
from autocontext.execution.scenario_remote_validation import malformed_scenario_output
from autocontext.runtime_images import require_pinned_runtime_image

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
_REMOTE_TELEMETRY_KINDS = frozenset({"hardware_identity", "accelerator_usage", "accelerator_peak_memory"})


def _normalized_request_float(value: object, *, label: str) -> float:
    """Return one canonical finite float for a request-identity number."""
    return _validation.normalized_exact_float(value, label=label, allow_subclasses=True)


@dataclass(frozen=True, slots=True)
class RemoteAcceleratorRequest:
    kind: str
    count: int = 1
    memory_gb: float | None = None

    def __post_init__(self) -> None:
        if type(self.kind) is not str:
            raise TypeError("accelerator kind must be a string")
        if type(self.count) is not int:
            raise TypeError("accelerator count must be an integer")
        if not self.kind.strip() or self.count < 1:
            raise ValueError("accelerator kind must be non-empty and count must be positive")
        if self.memory_gb is not None:
            memory_gb = _normalized_request_float(self.memory_gb, label="accelerator memory")
            if memory_gb <= 0:
                raise ValueError("accelerator memory must be positive and finite when supplied")
            object.__setattr__(self, "memory_gb", memory_gb)


@dataclass(frozen=True, slots=True)
class RemoteResourceRequest:
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_gb: float = 5.0
    accelerator: RemoteAcceleratorRequest | None = None

    def __post_init__(self) -> None:
        if self.accelerator is not None and type(self.accelerator) is not RemoteAcceleratorRequest:
            raise TypeError("remote accelerator request must be a RemoteAcceleratorRequest")
        cpu_cores = _normalized_request_float(self.cpu_cores, label="remote CPU request")
        memory_gb = _normalized_request_float(self.memory_gb, label="remote memory request")
        disk_gb = _normalized_request_float(self.disk_gb, label="remote disk request")
        if any(value <= 0 for value in (cpu_cores, memory_gb, disk_gb)):
            raise ValueError("remote CPU, memory, and disk requests must be positive and finite")
        object.__setattr__(self, "cpu_cores", cpu_cores)
        object.__setattr__(self, "memory_gb", memory_gb)
        object.__setattr__(self, "disk_gb", disk_gb)


@dataclass(frozen=True, slots=True)
class RemoteExecutionRequirements:
    """Provider-neutral, identity-bound placement and telemetry requirements."""

    image: str
    resources: RemoteResourceRequest = field(default_factory=RemoteResourceRequest)
    region: str | None = None
    required_telemetry: frozenset[RemoteTelemetryKind] = frozenset()

    def __post_init__(self) -> None:
        if type(self.image) is not str:
            raise TypeError("remote execution image must be a string")
        if type(self.resources) is not RemoteResourceRequest:
            raise TypeError("remote execution resources must be a RemoteResourceRequest")
        if not self.image.strip():
            raise ValueError("remote execution image must be non-empty")
        if self.resources.accelerator is not None:
            require_pinned_runtime_image(self.image)
        if self.region is not None:
            if type(self.region) is not str:
                raise TypeError("remote execution region must be a string when supplied")
            if not self.region.strip():
                raise ValueError("remote execution region must be non-empty when supplied")
        telemetry = frozenset(self.required_telemetry)
        if any(type(value) is not str for value in telemetry):
            raise TypeError("remote telemetry requirements must be strings")
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
            return f"accelerator count {accelerator.count} exceeds the configured {accelerator.kind!r} provider limit of {limit}"
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
        if type(self.content) is not bytes:
            raise TypeError("remote input artifact content must be bytes")
        if type(self.media_type) is not str or not self.media_type.strip():
            raise ValueError("remote input artifact media_type must be non-empty")


@dataclass(frozen=True, slots=True)
class RemoteOutputArtifact:
    name: str
    content: bytes
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        _validate_artifact_name(self.name)
        if type(self.content) is not bytes:
            raise TypeError("remote output artifact content must be bytes")
        if type(self.media_type) is not str or not self.media_type.strip():
            raise ValueError("remote output artifact media_type must be non-empty")


@dataclass(frozen=True, slots=True)
class RemoteSecretGrant:
    """Opaque host-plane reference; never carries a secret value."""

    name: str
    grant_id: str
    expires_at: float

    def __post_init__(self) -> None:
        if type(self.name) is not str or type(self.grant_id) is not str:
            raise TypeError("secret grant name and id must be strings")
        if not self.name.strip() or not self.grant_id.strip():
            raise ValueError("secret grant name and id must be non-empty")
        expires_at = _normalized_request_float(self.expires_at, label="secret grant expiry")
        object.__setattr__(self, "expires_at", expires_at)


@dataclass(frozen=True, slots=True)
class RemoteExecutionRequest:
    task_id: str
    image: str
    command: str
    resources: RemoteResourceRequest = field(default_factory=RemoteResourceRequest)
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
    region: str | None = None
    required_telemetry: frozenset[RemoteTelemetryKind] = frozenset()
    strict_task_identity: bool = False

    def __post_init__(self) -> None:
        if type(self) is not RemoteExecutionRequest:
            raise TypeError("remote request must be a RemoteExecutionRequest")
        if not all(type(value) is str for value in (self.task_id, self.image, self.command)):
            raise TypeError("remote task_id, image, and command must be strings")
        if not self.task_id.strip() or not self.image.strip() or not self.command.strip():
            raise ValueError("remote task_id, image, and command must be non-empty")
        if type(self.resources) is not RemoteResourceRequest:
            raise TypeError("remote resources must be a RemoteResourceRequest")
        if not isinstance(self.environment, Mapping) or not isinstance(self.metadata, Mapping):
            raise TypeError("remote environment and metadata must be mappings")
        # Snapshot caller-controlled mappings once, before validation.  Using
        # ``items()`` for validation and ``dict(...)`` later would permit a
        # mutable or adversarial Mapping to substitute unvalidated identity
        # content between the two traversals.
        environment = dict(self.environment)
        metadata = dict(self.metadata)
        secret_grants = _immutable.validated_tuple(self.secret_grants, label="remote secret_grants", item_type=RemoteSecretGrant)
        input_artifacts = _immutable.validated_tuple(
            self.input_artifacts, label="remote input_artifacts", item_type=RemoteInputArtifact
        )
        expected_outputs = _immutable.validated_tuple(self.expected_outputs, label="remote expected_outputs", item_type=str)
        # A frozen dataclass is not immutable if it retains caller-owned lists.
        # Snapshot identity-bearing sequences before any validation or hashing.
        object.__setattr__(self, "secret_grants", secret_grants)
        object.__setattr__(self, "input_artifacts", input_artifacts)
        object.__setattr__(self, "expected_outputs", expected_outputs)
        requirements = RemoteExecutionRequirements(
            image=self.image,
            resources=self.resources,
            region=self.region,
            required_telemetry=frozenset(self.required_telemetry),
        )
        object.__setattr__(self, "region", requirements.region)
        object.__setattr__(self, "required_telemetry", requirements.required_telemetry)
        timeout_seconds = _normalized_request_float(self.timeout_seconds, label="remote timeout")
        if type(self.max_reuse_tasks) is not int:
            raise TypeError("remote reuse bound must be an integer")
        if type(self.strict_task_identity) is not bool:
            raise TypeError("remote strict_task_identity must be boolean")
        if timeout_seconds <= 0 or self.max_reuse_tasks < 1:
            raise ValueError("remote timeout must be positive and finite and reuse bound must be positive")
        object.__setattr__(self, "timeout_seconds", timeout_seconds)
        if type(self.network_policy) is not str:
            raise TypeError("remote network policy must be a string")
        if self.network_policy not in {"deny", "allow"}:
            raise ValueError(f"unknown remote network policy: {self.network_policy}")
        if type(self.secrets_policy) is not str:
            raise TypeError("remote secrets policy must be a string")
        if self.secrets_policy not in {"deny", "scoped_grants"}:
            raise ValueError(f"unknown remote secrets policy: {self.secrets_policy}")
        if type(self.lifecycle) is not str:
            raise TypeError("remote lifecycle policy must be a string")
        if self.lifecycle not in {"ephemeral_per_eval", "reuse_matched_trials", "warm_snapshot"}:
            raise ValueError(f"unknown remote lifecycle policy: {self.lifecycle}")
        if self.secrets_policy == "deny" and self.secret_grants:
            raise ValueError("secret grants require secrets_policy='scoped_grants'")
        # Grant expiry is mutable dispatch policy, not immutable request shape.
        # Keeping expired opaque references reconstructible is required to
        # replay a previously committed paid result after process restart.
        # Provider adapters must reject them immediately before new dispatch.
        input_names = [artifact.name for artifact in self.input_artifacts]
        if len(input_names) != len(set(input_names)):
            raise ValueError("remote input artifact names must be unique")
        if len(self.expected_outputs) != len(set(self.expected_outputs)):
            raise ValueError("remote expected output artifact names must be unique")
        for name in self.expected_outputs:
            _validate_artifact_name(name)
        if self.snapshot_id is not None and type(self.snapshot_id) is not str:
            raise TypeError("remote snapshot_id must be a string when supplied")
        if type(self.snapshot_id) is str and not self.snapshot_id.strip():
            raise ValueError("remote snapshot_id must be non-empty when supplied")
        if self.lifecycle == "warm_snapshot" and not self.snapshot_id:
            raise ValueError("warm_snapshot lifecycle requires snapshot_id")
        if any(
            type(name) is not str or re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name) is None or type(value) is not str
            for name, value in environment.items()
        ):
            raise ValueError("remote environment names must be POSIX identifiers and values must be strings")
        if any(type(name) is not str or type(value) is not str or not name for name, value in metadata.items()):
            raise ValueError("remote metadata names and values must be strings with non-empty names")
        _prepared_fixture_provenance(metadata)
        # The request is replay provenance. Retaining caller-owned dictionaries
        # would let another thread mutate the provider command or attestation
        # after validation but before dispatch.
        object.__setattr__(self, "environment", MappingProxyType(environment))
        object.__setattr__(self, "metadata", MappingProxyType(metadata))

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
    fields: Mapping[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if type(self.sequence) is not int:
            raise TypeError("remote event sequence must be an integer")
        if self.sequence < 0:
            raise ValueError("remote event sequence must be non-negative")
        if type(self.event_type) is not str or type(self.message) is not str:
            raise TypeError("remote event type and message must be strings")
        if not self.event_type.strip():
            raise ValueError("remote event type must be non-empty")
        object.__setattr__(self, "fields", _immutable.freeze_json_object(self.fields, label="remote event fields"))


@dataclass(frozen=True, slots=True)
class RemoteResourceUsage:
    wall_seconds: float = 0.0
    cpu_seconds: float | None = None
    peak_memory_mb: float | None = None
    accelerator_seconds: float | None = None
    accelerator_peak_memory_mb: float | None = None

    def __post_init__(self) -> None:
        _validation.normalize_resource_usage(self)


@dataclass(frozen=True, slots=True)
class RemoteInputProvenance:
    name: str
    sha256: str
    size_bytes: int
    media_type: str

    def __post_init__(self) -> None:
        _validation.validate_input_provenance(self)


@dataclass(frozen=True, slots=True)
class RemoteResolvedEnvironment:
    image: str = ""
    region: str = ""
    accelerator_kind: str = ""
    accelerator_count: int = 0
    runtime: str = ""

    def __post_init__(self) -> None:
        _validation.validate_resolved_environment(self)


@dataclass(frozen=True, slots=True)
class RemoteExecutionProvenance:
    image: str = ""
    image_digest: str = ""
    package_sha256: str = ""
    inputs: tuple[RemoteInputProvenance, ...] = ()
    seed: int | None = None
    fixture_digest: str = ""
    fixture_state_sha256: str = ""
    fixture_observation_sha256: str = ""
    request_sha256: str = ""
    requested_region: str = ""
    requested_accelerator_kind: str = ""
    requested_accelerator_count: int = 0
    requested_accelerator_memory_gb: float | None = None
    required_telemetry: tuple[str, ...] = ()
    resolved: RemoteResolvedEnvironment = field(default_factory=RemoteResolvedEnvironment)

    def __post_init__(self) -> None:
        inputs = _immutable.validated_tuple(self.inputs, label="remote provenance inputs", item_type=RemoteInputProvenance)
        telemetry = _immutable.validated_tuple(
            self.required_telemetry,
            label="remote provenance required_telemetry",
            item_type=str,
        )
        if type(self.resolved) is not RemoteResolvedEnvironment:
            raise TypeError("remote provenance resolved must be a RemoteResolvedEnvironment")
        object.__setattr__(self, "inputs", inputs)
        object.__setattr__(self, "required_telemetry", telemetry)
        _validation.validate_execution_provenance(self)


@dataclass(frozen=True, slots=True)
class RemoteCleanupOutcome:
    attempted: bool
    succeeded: bool
    resource_id: str = ""
    detail: str = ""

    def __post_init__(self) -> None:
        _validation.validate_cleanup_outcome(self)


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
    retryable: bool = False
    attempt_id: str = ""

    def __post_init__(self) -> None:
        if type(self) is not ExternalEvalLedgerEntry:
            raise TypeError("external evaluation ledger must be an ExternalEvalLedgerEntry")
        if type(self.usage) is not RemoteResourceUsage:
            raise TypeError("external evaluation ledger usage must be RemoteResourceUsage")
        if type(self.cleanup) is not RemoteCleanupOutcome:
            raise TypeError("external evaluation ledger cleanup must be RemoteCleanupOutcome")
        if type(self.provenance) is not RemoteExecutionProvenance:
            raise TypeError("external evaluation ledger provenance must be RemoteExecutionProvenance")
        _validation.validate_ledger_entry(self)


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
    retryable: bool = False

    def __post_init__(self) -> None:
        if type(self) is not RemoteExecutionResult:
            raise TypeError("remote result must be a RemoteExecutionResult")
        artifacts = _immutable.validated_tuple(
            self.artifacts,
            label="remote result artifacts",
            item_type=RemoteOutputArtifact,
        )
        events = _immutable.validated_tuple(
            self.events,
            label="remote result events",
            item_type=RemoteExecutionEvent,
        )
        object.__setattr__(self, "artifacts", artifacts)
        object.__setattr__(self, "events", events)
        if type(self.usage) is not RemoteResourceUsage:
            raise TypeError("remote result usage must be RemoteResourceUsage")
        if type(self.cleanup) is not RemoteCleanupOutcome:
            raise TypeError("remote result cleanup must be RemoteCleanupOutcome")
        if type(self.provenance) is not RemoteExecutionProvenance:
            raise TypeError("remote result provenance must be RemoteExecutionProvenance")
        _validation.validate_execution_result(self)

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def artifact(self, name: str) -> RemoteOutputArtifact | None:
        return next((artifact for artifact in self.artifacts if artifact.name == name), None)

    def to_ledger_entry(self, *, attempt_id: str = "") -> ExternalEvalLedgerEntry:
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
            retryable=self.retryable,
            attempt_id=attempt_id,
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
            event_type = str(parsed.get("event", "message")).strip() or "message"
            events.append(
                RemoteExecutionEvent(
                    sequence=len(events) + 1,
                    event_type=event_type,
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
    malformed_scenario = malformed_scenario_output(request.metadata, final_payload)
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
            error=(_cleanup_failure_detail(malformed_scenario, cleanup) if not cleanup.succeeded else malformed_scenario),
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
    _require_exact_remote_request(request)
    return _request_identity.build_request_provenance(request, resolved=resolved)


def _remote_request_identity_payload(request: RemoteExecutionRequest) -> dict[str, Any]:
    _require_exact_remote_request(request)
    return _request_identity.request_identity_payload(request)


def _remote_request_identity_payload_sha256(payload: Mapping[str, object]) -> str:
    return _request_identity.request_identity_payload_sha256(payload)


def remote_request_sha256(request: RemoteExecutionRequest) -> str:
    """Hash the exact non-secret provider request and content-addressed inputs."""

    _require_exact_remote_request(request)
    return _request_identity.request_sha256(request)


def _prepared_fixture_provenance(metadata: Mapping[str, str]) -> tuple[str, str, str]:
    return _request_identity.prepared_fixture_provenance(metadata)


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
    """Return whether every request can safely share one provisioned sandbox."""
    return _request_identity.requests_are_reuse_compatible(requests)


def _validate_artifact_name(name: str) -> None:
    if type(name) is not str or not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"artifact path must stay relative to the task root: {name!r}")


def _require_exact_remote_request(request: object) -> None:
    if type(request) is not RemoteExecutionRequest:
        raise TypeError("remote request must be a RemoteExecutionRequest")


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
