"""Typed propagation for terminal remote-execution outcomes."""

from __future__ import annotations

from autocontext.execution.remote_execution import RemoteExecutionResult


class RemoteExecutionError(RuntimeError):
    """Base for remote failures that carry an application retry disposition."""

    @property
    def retryable(self) -> bool:
        raise NotImplementedError


class RemoteExecutionFailure(RemoteExecutionError):
    """An executor failure with a durable provider retry disposition.

    Application orchestration must use ``retryable`` instead of treating this
    as an ordinary transient exception. The complete remote result remains
    available for accounting, cleanup evidence, and operator diagnostics.
    """

    def __init__(self, result: RemoteExecutionResult, *, detail: str | None = None) -> None:
        self.result = result
        message = detail or f"{result.provider} {result.status}: {result.error}"
        super().__init__(message)

    @property
    def retryable(self) -> bool:
        return self.result.retryable


class RemoteExecutionAccountingError(RemoteExecutionError):
    """A durable accounting boundary failed after work may have been claimed.

    There is no trustworthy result to carry, so application orchestration must
    fail closed rather than create a new task identity and possibly repeat paid
    work. Retrying the same durable identity after reconciliation remains an
    operator-level action, not an automatic generation retry.
    """

    @property
    def retryable(self) -> bool:
        return False


__all__ = [
    "RemoteExecutionAccountingError",
    "RemoteExecutionError",
    "RemoteExecutionFailure",
]
