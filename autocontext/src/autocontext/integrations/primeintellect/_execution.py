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
    remote_request_provenance,
    remote_request_sha256,
)
from autocontext.integrations.primeintellect import _lifecycle

if TYPE_CHECKING:
    from autocontext.execution.external_eval_outbox import ExternalEvalLedgerOutbox

logger = logging.getLogger(__name__)


class PrimeExecutionClient(Protocol):
    """Narrow client surface needed by the retry coordinator."""

    ledger_outbox: ExternalEvalLedgerOutbox | None
    _active_lock: threading.RLock
    _result_committed: set[str]

    def validate_request(self, request: RemoteExecutionRequest) -> None: ...

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
        result: RemoteExecutionResult,
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
    passthrough_error: type[Exception],
) -> RemoteExecutionResult:
    """Execute one claimed request without retrying ambiguous paid work."""

    attempt = 0
    provider_wall_seconds = 0.0
    retry_events: list[RemoteExecutionEvent] = []
    while True:
        if client._is_cancellation_requested(request.task_id):
            result = client._canceled_result(request)
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        # Grants can expire while a failed attempt is backing off. Recheck
        # policy immediately before every provider attempt; policy errors are
        # deliberately outside the provider-fallback exception path.
        client.validate_request(request)
        if attempt == 0 and client.ledger_outbox is not None:
            # Invalid requests never become ambiguous paid work. The first
            # successful validation commits immediately before dispatch.
            claim = client.ledger_outbox.claim("primeintellect", request)
            if claim.result is not None:
                with client._active_lock:
                    client._result_committed.add(request.task_id)
                client._deliver_committed_ledger(
                    claim.attempt_id,
                    claim.result,
                    already_delivered=claim.sink_delivered,
                )
                return claim.result
        attempt_started = time.perf_counter()
        try:
            result = asyncio.run(client._execute_request_once(request, attempt_number=attempt + 1))
        except passthrough_error:
            raise
        except _lifecycle.SandboxCreationOutcomeUnknown as exc:
            provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
            result = with_provider_attempt(
                _lifecycle.ambiguous_creation_result(request, exc),
                request,
                attempt + 1,
            )
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
            if client._is_cancellation_requested(request.task_id):
                completed = _lifecycle.provider_error_result(
                    request,
                    detail=str(exc),
                    cleanup=exc.cleanup,
                    usage=exc.usage,
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
                usage=exc.usage,
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
            return client._commit_and_emit(
                request,
                result,
                provider_wall_seconds=provider_wall_seconds,
                retry_events=retry_events,
            )
        provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
        # Ledger persistence is outside the provider retry boundary. A sink
        # failure must never execute a paid remote task a second time.
        return client._commit_and_emit(
            request,
            result,
            provider_wall_seconds=provider_wall_seconds,
            retry_events=retry_events,
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
