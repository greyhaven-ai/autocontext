"""Cancellable transport boundary for campaign-auditor model calls."""

from __future__ import annotations

import concurrent.futures
import threading
import time
from dataclasses import dataclass
from typing import Any, Literal, Protocol


class AuditorModelResponse(Protocol):
    text: str
    usage: Any


class AuditorModelClient(Protocol):
    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> AuditorModelResponse: ...


class AuditorCallHandle(Protocol):
    """Provider-native asynchronous call that can prove cancellation."""

    def result(self, timeout: float) -> AuditorModelResponse: ...

    def cancel(self) -> bool: ...


class CancellableAuditorModelClient(Protocol):
    def start_generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> AuditorCallHandle: ...


@dataclass(frozen=True)
class AuditorCallOutcome:
    response: AuditorModelResponse | None
    latency_ms: int
    failure_status: Literal["timed_out", "canceled", "failed"] | None
    failure_reason: str | None
    model_call_attempted: bool


def execute_auditor_call(
    client: AuditorModelClient,
    *,
    model: str,
    prompt: str,
    max_tokens: int,
    timeout_seconds: float,
    cancellation_event: threading.Event | None,
) -> AuditorCallOutcome:
    """Run one request and return only after success or a terminal cancellation result."""

    started = time.perf_counter()
    pool: concurrent.futures.ThreadPoolExecutor | None = None
    future: concurrent.futures.Future[AuditorModelResponse] | None = None
    call_handle: AuditorCallHandle | None = None
    try:
        start_generate = getattr(client, "start_generate", None)
        if callable(start_generate):
            call_handle = start_generate(
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.0,
                role="auditor",
            )
        else:
            pool = concurrent.futures.ThreadPoolExecutor(max_workers=1, thread_name_prefix="campaign-auditor")
            future = pool.submit(
                client.generate,
                model=model,
                prompt=prompt,
                max_tokens=max_tokens,
                temperature=0.0,
                role="auditor",
            )
        deadline = time.monotonic() + timeout_seconds
        while True:
            if cancellation_event is not None and cancellation_event.is_set():
                canceled = call_handle.cancel() if call_handle is not None else bool(future and future.cancel())
                confirmed = canceled or call_handle is None
                return AuditorCallOutcome(
                    response=None,
                    latency_ms=_latency_ms(started),
                    failure_status="canceled" if confirmed else "failed",
                    failure_reason=(
                        "campaign audit canceled while model call was active"
                        if confirmed
                        else "auditor transport could not confirm cancellation"
                    ),
                    model_call_attempted=True,
                )
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                canceled = call_handle.cancel() if call_handle is not None else bool(future and future.cancel())
                return AuditorCallOutcome(
                    response=None,
                    latency_ms=_latency_ms(started),
                    failure_status="timed_out" if canceled or call_handle is None else "failed",
                    failure_reason=(
                        "auditor model call timed out and was canceled"
                        if canceled
                        else "auditor model call timed out"
                        if call_handle is None
                        else "auditor transport timed out but could not confirm cancellation"
                    ),
                    model_call_attempted=True,
                )
            try:
                response = (
                    call_handle.result(timeout=min(0.01, remaining))
                    if call_handle is not None
                    else _future_result(future, min(0.01, remaining))
                )
            except concurrent.futures.TimeoutError:
                continue
            return AuditorCallOutcome(
                response=response,
                latency_ms=_latency_ms(started),
                failure_status=None,
                failure_reason=None,
                model_call_attempted=True,
            )
    except Exception as exc:
        return AuditorCallOutcome(
            response=None,
            latency_ms=_latency_ms(started),
            failure_status="failed",
            failure_reason=f"auditor model call failed: {type(exc).__name__}",
            model_call_attempted=future is not None or call_handle is not None,
        )
    finally:
        if pool is not None:
            pool.shutdown(wait=False, cancel_futures=True)


def _future_result(
    future: concurrent.futures.Future[AuditorModelResponse] | None,
    timeout: float,
) -> AuditorModelResponse:
    if future is None:
        raise RuntimeError("auditor transport did not create a request")
    return future.result(timeout=timeout)


def _latency_ms(started: float) -> int:
    return int((time.perf_counter() - started) * 1000)


__all__ = [
    "AuditorCallHandle",
    "AuditorCallOutcome",
    "AuditorModelClient",
    "AuditorModelResponse",
    "CancellableAuditorModelClient",
    "execute_auditor_call",
]
