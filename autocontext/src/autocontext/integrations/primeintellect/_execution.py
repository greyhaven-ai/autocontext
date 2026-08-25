"""Retry orchestration and attempt identity for Prime remote execution."""

from __future__ import annotations

import asyncio
import hashlib
import logging
import threading
import time
from collections.abc import Sequence
from dataclasses import replace
from typing import TYPE_CHECKING, Protocol

from autocontext.execution.remote_execution import (
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteResourceUsage,
    remote_request_provenance,
    remote_request_sha256,
)
from autocontext.execution.remote_failure import RemoteExecutionAccountingError
from autocontext.integrations.primeintellect import _lifecycle

if TYPE_CHECKING:
    from autocontext.execution.external_eval_outbox import ExternalEvalLedgerOutbox, ExternalEvalOutboxClaim

logger = logging.getLogger(__name__)


class PrimeExecutionClient(Protocol):
    """Narrow client surface needed by the retry coordinator."""

    ledger_outbox: ExternalEvalLedgerOutbox | None
    _active_lock: threading.RLock
    _result_committed: set[str]

    def _prepare_dispatch(self, request: RemoteExecutionRequest) -> None: ...

    def _is_cancellation_requested(self, task_id: str) -> bool: ...

    def _canceled_result(
        self,
        request: RemoteExecutionRequest,
        detail: str = "",
        *,
        completed: RemoteExecutionResult | None = None,
    ) -> RemoteExecutionResult: ...

    def _commit_and_emit(
        self,
        request: RemoteExecutionRequest,
        result: RemoteExecutionResult,
        *,
        provider_wall_seconds: float,
        retry_events: Sequence[RemoteExecutionEvent] = (),
    ) -> RemoteExecutionResult: ...

    def _deliver_committed_ledger(
        self,
        attempt_id: str,
        *,
        already_delivered: bool,
    ) -> None: ...

    async def _execute_request_once(
        self,
        request: RemoteExecutionRequest,
        *,
        attempt_number: int,
    ) -> RemoteExecutionResult: ...


