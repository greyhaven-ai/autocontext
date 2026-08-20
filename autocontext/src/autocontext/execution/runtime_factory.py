"""Construct the configured scenario-execution data plane once.

Generation, campaign scheduling, and matched context evaluation must use the
same executor configuration.  Keeping construction here prevents those live
paths from silently drifting onto different local/remote runtimes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from autocontext.config.settings import AppSettings
from autocontext.execution.executors import LocalExecutor
from autocontext.execution.supervisor import ExecutionSupervisor
from autocontext.offline import require_online


@dataclass(frozen=True, slots=True)
class ExecutionRuntime:
    supervisor: ExecutionSupervisor
    remote_adapter: Any | None = None


def build_execution_runtime(
    settings: AppSettings,
    *,
    logger: logging.Logger | None = None,
) -> ExecutionRuntime:
    """Build the configured execution supervisor and optional remote adapter."""

    runtime_logger = logger or logging.getLogger(__name__)
    if settings.executor_mode == "primeintellect":
        require_online("use the PrimeIntellect executor", settings=settings)
        from autocontext.execution.executors.primeintellect import PrimeIntellectExecutor
        from autocontext.integrations.primeintellect.client import PrimeIntellectClient

        if not settings.primeintellect_api_key:
            raise ValueError("AUTOCONTEXT_PRIMEINTELLECT_API_KEY is required for primeintellect executor mode")
        remote = PrimeIntellectClient(
            api_key=settings.primeintellect_api_key,
            docker_image=settings.primeintellect_docker_image,
            cpu_cores=settings.primeintellect_cpu_cores,
            memory_gb=settings.primeintellect_memory_gb,
            disk_size_gb=settings.primeintellect_disk_size_gb,
            timeout_minutes=settings.primeintellect_timeout_minutes,
            max_wait_attempts=settings.primeintellect_wait_attempts,
            allow_fallback=settings.allow_primeintellect_fallback,
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


__all__ = ["ExecutionRuntime", "build_execution_runtime"]
