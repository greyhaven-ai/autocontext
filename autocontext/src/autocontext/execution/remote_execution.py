"""Provider-neutral remote execution session contract (AC-978)."""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
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


@dataclass(frozen=True, slots=True)
class RemoteAcceleratorRequest:
    kind: str
    count: int = 1
    memory_gb: float | None = None

    def __post_init__(self) -> None:
        if not self.kind.strip() or self.count < 1:
            raise ValueError("accelerator kind must be non-empty and count must be positive")


@dataclass(frozen=True, slots=True)
class RemoteResourceRequest:
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_gb: float = 5.0
    accelerator: RemoteAcceleratorRequest | None = None

    def __post_init__(self) -> None:
        if self.cpu_cores <= 0 or self.memory_gb <= 0 or self.disk_gb <= 0:
            raise ValueError("remote CPU, memory, and disk requests must be positive")


@dataclass(frozen=True, slots=True)
class RemoteInputArtifact:
    name: str
    content: bytes
    media_type: str = "application/octet-stream"

    def __post_init__(self) -> None:
        _validate_artifact_name(self.name)


@dataclass(frozen=True, slots=True)
class RemoteOutputArtifact:
    name: str
    content: bytes
    media_type: str = "application/octet-stream"


@dataclass(frozen=True, slots=True)
class RemoteSecretGrant:
    """Opaque host-plane reference; never carries a secret value."""

    name: str
    grant_id: str
    expires_at: float

    def __post_init__(self) -> None:
        if not self.name.strip() or not self.grant_id.strip():
            raise ValueError("secret grant name and id must be non-empty")


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

    def __post_init__(self) -> None:
        if not self.task_id.strip() or not self.image.strip() or not self.command.strip():
            raise ValueError("remote task_id, image, and command must be non-empty")
        if self.timeout_seconds <= 0 or self.max_reuse_tasks < 1:
            raise ValueError("remote timeout and reuse bound must be positive")
        if self.secrets_policy == "deny" and self.secret_grants:
            raise ValueError("secret grants require secrets_policy='scoped_grants'")
        for grant in self.secret_grants:
            if grant.expires_at <= time.time():
                raise ValueError(f"secret grant is expired: {grant.name}")
        for name in self.expected_outputs:
            _validate_artifact_name(name)
        if self.lifecycle == "warm_snapshot" and not self.snapshot_id:
            raise ValueError("warm_snapshot lifecycle requires snapshot_id")


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

    @property
    def succeeded(self) -> bool:
        return self.status == "success"

    def artifact(self, name: str) -> RemoteOutputArtifact | None:
        return next((artifact for artifact in self.artifacts if artifact.name == name), None)

    def to_ledger_entry(self) -> ExternalEvalLedgerEntry:
        infrastructure_succeeded = self.status not in {"provider_error", "timeout", "cleanup_error"}
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
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status="task_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=stderr.strip() or f"task exited with status {exit_code}",
            session_id=session_id,
        )

    artifacts = _parse_artifacts(final_payload.get("artifacts"))
    names = {artifact.name for artifact in artifacts}
    missing = [name for name in request.expected_outputs if name not in names]
    if missing:
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider=provider,
            status="artifact_error",
            stdout=stdout,
            stderr=stderr,
            exit_code=exit_code,
            artifacts=artifacts,
            events=tuple(events),
            usage=usage,
            cleanup=cleanup,
            error=f"missing declared output artifacts: {', '.join(missing)}",
            session_id=session_id,
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
    )


def _parse_artifacts(raw: object) -> tuple[RemoteOutputArtifact, ...]:
    if not isinstance(raw, Mapping):
        return ()
    artifacts: list[RemoteOutputArtifact] = []
    for name, item in raw.items():
        if not isinstance(name, str):
            continue
        _validate_artifact_name(name)
        if isinstance(item, str):
            artifacts.append(RemoteOutputArtifact(name=name, content=item.encode("utf-8"), media_type="text/plain"))
            continue
        if isinstance(item, Mapping) and isinstance(item.get("base64"), str):
            try:
                content = base64.b64decode(item["base64"], validate=True)
            except ValueError:
                continue
            artifacts.append(
                RemoteOutputArtifact(
                    name=name,
                    content=content,
                    media_type=str(item.get("media_type", "application/octet-stream")),
                )
            )
    return tuple(artifacts)


def requests_are_reuse_compatible(requests: Sequence[RemoteExecutionRequest]) -> bool:
    if not requests:
        return False
    first = requests[0]
    return all(
        request.lifecycle == "reuse_matched_trials"
        and request.image == first.image
        and request.resources == first.resources
        and request.network_policy == first.network_policy
        and request.secrets_policy == first.secrets_policy
        for request in requests
    ) and len(requests) <= min(request.max_reuse_tasks for request in requests)


def _validate_artifact_name(name: str) -> None:
    if not name or name.startswith("/") or ".." in name.split("/"):
        raise ValueError(f"artifact path must stay relative to the task root: {name!r}")


__all__ = [
    "ExternalEvalLedgerEntry",
    "ExternalEvalLedgerSink",
    "RemoteAcceleratorRequest",
    "RemoteCleanupOutcome",
    "RemoteExecutionAdapter",
    "RemoteExecutionEvent",
    "RemoteExecutionRequest",
    "RemoteExecutionResult",
    "RemoteExecutionStatus",
    "RemoteInputArtifact",
    "RemoteLifecyclePolicy",
    "RemoteNetworkPolicy",
    "RemoteOutputArtifact",
    "RemoteResourceRequest",
    "RemoteResourceUsage",
    "RemoteSecretGrant",
    "RemoteSecretsPolicy",
    "parse_remote_stdout",
    "requests_are_reuse_compatible",
]
