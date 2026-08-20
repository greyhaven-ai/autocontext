"""Prime Intellect adapter for provider-neutral remote execution sessions."""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import shlex
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field, replace
from typing import Any

from autocontext.execution.remote_execution import (
    ExternalEvalLedgerSink,
    RemoteCleanupOutcome,
    RemoteExecutionEvent,
    RemoteExecutionRequest,
    RemoteExecutionResult,
    RemoteResourceUsage,
    parse_remote_stdout,
    requests_are_reuse_compatible,
)
from autocontext.execution.scenario_remote_task import build_builtin_scenario_remote_request
from autocontext.offline import require_online
from autocontext.scenarios.base import ExecutionLimits

logger = logging.getLogger(__name__)

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

    api_key: str
    docker_image: str = "python:3.11-slim"
    cpu_cores: float = 1.0
    memory_gb: float = 2.0
    disk_size_gb: float = 5.0
    timeout_minutes: int = 30
    max_wait_attempts: int = 60
    network_access: bool = True
    allow_fallback: bool = False
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

    def capabilities(self) -> dict[str, bool]:
        return {
            name: self.provider_capabilities.get(name) is True
            for name in ("accelerator", "session_reuse", "snapshot", "warm", "secret_grants")
        }

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
        attempt = 0
        while True:
            # Grants can expire while a failed attempt is backing off. Recheck
            # policy immediately before every provider attempt; policy errors
            # are deliberately outside the provider-fallback exception path.
            self._validate_request(request)
            try:
                result = asyncio.run(self._execute_request_once(request))
                self._emit_ledger(result)
                return result
            except MissingPrimeIntellectExtraError:
                raise
            except Exception as exc:
                logger.debug("integrations.primeintellect.client: provider failure", exc_info=True)
                attempt += 1
                if attempt <= max_retries:
                    time.sleep(backoff_seconds * attempt)
                    continue
                if not self.allow_fallback:
                    raise
                result = RemoteExecutionResult(
                    task_id=request.task_id,
                    provider="primeintellect",
                    status="provider_error",
                    cleanup=RemoteCleanupOutcome(attempted=False, succeeded=False),
                    error=str(exc),
                    events=(
                        RemoteExecutionEvent(
                            sequence=1,
                            event_type="provider_error",
                            message=str(exc),
                            fields={"attempts": attempt},
                        ),
                    ),
                )
                self._emit_ledger(result)
                return result

    def execute_requests(
        self,
        requests: Sequence[RemoteExecutionRequest],
    ) -> tuple[RemoteExecutionResult, ...]:
        """Execute a bounded matched cohort when isolated reuse is explicitly advertised."""

        require_online("use the PrimeIntellect executor")
        if not self.capabilities()["session_reuse"]:
            raise UnsupportedRemoteCapabilityError("Prime Intellect adapter does not advertise session_reuse")
        if not requests_are_reuse_compatible(requests):
            raise ValueError("requests are not compatible with bounded matched-trial reuse")
        for request in requests:
            self._validate_request(request)
        results = asyncio.run(self._execute_reuse_batch_once(requests))
        for result in results:
            self._emit_ledger(result)
        return results

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

        request = build_builtin_scenario_remote_request(
            scenario_name,
            strategy,
            seed,
            ExecutionLimits(
                timeout_seconds=timeout_seconds,
                max_memory_mb=max_memory_mb,
                network_access=network_access,
            ),
            image=self.docker_image,
            cpu_cores=self.cpu_cores,
            disk_gb=self.disk_size_gb,
            memory_gb=self.memory_gb,
        )
        result = self.execute_request(request, max_retries=max_retries, backoff_seconds=backoff_seconds)
        if not result.succeeded:
            if self.allow_fallback:
                return self.fallback_local_response(scenario_name, seed)
            raise RuntimeError(f"primeintellect {result.status}: {result.error}")
        payload = _last_json_object(result.stdout)
        if not isinstance(payload.get("result"), Mapping) or not isinstance(payload.get("replay"), Mapping):
            if self.allow_fallback:
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
        started = time.perf_counter()
        response: Any | None = None
        timeout_error = ""
        cleanup = RemoteCleanupOutcome(attempted=False, succeeded=False)
        async with sandbox_client_cls(api_key=self.api_key) as client:
            self._validate_request(request)
            sandbox = await client.create(create_sandbox_request(**self._create_kwargs(request)))
            sandbox_id = str(sandbox.id)
            try:
                await client.wait_for_creation(sandbox_id, max_attempts=self.max_wait_attempts)
                try:
                    response = await client.execute_command(
                        sandbox_id=sandbox_id,
                        command=self._build_command(request),
                        timeout=max(1, int(request.timeout_seconds)),
                    )
                except TimeoutError as exc:
                    timeout_error = str(exc) or "remote task timed out"
            finally:
                cleanup = await self._cleanup(client, sandbox_id)
        usage = RemoteResourceUsage(wall_seconds=time.perf_counter() - started)
        lifecycle_event = RemoteExecutionEvent(
            sequence=1,
            event_type="lifecycle",
            message=request.lifecycle,
            fields={"session_id": sandbox_id, "cleanup": cleanup.succeeded},
        )
        if timeout_error:
            cleanup_detail = _cleanup_failure_detail(timeout_error, cleanup)
            return RemoteExecutionResult(
                task_id=request.task_id,
                provider="primeintellect",
                status="cleanup_error" if not cleanup.succeeded else "timeout",
                usage=usage,
                cleanup=cleanup,
                error=cleanup_detail if not cleanup.succeeded else timeout_error,
                session_id=sandbox_id,
                events=(lifecycle_event,),
            )
        if response is None:
            raise RuntimeError("Prime Intellect returned no command response")
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
        return replace(parsed, events=(lifecycle_event, *parsed.events))

    async def _execute_reuse_batch_once(
        self,
        requests: Sequence[RemoteExecutionRequest],
    ) -> tuple[RemoteExecutionResult, ...]:
        sandbox_client_cls, create_sandbox_request = _prime_sandboxes_sdk()
        first = requests[0]
        sandbox_id = ""
        pending: list[RemoteExecutionResult] = []
        cleanup = RemoteCleanupOutcome(attempted=False, succeeded=False)
        async with sandbox_client_cls(api_key=self.api_key) as client:
            self._validate_request(first)
            sandbox = await client.create(create_sandbox_request(**self._create_kwargs(first)))
            sandbox_id = str(sandbox.id)
            try:
                await client.wait_for_creation(sandbox_id, max_attempts=self.max_wait_attempts)
                for request in requests:
                    self._validate_request(request)
                    started = time.perf_counter()
                    try:
                        response = await client.execute_command(
                            sandbox_id=sandbox_id,
                            command=self._build_command(request),
                            timeout=max(1, int(request.timeout_seconds)),
                        )
                    except TimeoutError as exc:
                        pending.append(
                            RemoteExecutionResult(
                                task_id=request.task_id,
                                provider="primeintellect",
                                status="timeout",
                                usage=RemoteResourceUsage(wall_seconds=time.perf_counter() - started),
                                error=str(exc) or "remote task timed out",
                                session_id=sandbox_id,
                            )
                        )
                        continue
                    pending.append(
                        parse_remote_stdout(
                            request,
                            provider="primeintellect",
                            stdout=str(response.stdout),
                            stderr=str(response.stderr),
                            exit_code=int(response.exit_code),
                            usage=RemoteResourceUsage(wall_seconds=time.perf_counter() - started),
                            cleanup=RemoteCleanupOutcome(attempted=False, succeeded=True, resource_id=sandbox_id),
                            session_id=sandbox_id,
                        )
                    )
            finally:
                cleanup = await self._cleanup(client, sandbox_id)
        results: list[RemoteExecutionResult] = []
        for index, (_request, result) in enumerate(zip(requests, pending, strict=True)):
            status = "cleanup_error" if not cleanup.succeeded else result.status
            event = RemoteExecutionEvent(
                sequence=1,
                event_type="lifecycle",
                message="reuse_matched_trials",
                fields={"session_id": sandbox_id, "cohort_index": index, "cleanup": cleanup.succeeded},
            )
            results.append(
                replace(
                    result,
                    status=status,
                    cleanup=cleanup,
                    error=(_cleanup_failure_detail(result.error, cleanup) if status == "cleanup_error" else result.error),
                    events=(event, *result.events),
                )
            )
        return tuple(results)

    async def _cleanup(self, client: Any, sandbox_id: str) -> RemoteCleanupOutcome:
        if not sandbox_id:
            return RemoteCleanupOutcome(attempted=False, succeeded=False)
        try:
            await client.delete(sandbox_id)
        except Exception as exc:
            logger.debug("integrations.primeintellect.client: cleanup failed", exc_info=True)
            return RemoteCleanupOutcome(attempted=True, succeeded=False, resource_id=sandbox_id, detail=str(exc))
        return RemoteCleanupOutcome(attempted=True, succeeded=True, resource_id=sandbox_id)

    def _validate_request(self, request: RemoteExecutionRequest) -> None:
        capabilities = self.capabilities()
        expired_grants = [grant.name for grant in request.secret_grants if grant.expires_at <= time.time()]
        if expired_grants:
            raise ValueError(f"remote secret grant expired before dispatch: {', '.join(sorted(expired_grants))}")
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

    def _create_kwargs(self, request: RemoteExecutionRequest) -> dict[str, Any]:
        resources = request.resources
        kwargs: dict[str, Any] = {
            "name": f"autocontext-{_safe_name(request.task_id)}",
            "docker_image": request.image,
            "cpu_cores": resources.cpu_cores,
            "memory_gb": resources.memory_gb,
            "disk_size_gb": resources.disk_gb,
            "timeout_minutes": max(self.timeout_minutes, max(1, int(request.timeout_seconds // 60) + 1)),
            "network_access": request.network_policy == "allow" and self.network_access,
        }
        if resources.accelerator is not None:
            kwargs.update({"gpu_type": resources.accelerator.kind, "gpu_count": resources.accelerator.count})
            if resources.accelerator.memory_gb is not None:
                kwargs["gpu_memory_gb"] = resources.accelerator.memory_gb
        if request.snapshot_id:
            kwargs["snapshot_id"] = request.snapshot_id
        if request.secret_grants:
            kwargs["secret_grants"] = [grant.grant_id for grant in request.secret_grants]
        return kwargs

    def _build_command(self, request: RemoteExecutionRequest) -> str:
        parts: list[str] = []
        if request.input_artifacts:
            encoded = [
                {"name": artifact.name, "content": base64.b64encode(artifact.content).decode("ascii")}
                for artifact in request.input_artifacts
            ]
            bootstrap = (
                "import base64,json,pathlib\n"
                f"items=json.loads({json.dumps(json.dumps(encoded))})\n"
                "root=pathlib.Path.cwd().resolve()\n"
                "for item in items:\n"
                " p=(root/item['name']).resolve(); p.relative_to(root); p.parent.mkdir(parents=True,exist_ok=True); "
                "p.write_bytes(base64.b64decode(item['content']))\n"
            )
            parts.append("python - <<'PY'\n" + bootstrap + "PY")
        for name, value in sorted(request.environment.items()):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", name):
                raise ValueError(f"invalid remote environment name: {name!r}")
            parts.append(f"export {name}={shlex.quote(value)}")
        parts.append(request.command)
        return "\n".join(parts)

    def _emit_ledger(self, result: RemoteExecutionResult) -> None:
        if self.ledger_sink is not None:
            self.ledger_sink(result.to_ledger_entry())

    def fallback_local_response(self, scenario_name: str, seed: int) -> dict[str, Any]:
        """Return the historical caller-side recovery shape."""

        return {
            "result": {
                "score": 0.0,
                "winner": "incumbent",
                "summary": "primeintellect execution unavailable",
                "replay": [{"event": "remote_unavailable"}],
                "metrics": {"remote_available": 0.0},
                "validation_errors": ["remote execution unavailable"],
            },
            "replay": {
                "scenario": scenario_name,
                "seed": seed,
                "narrative": "Remote execution unavailable; fallback result generated.",
                "timeline": [{"event": "remote_unavailable"}],
            },
        }

    def unavailable_state(self, environment_name: str, reason: str) -> dict[str, Any]:
        return {"environment": environment_name, "status": "failed", "error": reason}

    def _build_eval_command(self, *, scenario_name: str, strategy: dict[str, Any], seed: int) -> str:
        """Compatibility helper; scenario logic lives in its packaged entrypoint."""

        request = build_builtin_scenario_remote_request(
            scenario_name,
            strategy,
            seed,
            ExecutionLimits(),
            image=self.docker_image,
            cpu_cores=self.cpu_cores,
            disk_gb=self.disk_size_gb,
            memory_gb=self.memory_gb,
        )
        return request.command


def _last_json_object(stdout: str) -> dict[str, Any]:
    for line in reversed(stdout.splitlines()):
        try:
            parsed = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
    return {}


def _safe_name(value: str) -> str:
    cleaned = "".join(char if char.isalnum() or char in {"-", "_"} else "-" for char in value)
    return cleaned[:48] or "task"


def _cleanup_failure_detail(primary_error: str, cleanup: RemoteCleanupOutcome) -> str:
    cleanup_error = cleanup.detail.strip() or "remote resource cleanup failed"
    return f"{primary_error}; cleanup failed: {cleanup_error}" if primary_error else cleanup_error


__all__ = [
    "MissingPrimeIntellectExtraError",
    "PrimeIntellectClient",
    "UnsupportedRemoteCapabilityError",
]