def execute_with_retries(
    client: PrimeExecutionClient,
    request: RemoteExecutionRequest,
    *,
    max_retries: int,
    backoff_seconds: float,
    passthrough_error: type[Exception] | tuple[type[Exception], ...],
) -> RemoteExecutionResult:
    """Execute one claimed request without retrying ambiguous paid work."""

    if client.ledger_outbox is not None:
        replay = _replay_outbox(client, request)
        if replay is not None:
            return _deliver_replayed_result(client, request, replay)

    # These mutable gates apply only to a new provider dispatch. In particular,
    # an already-committed paid result remains replayable if the SDK is later
    # removed, offline mode is enabled, or a capability/secret policy changes.
    try:
        client._prepare_dispatch(request)
    except Exception:
        if client.ledger_outbox is not None:
            # replay() and mutable preflight cannot be one SQLite transaction:
            # another process may commit this request after the first read but
            # before a local dispatch gate fails. Give that durable completion
            # precedence instead of surfacing a stale offline/SDK/policy error.
            replay = _replay_outbox(client, request)
            if replay is not None:
                return _deliver_replayed_result(client, request, replay)
        raise
    if client.ledger_outbox is not None:
        # claim() rechecks under an immediate SQLite transaction. Another
        # process may have completed this request after replay() returned None.
        claim = _claim_outbox(client, request)
        if claim.result is not None:
            return _deliver_replayed_result(client, request, claim)

    attempt = 0
    provider_wall_seconds = 0.0
    retry_events: list[RemoteExecutionEvent] = []
    last_retry_failure: _lifecycle.RetryableProvisioningError | None = None
    retry_usage = RemoteResourceUsage()
    while True:
        if client._is_cancellation_requested(request.task_id):
            completed = None
            if last_retry_failure is not None:
                completed = _lifecycle.provider_error_result(
                    request,
                    detail=str(last_retry_failure),
                    cleanup=last_retry_failure.cleanup,
                    usage=retry_usage,
                    session_id=last_retry_failure.session_id,
                    attempts=attempt,
                    lifecycle_event=last_retry_failure.lifecycle_event,
                )
            result = client._canceled_result(request, completed=completed)
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        # Recheck every mutable gate immediately before each claimed provider
        # attempt. The first preflight kept ordinary invalid requests out of the
        # journal; this closes the preflight/claim/create race. Later checks also
        # catch offline mode, SDK availability, or grants changing in backoff.
        try:
            client._prepare_dispatch(request)
        except Exception as exc:
            result = _dispatch_preflight_error_result(
                request,
                exc,
                previous=last_retry_failure,
                completed_attempts=attempt,
                accumulated_usage=retry_usage,
            )
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        attempt_started = time.perf_counter()
        try:
            result = asyncio.run(client._execute_request_once(request, attempt_number=attempt + 1))
        except passthrough_error as exc:
            # The optional SDK is checked before claim, but _execute_request_once
            # resolves it again at the provider boundary. If it disappears in
            # that narrow post-claim window, record a known pre-dispatch failure
            # instead of abandoning an unresolved durable claim.
            provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
            result = _dispatch_preflight_error_result(
                request,
                exc,
                previous=last_retry_failure,
                completed_attempts=attempt,
                accumulated_usage=retry_usage,
            )
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        except _lifecycle.SandboxCreationOutcomeUnknown as exc:
            provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
            result = with_provider_attempt(
                _lifecycle.ambiguous_creation_result(request, exc),
                request,
                attempt + 1,
            )
            result = replace(result, usage=_accumulate_usage(retry_usage, result.usage))
            if client._is_cancellation_requested(request.task_id):
                result = client._canceled_result(request, str(exc), completed=result)
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        except _lifecycle.RetryableProvisioningError as exc:
            provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
            last_retry_failure = exc
            retry_usage = _accumulate_usage(retry_usage, exc.usage)
            if client._is_cancellation_requested(request.task_id):
                completed = _lifecycle.provider_error_result(
                    request,
                    detail=str(exc),
                    cleanup=exc.cleanup,
                    usage=retry_usage,
                    session_id=exc.session_id,
                    attempts=attempt + 1,
                    lifecycle_event=exc.lifecycle_event,
                )
                result = client._canceled_result(request, str(exc), completed=completed)
                return client._commit_and_emit(
                    request,
                    result,
                    provider_wall_seconds=provider_wall_seconds,
                    retry_events=retry_events,
                )
            attempt += 1
            if attempt <= max_retries:
                retry_events.append(
                    RemoteExecutionEvent(
                        sequence=len(retry_events) + 1,
                        event_type="provider_retry",
                        message=str(exc),
                        fields={
                            "provider_attempt": attempt,
                            "provider_attempt_id": provider_attempt_id(request, attempt),
                            "session_id": exc.session_id,
                            "cleanup_succeeded": exc.cleanup.succeeded,
                            "backoff_seconds": backoff_seconds * attempt,
                        },
                    )
                )
                time.sleep(backoff_seconds * attempt)
                continue
            result = _lifecycle.provider_error_result(
                request,
                detail=str(exc),
                cleanup=exc.cleanup,
                usage=retry_usage,
                session_id=exc.session_id,
                attempts=attempt,
                lifecycle_event=exc.lifecycle_event,
            )
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        except Exception as exc:
            provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
            logger.debug("integrations.primeintellect.client: provider failure", exc_info=True)
            if client._is_cancellation_requested(request.task_id):
                result = client._canceled_result(request, str(exc))
            else:
                result = RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="cleanup_error",
                    cleanup=RemoteCleanupOutcome(
                        attempted=False,
                        succeeded=False,
                        detail="provider phase and cleanup outcome are unknown",
                    ),
                    error=f"{exc}; provider phase and cleanup outcome are unknown",
                    provenance=remote_request_provenance(request),
                    events=(
                        RemoteExecutionEvent(
                            sequence=1,
                            event_type="provider_error",
                            message=str(exc),
                            fields={
                                "phase": "unknown",
                                "attempts": attempt + 1,
                                "provider_attempt": attempt + 1,
                                "provider_attempt_id": provider_attempt_id(request, attempt + 1),
                            },
                        ),
                    ),
                )
            result = replace(result, usage=_accumulate_usage(retry_usage, result.usage))
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
        result = replace(result, usage=_accumulate_usage(retry_usage, result.usage))
        # Ledger persistence is outside the provider retry boundary. A sink
        # failure must never execute a paid remote task a second time.
        return client._commit_and_emit(
            request,
            result,
            provider_wall_seconds=provider_wall_seconds,
            retry_events=retry_events,
        )


