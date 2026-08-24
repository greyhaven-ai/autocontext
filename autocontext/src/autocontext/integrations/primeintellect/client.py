"""Prime Intellect adapter for provider-neutral remote execution sessions."""

from __future__ import annotations

import asyncio
import logging
import math
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from autocontext.execution.remote_execution import (
    ExternalEvalLedgerSink,
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteProviderCapabilities,
    RemoteResolvedEnvironment,
    RemoteResourceRequest,
    RemoteResourceUsage,
    parse_remote_stdout,
    remote_request_provenance,
    remote_request_sha256,
)
from autocontext.execution.scenario_remote_package import DEFAULT_REMOTE_RUNTIME_IMAGE, require_pinned_runtime_image
from autocontext.execution.scenario_remote_task import build_builtin_scenario_remote_request
from autocontext.integrations.primeintellect import _lifecycle
from autocontext.integrations.primeintellect import _request as _request_helpers
from autocontext.integrations.primeintellect.accelerators import (
    ProviderCapabilityDriftError,
    create_kwargs,
    resolved_environment,
    resource_usage,
    validate_required_telemetry,
    validate_resolved_environment,
)
from autocontext.offline import require_online
from autocontext.scenarios.base import ExecutionLimits

logger = logging.getLogger(__name__)
_CLEANUP_TIMEOUT_SECONDS = 30.0

AsyncSandboxClient: Any | None = None
CreateSandboxRequest: Any | None = None


class MissingPrimeIntellectExtraError(RuntimeError):
    """Raised when the PrimeIntellect optional extra is not installed."""


class UnsupportedRemoteCapabilityError(RuntimeError):
    """Raised when a request needs a capability the provider did not advertise."""


class _CreateSandboxRequestFallback:
    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)


def _prime_sandboxes_sdk() -> tuple[Any, Any]:
    if AsyncSandboxClient is not None:
        return AsyncSandboxClient, CreateSandboxRequest or _CreateSandboxRequestFallback
    try:
        from prime_sandboxes import AsyncSandboxClient as client_cls
        from prime_sandboxes import CreateSandboxRequest as request_cls
    except ImportError as exc:
        raise MissingPrimeIntellectExtraError(
            "PrimeIntellect execution requires the optional prime-sandboxes SDK. "
            "Install it with `pip install 'autocontext[primeintellect]'`."
        ) from exc
    return client_cls, request_cls


