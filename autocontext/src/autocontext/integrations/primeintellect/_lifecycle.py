"""Internal Prime sandbox lifecycle state and terminal-result builders."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field, replace

from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteResourceUsage,
    remote_request_provenance,
)


@dataclass(slots=True)
class ActiveSandbox:
    task_id: str
    sandbox_id: str
    cleanup_started: bool = False
    cleanup_outcome: RemoteCleanupOutcome | None = None
    cleanup_done: threading.Event = field(default_factory=threading.Event)


class RetryableProvisioningError(RuntimeError):
    """A pre-dispatch provider failure whose sandbox was verified deleted."""

    def __init__(
        self,
        error: Exception,
        *,
        cleanup: RemoteCleanupOutcome,
        usage: RemoteResourceUsage,
        session_id: str,
        lifecycle_event: RemoteExecutionEvent,
    ) -> None:
        super().__init__(f"{type(error).__name__}: {error}")
        self.cleanup = cleanup
        self.usage = usage
        self.session_id = session_id
        self.lifecycle_event = lifecycle_event


class SandboxCreationOutcomeUnknown(RuntimeError):
    """The provider may have created a sandbox without returning its id."""

    def __init__(self, detail: str, *, client_exit_error: Exception | None = None) -> None:
        super().__init__(detail)
        self.client_exit_error = client_exit_error


def ambiguous_creation_result(
    request: RemoteExecutionRequest,
    error: SandboxCreationOutcomeUnknown,
) -> RemoteExecutionResult:
    """Build the terminal result for a create call with no trustworthy resource ID."""

    cleanup = RemoteCleanupOutcome(
        attempted=False,
        succeeded=False,
        detail="sandbox creation outcome is unknown; no provider resource id was returned",
    )
    events = [
        RemoteExecutionEvent(
            sequence=1,
            event_type="provider_error",
            message=str(error),
            fields={"phase": "sandbox_creation", "attempts": 1},
        )
    ]
    if error.client_exit_error is not None:
        events.append(
            RemoteExecutionEvent(
                sequence=2,
                event_type="provider_client_exit_error",
                message=str(error.client_exit_error),
                fields={"phase": "sandbox_creation"},
            )
        )
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider="primeintellect",
        status="cleanup_error",
        cleanup=cleanup,
        error=cleanup_failure_detail(str(error), cleanup),
        provenance=remote_request_provenance(request),
        events=tuple(events),
    )


def cleanup_failure_detail(primary_error: str, cleanup: RemoteCleanupOutcome) -> str:
    cleanup_error = cleanup.detail.strip() or "remote resource cleanup failed"
    return f"{primary_error}; cleanup failed: {cleanup_error}" if primary_error else cleanup_error


def terminal_cleanup_error(
    request: RemoteExecutionRequest,
    *,
    cleanup: RemoteCleanupOutcome,
    usage: RemoteResourceUsage,
    session_id: str,
    primary_error: str,
    lifecycle_event: RemoteExecutionEvent,
) -> RemoteExecutionResult:
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider="primeintellect",
        status="cleanup_error",
        usage=usage,
        cleanup=cleanup,
        error=cleanup_failure_detail(primary_error, cleanup),
        session_id=session_id,
        events=(lifecycle_event,),
        provenance=remote_request_provenance(request),
    )


def provider_error_result(
    request: RemoteExecutionRequest,
    detail: str,
    cleanup: RemoteCleanupOutcome,
    usage: RemoteResourceUsage,
    session_id: str,
    attempts: int,
    lifecycle_event: RemoteExecutionEvent,
) -> RemoteExecutionResult:
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider="primeintellect",
        status="provider_error",
        usage=usage,
        cleanup=cleanup,
        error=detail,
        session_id=session_id,
        events=(
            lifecycle_event,
            RemoteExecutionEvent(
                sequence=lifecycle_event.sequence + 1,
                event_type="provider_error",
                message=detail,
                fields={"phase": "provisioning", "attempts": attempts},
            ),
        ),
        provenance=remote_request_provenance(request),
        retryable=True,
    )


def client_exit_error_result(
    parsed: RemoteExecutionResult,
    error: Exception | None,
    lifecycle_event: RemoteExecutionEvent,
) -> RemoteExecutionResult:
    """Attach SDK shutdown failure without erasing the primary outcome."""

    if error is None:
        return parsed
    detail = f"Prime Intellect client context exit failed: {type(error).__name__}: {error}"
    events = parsed.events if lifecycle_event in parsed.events else (lifecycle_event, *parsed.events)
    combined_error = f"{parsed.error}; {detail}" if parsed.error else detail
    return replace(
        parsed,
        error=combined_error,
        events=(
            *events,
            RemoteExecutionEvent(
                sequence=len(events) + 1,
                event_type="provider_client_exit_error",
                message=detail,
                fields={"session_id": parsed.session_id},
            ),
        ),
    )


__all__ = [
    "ActiveSandbox",
    "RetryableProvisioningError",
    "SandboxCreationOutcomeUnknown",
    "ambiguous_creation_result",
    "client_exit_error_result",
    "cleanup_failure_detail",
    "provider_error_result",
    "terminal_cleanup_error",
]