def _deliver_replayed_result(
    client: PrimeExecutionClient,
    request: RemoteExecutionRequest,
    claim: ExternalEvalOutboxClaim,
) -> RemoteExecutionResult:
    if claim.result is None:
        raise ValueError("completed Prime replay requires an outbox claim with a result")
    with client._active_lock:
        client._result_committed.add(request.task_id)
    try:
        client._deliver_committed_ledger(
            claim.attempt_id,
            already_delivered=claim.sink_delivered,
        )
    except RemoteExecutionAccountingError:
        raise
    except Exception as exc:
        raise RemoteExecutionAccountingError(f"Prime replay ledger delivery failed: {type(exc).__name__}: {exc}") from exc
    return claim.result


def _replay_outbox(
    client: PrimeExecutionClient,
    request: RemoteExecutionRequest,
) -> ExternalEvalOutboxClaim | None:
    assert client.ledger_outbox is not None
    try:
        return client.ledger_outbox.replay("primeintellect", request)
    except Exception as exc:
        raise RemoteExecutionAccountingError(f"Prime durable outbox replay failed: {type(exc).__name__}: {exc}") from exc


def _claim_outbox(
    client: PrimeExecutionClient,
    request: RemoteExecutionRequest,
) -> ExternalEvalOutboxClaim:
    assert client.ledger_outbox is not None
    try:
        return client.ledger_outbox.claim("primeintellect", request)
    except Exception as exc:
        raise RemoteExecutionAccountingError(f"Prime durable outbox claim failed: {type(exc).__name__}: {exc}") from exc


def _dispatch_preflight_error_result(
    request: RemoteExecutionRequest,
    error: Exception,
    *,
    previous: _lifecycle.RetryableProvisioningError | None,
    completed_attempts: int,
    accumulated_usage: RemoteResourceUsage,
) -> RemoteExecutionResult:
    blocked_attempt = completed_attempts + 1
    dispatch_kind = "retry" if completed_attempts else "dispatch"
    detail = f"remote dispatch preflight failed before provider {dispatch_kind}: {type(error).__name__}: {error}"
    cleanup = previous.cleanup if previous is not None else RemoteCleanupOutcome(attempted=False, succeeded=True)
    session_id = previous.session_id if previous is not None else ""
    return RemoteExecutionResult(
        task_id=request.task_id,
        provider="primeintellect",
        status="provider_error",
        usage=accumulated_usage,
        cleanup=cleanup,
        error=detail,
        session_id=session_id,
        provenance=remote_request_provenance(request),
        events=(
            RemoteExecutionEvent(
                sequence=1,
                event_type="provider_error",
                message=detail,
                fields={
                    "phase": "pre_dispatch_preflight",
                    "attempts": completed_attempts,
                    "blocked_provider_attempt": blocked_attempt,
                    "blocked_provider_attempt_id": provider_attempt_id(request, blocked_attempt),
                    "previous_session_id": session_id,
                    "previous_cleanup_succeeded": cleanup.succeeded,
                },
            ),
        ),
    )


def _accumulate_usage(total: RemoteResourceUsage, addition: RemoteResourceUsage) -> RemoteResourceUsage:
    def summed(first: float | None, second: float | None) -> float | None:
        if first is None and second is None:
            return None
        return (first or 0.0) + (second or 0.0)

    def peak(first: float | None, second: float | None) -> float | None:
        values = tuple(value for value in (first, second) if value is not None)
        return max(values) if values else None

    return RemoteResourceUsage(
        wall_seconds=total.wall_seconds + addition.wall_seconds,
        cpu_seconds=summed(total.cpu_seconds, addition.cpu_seconds),
        peak_memory_mb=peak(total.peak_memory_mb, addition.peak_memory_mb),
        accelerator_seconds=summed(total.accelerator_seconds, addition.accelerator_seconds),
        accelerator_peak_memory_mb=peak(
            total.accelerator_peak_memory_mb,
            addition.accelerator_peak_memory_mb,
        ),
    )


def provider_attempt_id(request: RemoteExecutionRequest, attempt_number: int) -> str:
    identity = f"primeintellect\0{remote_request_sha256(request)}\0{attempt_number}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def with_provider_attempt(
    result: RemoteExecutionResult,
    request: RemoteExecutionRequest,
    attempt_number: int,
) -> RemoteExecutionResult:
    attempt_id = provider_attempt_id(request, attempt_number)
    return replace(
        result,
        events=tuple(
            replace(
                event,
                fields={
                    **event.fields,
                    "provider_attempt": attempt_number,
                    "provider_attempt_id": attempt_id,
                },
            )
            for event in result.events
        ),
    )
