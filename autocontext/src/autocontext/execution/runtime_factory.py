"""Construct the configured scenario-execution data plane once.

Generation, campaign scheduling, and matched context evaluation must use the
same executor configuration.  Keeping construction here prevents those live
paths from silently drifting onto different local/remote runtimes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from autocontext.config.production_execution import parse_csv_values
from autocontext.config.settings import AppSettings
from autocontext.execution.executors import LocalExecutor
from autocontext.execution.external_eval_outbox import ExternalEvalLedgerOutbox, ExternalEvalOutboxStatus
from autocontext.execution.remote_execution import (
    RemoteAcceleratorRequest,
    RemoteExecutionRequirements,
    RemoteExecutionResult,
    RemoteProviderCapabilities,
    RemoteResourceRequest,
)
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.offline import require_online


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    supervisor: ExecutionSupervisor
    remote_adapter: Any | None = None
    remote_ledger_outbox: ExternalEvalLedgerOutbox | None = None

    def take_remote_result(self, task_id: str) -> RemoteExecutionResult | None:
        take = getattr(self.supervisor.executor, "take_remote_result", None)
        result = take(task_id) if callable(take) else None
        return result if isinstance(result, RemoteExecutionResult) else None

    def unresolved_remote_evaluations(self) -> tuple[ExternalEvalOutboxStatus, ...]:
        if self.remote_ledger_outbox is None:
            return ()
        return self.remote_ledger_outbox.statuses(unresolved_only=True)

    def validated_remote_outbox_instance_id(self) -> str | None:
        outbox = self.remote_ledger_outbox
        adapter_outbox = getattr(self.remote_adapter, "ledger_outbox", None)
        executor_client = getattr(self.supervisor.executor, "client", None)
        executor_outbox = getattr(executor_client, "ledger_outbox", None)
        if outbox is None:
            if adapter_outbox is not None or executor_outbox is not None:
                raise ValueError("Prime campaign runtime components must share one external-evaluation outbox instance")
            return None
        instance_id = outbox.instance_id
        if adapter_outbox is not outbox or executor_outbox is not outbox:
            raise ValueError("Prime campaign runtime components must share one external-evaluation outbox instance")
        return instance_id


def build_execution_runtime(
    settings: AppSettings,
    *,
    logger: logging.Logger | None = None,
    remote_ledger_outbox: ExternalEvalLedgerOutbox | None = None,
) -> ExecutionRuntime:
    """Build the configured execution supervisor and optional remote adapter."""

    runtime_logger = logger or logging.getLogger(__name__)
    if settings.executor_mode == "primeintellect":
        from autocontext.execution.executors.primeintellect import PrimeIntellectExecutor
        from autocontext.integrations.primeintellect.client import PrimeIntellectClient

        ledger_outbox = remote_ledger_outbox or ExternalEvalLedgerOutbox(
            settings.runs_root / "external-evaluations" / "prime-ledger.sqlite3"
        )
        unresolved = ledger_outbox.statuses(unresolved_only=True)
        if unresolved:
            runtime_logger.error(
                "Prime external-evaluation accounting requires reconciliation for %d durable outbox entr%s at %s",
                len(unresolved),
                "y" if len(unresolved) == 1 else "ies",
                ledger_outbox.path,
            )
        remote = PrimeIntellectClient(
            # A completed result is local durable state and must remain
            # recoverable after credentials are removed. The client validates
            # this credential again immediately before every new dispatch.
            api_key=settings.primeintellect_api_key or "",
            docker_image=settings.primeintellect_docker_image,
            cpu_cores=settings.primeintellect_cpu_cores,
            memory_gb=settings.primeintellect_memory_gb,
            disk_size_gb=settings.primeintellect_disk_size_gb,
            timeout_minutes=settings.primeintellect_timeout_minutes,
            max_wait_attempts=settings.primeintellect_wait_attempts,
            allow_fallback=settings.allow_primeintellect_fallback,
            default_requirements=prime_default_requirements(settings),
            resource_capabilities=prime_resource_capabilities(settings),
            ledger_outbox=ledger_outbox,
            offline=settings.offline,
        )
        return ExecutionRuntime(
            supervisor=ExecutionSupervisor(
                executor=PrimeIntellectExecutor(
                    remote,
                    max_retries=settings.primeintellect_max_retries,
                    backoff_seconds=settings.primeintellect_backoff_seconds,
                )
            ),
            remote_adapter=remote,
            remote_ledger_outbox=ledger_outbox,
        )
    if settings.executor_mode == "monty":
        from autocontext.execution.executors.monty import MontyExecutor

        return ExecutionRuntime(
            supervisor=ExecutionSupervisor(
                executor=MontyExecutor(
                    max_execution_time_seconds=settings.monty_max_execution_time_seconds,
                    max_external_calls=settings.monty_max_external_calls,
                )
            )
        )
    if settings.executor_mode == "ssh":
        require_online("use the SSH executor", settings=settings, detail=settings.ssh_host)
        if not settings.ssh_host:
            raise ValueError("AUTOCONTEXT_SSH_HOST is required for ssh executor mode")
        from autocontext.execution.executors.ssh import SSHExecutor
        from autocontext.integrations.ssh.client import SSHClient
        from autocontext.integrations.ssh.config import SSHHostConfig

        ssh_client = SSHClient(
            SSHHostConfig(
                name=settings.ssh_host,
                hostname=settings.ssh_host,
                port=settings.ssh_port,
                user=settings.ssh_user,
                identity_file=settings.ssh_identity_file,
                working_directory=settings.ssh_working_directory,
                connect_timeout=settings.ssh_connect_timeout,
                command_timeout=settings.ssh_command_timeout,
            )
        )
        try:
            ssh_client.validate_runtime()
        except RuntimeError as exc:
            if not settings.ssh_allow_fallback:
                raise
            runtime_logger.warning("SSH executor preflight failed; falling back to local executor: %s", exc)
            return ExecutionRuntime(supervisor=ExecutionSupervisor(executor=LocalExecutor()))
        return ExecutionRuntime(
            supervisor=ExecutionSupervisor(
                executor=SSHExecutor(
                    client=ssh_client,
                    allow_fallback=settings.ssh_allow_fallback,
                )
            )
        )
    if settings.executor_mode == "gondolin":
        raise ValueError(
            "Gondolin executor mode is reserved for the optional microVM sandbox backend and is not wired yet. "
            "Use monty for in-process sandboxing, or local/ssh/primeintellect for supported executors."
        )
    if settings.executor_mode == "local":
        return ExecutionRuntime(supervisor=ExecutionSupervisor(executor=LocalExecutor()))
    raise ValueError(f"Unsupported executor mode: {settings.executor_mode!r}")


def prime_default_requirements(settings: AppSettings) -> RemoteExecutionRequirements:
    kind = settings.primeintellect_accelerator_kind.strip()
    accelerator = RemoteAcceleratorRequest(kind=kind, count=settings.primeintellect_accelerator_count) if kind else None
    required = parse_csv_values(settings.primeintellect_required_telemetry) if accelerator is not None else frozenset()
    return RemoteExecutionRequirements(
        image=settings.primeintellect_docker_image,
        resources=RemoteResourceRequest(
            cpu_cores=settings.primeintellect_cpu_cores,
            memory_gb=settings.primeintellect_memory_gb,
            disk_gb=settings.primeintellect_disk_size_gb,
            accelerator=accelerator,
        ),
        region=settings.primeintellect_region.strip() or None,
        required_telemetry=required,  # type: ignore[arg-type]
    )


def prime_resource_capabilities(settings: AppSettings) -> RemoteProviderCapabilities:
    kinds = parse_csv_values(settings.primeintellect_supported_accelerator_kinds)
    return RemoteProviderCapabilities(
        images=parse_csv_values(settings.primeintellect_supported_images),
        regions=parse_csv_values(settings.primeintellect_supported_regions),
        accelerator_limits={kind: settings.primeintellect_max_accelerator_count for kind in kinds},
        telemetry=parse_csv_values(settings.primeintellect_available_telemetry),  # type: ignore[arg-type]
    )


__all__ = [
    "ExecutionRuntime",
    "build_execution_runtime",
    "prime_default_requirements",
    "prime_resource_capabilities",
]