@dataclass(slots=True)
class PrimeIntellectClient:
    """Optional Prime Intellect remote-session adapter.

    Scenario evaluation is packaged by ``scenario_remote_task``. This client
    owns only provider capability checks, sandbox lifecycle, command/event
    transport, artifact collection, usage, cleanup, and ledger emission.
    """

    api_key: str = field(repr=False)
    docker_image: str = DEFAULT_REMOTE_RUNTIME_IMAGE
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_size_gb: float = 5.0
    timeout_minutes: int = 30
    max_wait_attempts: int = 60
    network_access: bool = True
    allow_fallback: bool = False
    default_requirements: RemoteExecutionRequirements | None = None
    resource_capabilities: RemoteProviderCapabilities = field(default_factory=RemoteProviderCapabilities)
    provider_capabilities: Mapping[str, bool] = field(
        default_factory=lambda: {
            "accelerator": False,
            # Reuse is unsafe unless the provider can prove a clean task
            # boundary inside one sandbox. Prime currently exposes lifecycle
            # reuse but no reset/isolation primitive, so production defaults
            # must remain cold and ephemeral.
            "session_reuse": False,
            "snapshot": False,
            "warm": False,
            "secret_grants": False,
        }
    )
    ledger_sink: ExternalEvalLedgerSink | None = None
    _active_lock: threading.RLock = field(default_factory=threading.RLock, init=False, repr=False)
    _active_sandboxes: dict[str, _lifecycle.ActiveSandbox] = field(default_factory=dict, init=False, repr=False)
    _inflight_tasks: set[str] = field(default_factory=set, init=False, repr=False)
    _cancellation_requested: set[str] = field(default_factory=set, init=False, repr=False)
    _result_committed: set[str] = field(default_factory=set, init=False, repr=False)

    def __post_init__(self) -> None:
        if not self.api_key.strip():
            raise ValueError("Prime Intellect API key must be non-empty")
        require_pinned_runtime_image(self.docker_image)
        resources = (self.cpu_cores, self.memory_gb, self.disk_size_gb)
        if any(not math.isfinite(value) or value <= 0 for value in resources):
            raise ValueError("Prime Intellect resource limits must be positive and finite")
        if self.timeout_minutes < 1 or self.max_wait_attempts < 1:
            raise ValueError("Prime Intellect timeout and wait attempts must be positive")
        if self.default_requirements is None:
            self.default_requirements = RemoteExecutionRequirements(
                image=self.docker_image,
                resources=self._default_resource_request(),
            )
        elif self.default_requirements.image != self.docker_image:
            raise ValueError("Prime default requirements image must match docker_image")
        self.validate_requirements(self.default_requirements)

    def capabilities(self) -> dict[str, bool]:
        return {
            name: (
                bool(self.resource_capabilities.accelerator_limits)
                if name == "accelerator"
                else False
                if name == "session_reuse"
                else self.provider_capabilities.get(name) is True
            )
            for name in ("accelerator", "session_reuse", "snapshot", "warm", "secret_grants")
        }

    def validate_requirements(self, requirements: RemoteExecutionRequirements) -> None:
        reason = self.resource_capabilities.mismatch_reason(requirements)
        if reason:
            raise UnsupportedRemoteCapabilityError(f"Prime Intellect capability mismatch: {reason}")

    def _default_resource_request(self) -> RemoteResourceRequest:
        return RemoteResourceRequest(
            cpu_cores=self.cpu_cores,
            memory_gb=self.memory_gb,
            disk_gb=self.disk_size_gb,
        )

    def warm_provision(self, environment_name: str, max_retries: int = 2, backoff_seconds: float = 0.75) -> dict[str, Any]:
        del max_retries, backoff_seconds
        require_online("use the PrimeIntellect executor")
        try:
            asyncio.run(self._probe())
            return {"environment": environment_name, "status": "ready"}
        except Exception as exc:
            logger.debug("integrations.primeintellect.client: probe failed", exc_info=True)
            return self.unavailable_state(environment_name, str(exc))

    def execute_request(
        self,
        request: RemoteExecutionRequest,
        *,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ) -> RemoteExecutionResult:
        require_online("use the PrimeIntellect executor")
        if max_retries < 0 or not math.isfinite(backoff_seconds) or backoff_seconds < 0:
            raise ValueError("Prime Intellect retry count and backoff must be non-negative and finite")
        self._begin_task(request.task_id)
        try:
            attempt = 0
            provider_wall_seconds = 0.0
            while True:
                if self._is_cancellation_requested(request.task_id):
                    result = self._canceled_result(request)
                    return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
                # Grants can expire while a failed attempt is backing off. Recheck
                # policy immediately before every provider attempt; policy errors
                # are deliberately outside the provider-fallback exception path.
                self.validate_request(request)
                attempt_started = time.perf_counter()
                try:
                    result = asyncio.run(self._execute_request_once(request))
                except MissingPrimeIntellectExtraError:
                    raise
                except _lifecycle.SandboxCreationOutcomeUnknown as exc:
                    provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
                    result = _lifecycle.ambiguous_creation_result(request, exc)
                    if self._is_cancellation_requested(request.task_id):
                        result = self._canceled_result(request, str(exc), completed=result)
                        return self._commit_and_emit(
                            request,
                            result,
                            provider_wall_seconds=provider_wall_seconds,
                        )
                    result = self._commit_and_emit(
                        request,
                        result,
                        provider_wall_seconds=provider_wall_seconds,
                    )
                    return result
                except _lifecycle.RetryableProvisioningError as exc:
                    provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
                    if self._is_cancellation_requested(request.task_id):
                        completed = _lifecycle.provider_error_result(
                            request,
                            detail=str(exc),
                            cleanup=exc.cleanup,
                            usage=exc.usage,
                            session_id=exc.session_id,
                            attempts=attempt + 1,
                            lifecycle_event=exc.lifecycle_event,
                        )
                        result = self._canceled_result(request, str(exc), completed=completed)
                        return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
                    attempt += 1
                    if attempt <= max_retries:
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
                    return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
                except Exception as exc:
                    provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
                    logger.debug("integrations.primeintellect.client: provider failure", exc_info=True)
                    if self._is_cancellation_requested(request.task_id):
                        result = self._canceled_result(request, str(exc))
                        return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
                    attempt += 1
                    if attempt <= max_retries:
                        time.sleep(backoff_seconds * attempt)
                        continue
                    result = RemoteExecutionResult(
                        task_id=request.task_id,
                        provider="primeintellect",
                        status="provider_error",
                        cleanup=RemoteCleanupOutcome(
                            attempted=False,
                            succeeded=True,
                            detail="no provider resource was registered",
                        ),
                        error=str(exc),
                        provenance=remote_request_provenance(request),
                        events=(
                            RemoteExecutionEvent(
                                sequence=1,
                                event_type="provider_error",
                                message=str(exc),
                                fields={"attempts": attempt},
                            ),
                        ),
                    )
                    return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
                provider_wall_seconds += max(0.0, time.perf_counter() - attempt_started)
                # Ledger persistence is deliberately outside the provider retry
                # exception boundary. A sink failure must never execute a paid
                # remote task a second time.
                return self._commit_and_emit(request, result, provider_wall_seconds=provider_wall_seconds)
        finally:
            self._end_task(request.task_id)

    def execute_requests(
        self,
        requests: Sequence[RemoteExecutionRequest],
    ) -> tuple[RemoteExecutionResult, ...]:
        """Reject session reuse until Prime exposes a verified reset primitive."""

        require_online("use the PrimeIntellect executor")
        del requests
        raise UnsupportedRemoteCapabilityError(
            "Prime Intellect session_reuse is disabled until the provider exposes a verified reset primitive"
        )

    def cancel_request(self, request: RemoteExecutionRequest | str) -> bool:
        """Cancel an in-flight task by deleting its provider sandbox exactly once.

        Campaign workers may only retain the durable task id, while the generic
        scheduler adapter retains the full request. Both forms resolve through
        the same host-side task-to-sandbox registry. Cancellation requested
        before sandbox creation is remembered and honored immediately after the
        provider returns the sandbox id.
        """

        task_id = request.task_id if isinstance(request, RemoteExecutionRequest) else request
        if not task_id.strip():
            return False
        with self._active_lock:
            if task_id not in self._inflight_tasks:
                return False
            if task_id in self._result_committed:
                return False
            self._cancellation_requested.add(task_id)
            active = self._active_sandboxes.get(task_id)
        if active is None:
            return True
        return asyncio.run(self._cancel_active_sandbox(active))

    def execute_strategy(
        self,
        *,
        scenario_name: str,
        strategy: dict[str, Any],
        seed: int,
        timeout_seconds: float,
        max_memory_mb: int,
        network_access: bool,
        max_retries: int = 2,
        backoff_seconds: float = 0.75,
    ) -> dict[str, Any]:
        """Compatibility facade for existing scenario executors."""

        requirements = self.default_requirements
        if requirements is None:
            raise RuntimeError("Prime Intellect execution requirements are unavailable")
        request = build_builtin_scenario_remote_request(
            scenario_name,
            strategy,
            seed,
            ExecutionLimits(
                timeout_seconds=timeout_seconds,
                max_memory_mb=max_memory_mb,
                network_access=network_access,
            ),
            image=requirements.image,
            cpu_cores=requirements.resources.cpu_cores,
            disk_gb=requirements.resources.disk_gb,
            memory_gb=requirements.resources.memory_gb,
            accelerator=requirements.resources.accelerator,
            region=requirements.region,
            required_telemetry=requirements.required_telemetry,
        )
        result = self.execute_request(request, max_retries=max_retries, backoff_seconds=backoff_seconds)
        if not result.succeeded:
            if self.allow_fallback and requirements.resources.accelerator is None:
                return self.fallback_local_response(scenario_name, seed)
            raise RuntimeError(f"primeintellect {result.status}: {result.error}")
        payload = _request_helpers.last_json_object(result.stdout)
        if not isinstance(payload.get("result"), Mapping) or not isinstance(payload.get("replay"), Mapping):
            if self.allow_fallback and requirements.resources.accelerator is None:
                return self.fallback_local_response(scenario_name, seed)
            raise ValueError("primeintellect task response missing result or replay")
        return {"result": dict(payload["result"]), "replay": dict(payload["replay"])}

    async def _probe(self) -> None:
        sandbox_client_cls, _ = _prime_sandboxes_sdk()
        async with sandbox_client_cls(api_key=self.api_key) as client:
            await client.list(per_page=1, exclude_terminated=True)

    async def _execute_request_once(self, request: RemoteExecutionRequest) -> RemoteExecutionResult:
        sandbox_client_cls, create_sandbox_request = _prime_sandboxes_sdk()
        sandbox_id = ""
        active: _lifecycle.ActiveSandbox | None = None
        started = time.perf_counter()
        response: Any | None = None
        timeout_error = ""
        cancellation_error = ""
        provider_error: Exception | None = None
        client_exit_error: Exception | None = None
        command_dispatched = False
        cleanup = RemoteCleanupOutcome(attempted=False, succeeded=False)
        resolved = RemoteResolvedEnvironment()
        try:
            async with sandbox_client_cls(api_key=self.api_key) as client:
                self.validate_request(request)
                try:
                    sandbox = await client.create(
                        create_sandbox_request(
                            **create_kwargs(
                                request,
                                timeout_minutes=self.timeout_minutes,
                                network_access=self.network_access,
                            )
                        )
                    )
                    raw_sandbox_id = getattr(sandbox, "id", None)
                    if raw_sandbox_id is None or not str(raw_sandbox_id).strip():
                        raise ValueError("provider returned a sandbox without a resource id")
                    sandbox_id = str(raw_sandbox_id)
                except Exception as exc:
                    raise _lifecycle.SandboxCreationOutcomeUnknown(
                        f"sandbox creation failed: {type(exc).__name__}: {exc}"
                    ) from exc
                active = self._register_sandbox(request.task_id, sandbox_id)
                try:
                    resolved = resolved_environment(sandbox)
                    validate_resolved_environment(request, resolved)
                    if self._is_cancellation_requested(request.task_id):
                        cancellation_error = "remote task canceled"
                    else:
                        await client.wait_for_creation(sandbox_id, max_attempts=self.max_wait_attempts)
                    if self._is_cancellation_requested(request.task_id):
                        cancellation_error = "remote task canceled"
                    elif not cancellation_error:
                        try:
                            command_dispatched = True
                            response = await client.execute_command(
                                sandbox_id=sandbox_id,
                                command=_request_helpers.build_command(request),
                                timeout=max(1, math.ceil(request.timeout_seconds)),
                            )
                        except TimeoutError as exc:
                            timeout_error = str(exc) or "remote task timed out"
                except Exception as exc:
                    provider_error = exc
                finally:
                    cleanup = await self._cleanup_once(client, active)
        except _lifecycle.SandboxCreationOutcomeUnknown:
            raise
        except Exception as exc:
            if not sandbox_id:
                creation_error = exc.__context__
                if isinstance(creation_error, _lifecycle.SandboxCreationOutcomeUnknown):
                    detail = f"{creation_error}; Prime Intellect client context exit failed: {type(exc).__name__}: {exc}"
                    raise _lifecycle.SandboxCreationOutcomeUnknown(detail, client_exit_error=exc) from exc
                raise
            client_exit_error = exc
        if self._is_cancellation_requested(request.task_id):
            cancellation_error = "remote task canceled"
        try:
            usage = resource_usage(response, wall_seconds=time.perf_counter() - started)
            if response is not None:
                validate_required_telemetry(request, resolved, usage)
        except ProviderCapabilityDriftError as exc:
            usage = RemoteResourceUsage(wall_seconds=time.perf_counter() - started)
            provider_error = provider_error or exc
        lifecycle_event = RemoteExecutionEvent(
            sequence=1,
            event_type="canceled" if cancellation_error else "lifecycle",
            message=cancellation_error or request.lifecycle,
            fields={
                "session_id": sandbox_id,
                "cleanup": cleanup.succeeded,
                "request_sha256": remote_request_sha256(request),
                "resolved_image": resolved.image,
                "resolved_region": resolved.region,
                "resolved_accelerator_kind": resolved.accelerator_kind,
                "resolved_accelerator_count": resolved.accelerator_count,
                "runtime": resolved.runtime,
            },
        )

        def finish(result: RemoteExecutionResult) -> RemoteExecutionResult:
            enriched = replace(result, provenance=remote_request_provenance(request, resolved=resolved))
            return _lifecycle.client_exit_error_result(enriched, client_exit_error, lifecycle_event)

        if cancellation_error:
            detail = (
                _lifecycle.cleanup_failure_detail(cancellation_error, cleanup) if not cleanup.succeeded else cancellation_error
            )
            return finish(
                RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="cleanup_error" if not cleanup.succeeded else "provider_error",
                    usage=usage,
                    cleanup=cleanup,
                    error=detail,
                    session_id=sandbox_id,
                    events=(lifecycle_event,),
                    provenance=remote_request_provenance(request),
                )
            )
        if provider_error is not None:
            if not cleanup.succeeded:
                provider_detail = f"{type(provider_error).__name__}: {provider_error}"
                return finish(
                    _lifecycle.terminal_cleanup_error(
                        request,
                        cleanup=cleanup,
                        usage=usage,
                        session_id=sandbox_id,
                        primary_error=provider_detail,
                        lifecycle_event=lifecycle_event,
                    )
                )
            if isinstance(provider_error, ProviderCapabilityDriftError):
                detail = f"provider capability drift: {provider_error}"
                return finish(
                    RemoteExecutionResult(
                        task_id=request.task_id,
                        provider="primeintellect",
                        status="provider_error",
                        usage=usage,
                        cleanup=cleanup,
                        error=detail,
                        session_id=sandbox_id,
                        events=(lifecycle_event,),
                    )
                )
            if command_dispatched:
                provider_detail = f"remote command outcome is unknown: {type(provider_error).__name__}: {provider_error}"
                return finish(
                    RemoteExecutionResult(
                        task_id=request.task_id,
                        provider="primeintellect",
                        status="provider_error",
                        usage=usage,
                        cleanup=cleanup,
                        error=provider_detail,
                        session_id=sandbox_id,
                        events=(lifecycle_event,),
                        provenance=remote_request_provenance(request),
                    )
                )
            if client_exit_error is not None:
                detail = f"{type(provider_error).__name__}: {provider_error}"
                return finish(_lifecycle.provider_error_result(request, detail, cleanup, usage, sandbox_id, 1, lifecycle_event))
            raise _lifecycle.RetryableProvisioningError(
                provider_error,
                cleanup=cleanup,
                usage=usage,
                session_id=sandbox_id,
                lifecycle_event=lifecycle_event,
            ) from provider_error
        if timeout_error:
            cleanup_detail = _lifecycle.cleanup_failure_detail(timeout_error, cleanup)
            return finish(
                RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="cleanup_error" if not cleanup.succeeded else "timeout",
                    usage=usage,
                    cleanup=cleanup,
                    error=cleanup_detail if not cleanup.succeeded else timeout_error,
                    session_id=sandbox_id,
                    events=(lifecycle_event,),
                    provenance=remote_request_provenance(request),
                )
            )
        if response is None:
            if not cleanup.succeeded:
                return finish(
                    _lifecycle.terminal_cleanup_error(
                        request,
                        cleanup=cleanup,
                        usage=usage,
                        session_id=sandbox_id,
                        primary_error="Prime Intellect returned no command response",
                        lifecycle_event=lifecycle_event,
                    )
                )
            return finish(
                RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="provider_error",
                    usage=usage,
                    cleanup=cleanup,
                    error="Prime Intellect returned no command response",
                    session_id=sandbox_id,
                    events=(lifecycle_event,),
                    provenance=remote_request_provenance(request),
                )
            )
        try:
            parsed = parse_remote_stdout(
                request,
                provider="primeintellect",
                stdout=str(response.stdout),
                stderr=str(response.stderr),
                exit_code=int(response.exit_code),
                usage=usage,
                cleanup=cleanup,
                session_id=sandbox_id,
            )
        except Exception as exc:
            if not cleanup.succeeded:
                return finish(
                    _lifecycle.terminal_cleanup_error(
                        request,
                        cleanup=cleanup,
                        usage=usage,
                        session_id=sandbox_id,
                        primary_error=f"{type(exc).__name__}: {exc}",
                        lifecycle_event=lifecycle_event,
                    )
                )
            return finish(
                RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="artifact_error",
                    usage=usage,
                    cleanup=cleanup,
                    error=f"malformed provider command response: {type(exc).__name__}: {exc}",
                    session_id=sandbox_id,
                    events=(lifecycle_event,),
                    provenance=remote_request_provenance(request),
                )
            )
        events = (lifecycle_event, *parsed.events)
        return finish(replace(parsed, events=events))

    def _begin_task(self, task_id: str) -> None:
        with self._active_lock:
            if task_id in self._inflight_tasks:
                raise RuntimeError(f"Prime Intellect task is already in flight: {task_id}")
            self._inflight_tasks.add(task_id)

    def _end_task(self, task_id: str) -> None:
        with self._active_lock:
            self._inflight_tasks.discard(task_id)
            self._active_sandboxes.pop(task_id, None)
            self._cancellation_requested.discard(task_id)
            self._result_committed.discard(task_id)

    def _register_sandbox(self, task_id: str, sandbox_id: str) -> _lifecycle.ActiveSandbox:
        active = _lifecycle.ActiveSandbox(task_id=task_id, sandbox_id=sandbox_id)
        with self._active_lock:
            if task_id not in self._inflight_tasks:
                raise RuntimeError(f"Prime Intellect task is no longer in flight: {task_id}")
            self._active_sandboxes[task_id] = active
        return active

    def _is_cancellation_requested(self, task_id: str) -> bool:
        with self._active_lock:
            return task_id in self._cancellation_requested

    async def _cancel_active_sandbox(self, active: _lifecycle.ActiveSandbox) -> bool:
        sandbox_client_cls, _ = _prime_sandboxes_sdk()
        async with sandbox_client_cls(api_key=self.api_key) as client:
            cleanup = await self._cleanup_once(client, active)
        return cleanup.succeeded

    async def _cleanup_once(self, client: Any, active: _lifecycle.ActiveSandbox) -> RemoteCleanupOutcome:
        with self._active_lock:
            owns_cleanup = not active.cleanup_started
            if owns_cleanup:
                active.cleanup_started = True
        if owns_cleanup:
            try:
                await asyncio.wait_for(
                    client.delete(active.sandbox_id),
                    timeout=_CLEANUP_TIMEOUT_SECONDS,
                )
            except Exception as exc:
                logger.debug("integrations.primeintellect.client: cleanup failed", exc_info=True)
                detail = (
                    f"sandbox cleanup timed out after {_CLEANUP_TIMEOUT_SECONDS:g} seconds"
                    if isinstance(exc, TimeoutError)
                    else str(exc) or type(exc).__name__
                )
                outcome = RemoteCleanupOutcome(
                    attempted=True,
                    succeeded=False,
                    resource_id=active.sandbox_id,
                    detail=detail,
                )
            else:
                outcome = RemoteCleanupOutcome(attempted=True, succeeded=True, resource_id=active.sandbox_id)
            with self._active_lock:
                active.cleanup_outcome = outcome
                active.cleanup_done.set()
                if self._active_sandboxes.get(active.task_id) is active:
                    self._active_sandboxes.pop(active.task_id, None)
            return outcome

        completed = await asyncio.to_thread(active.cleanup_done.wait, _CLEANUP_TIMEOUT_SECONDS + 1.0)
        with self._active_lock:
            concurrent_outcome = active.cleanup_outcome
        if completed and concurrent_outcome is not None:
            return concurrent_outcome
        return RemoteCleanupOutcome(
            attempted=True,
            succeeded=False,
            resource_id=active.sandbox_id,
            detail="timed out waiting for concurrent sandbox cleanup",
        )

    def _canceled_result(
        self,
        request: RemoteExecutionRequest,
        detail: str = "",
        *,
        completed: RemoteExecutionResult | None = None,
    ) -> RemoteExecutionResult:
        message = "remote task canceled"
        if detail and detail != message:
            logger.debug("Prime cancellation followed provider error: %s", detail)
        cleanup = completed.cleanup if completed is not None else RemoteCleanupOutcome(attempted=False, succeeded=True)
        events = completed.events if completed is not None else ()
        if not any(event.event_type == "canceled" for event in events):
            events = (
                *events,
                RemoteExecutionEvent(
                    sequence=len(events) + 1,
                    event_type="canceled",
                    message=message,
                ),
            )
        return RemoteExecutionResult(
            task_id=request.task_id,
            provider="primeintellect",
            status="cleanup_error" if not cleanup.succeeded else "provider_error",
            usage=completed.usage if completed is not None else RemoteResourceUsage(),
            cleanup=cleanup,
            error=_lifecycle.cleanup_failure_detail(message, cleanup) if not cleanup.succeeded else message,
            session_id=completed.session_id if completed is not None else "",
            provenance=remote_request_provenance(request),
            events=events,
        )

    def _commit_and_emit(
        self,
        request: RemoteExecutionRequest,
        result: RemoteExecutionResult,
        *,
        provider_wall_seconds: float,
    ) -> RemoteExecutionResult:
        """Linearize terminal result publication against cancellation."""

        if provider_wall_seconds > result.usage.wall_seconds:
            result = replace(
                result,
                usage=replace(result.usage, wall_seconds=provider_wall_seconds),
            )
        with self._active_lock:
            cancellation_won = request.task_id in self._cancellation_requested
            self._result_committed.add(request.task_id)
        if cancellation_won and not any(event.event_type == "canceled" for event in result.events):
            result = self._canceled_result(request, completed=result)
        self._emit_ledger(result)
        return result

    def validate_request(self, request: RemoteExecutionRequest) -> None:
        capabilities = self.capabilities()
        expired_grants = [grant.name for grant in request.secret_grants if grant.expires_at <= time.time()]
        if expired_grants:
            raise ValueError(f"remote secret grant expired before dispatch: {', '.join(sorted(expired_grants))}")
        self.validate_requirements(request.requirements)
        if request.resources.accelerator is not None and not capabilities["accelerator"]:
            raise UnsupportedRemoteCapabilityError("Prime Intellect adapter does not advertise accelerator support")
        if request.lifecycle == "reuse_matched_trials" and not capabilities["session_reuse"]:
            raise UnsupportedRemoteCapabilityError("Prime Intellect adapter does not advertise session_reuse")
        if request.lifecycle == "warm_snapshot" and not (capabilities["snapshot"] and capabilities["warm"]):
            raise UnsupportedRemoteCapabilityError("Prime Intellect adapter does not advertise warm snapshot support")
        if request.secret_grants and not capabilities["secret_grants"]:
            raise UnsupportedRemoteCapabilityError("Prime Intellect adapter does not advertise scoped secret grants")
        if request.network_policy == "allow" and not self.network_access:
            raise UnsupportedRemoteCapabilityError("Prime Intellect network access is disabled by adapter policy")

    def _emit_ledger(self, result: RemoteExecutionResult) -> None:
        if self.ledger_sink is not None:
            self.ledger_sink(result.to_ledger_entry())

    def fallback_local_response(self, scenario_name: str, seed: int) -> dict[str, Any]:
        return _request_helpers.fallback_local_response(scenario_name, seed)

    def unavailable_state(self, environment_name: str, reason: str) -> dict[str, Any]:
        return {"environment": environment_name, "status": "failed", "error": reason}

    def _build_eval_command(self, *, scenario_name: str, strategy: dict[str, Any], seed: int) -> str:
        """Compatibility helper; scenario logic lives in its packaged entrypoint."""

        requirements = self.default_requirements
        if requirements is None:
            raise RuntimeError("Prime Intellect execution requirements are unavailable")
        return _request_helpers.build_eval_command(
            requirements,
            scenario_name=scenario_name,
            strategy=strategy,
            seed=seed,
        )


__all__ = [
    "MissingPrimeIntellectExtraError",
    "PrimeIntellectClient",
    "ProviderCapabilityDriftError",
    "UnsupportedRemoteCapabilityError",
]
