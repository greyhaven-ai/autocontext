"""Concrete cancellable adapters for campaign-auditor model clients."""

from __future__ import annotations

import concurrent.futures
import multiprocessing
import os
import signal
import threading
import time
from dataclasses import dataclass
from multiprocessing.connection import Connection
from typing import Any

from autocontext.audit.campaign_audit_transport import (
    AuditorCallHandle,
    AuditorModelClient,
    AuditorModelResponse,
    AuditorSubmissionNotStartedError,
)


@dataclass(frozen=True, slots=True)
class _Usage:
    input_tokens: int
    output_tokens: int


@dataclass(slots=True)
class _Response:
    text: str
    usage: _Usage


_PROCESS_READY_TIMEOUT_SECONDS = 5.0
_PROCESS_TERM_TIMEOUT_SECONDS = 1.5
_PROCESS_KILL_TIMEOUT_SECONDS = 1.5
_WORKER_REAP_TIMEOUT_SECONDS = 0.75
_DISPATCH_MESSAGE = "dispatch"


class ProcessAuditorCallHandle:
    """A call handle whose cancellation terminates the owning client process."""

    def __init__(
        self,
        process: multiprocessing.Process,
        connection: Connection,
        *,
        process_group_id: int,
    ) -> None:
        self._process = process
        self._connection = connection
        self._process_group_id = process_group_id
        self._lock = threading.Lock()
        self._resolved = False

    def result(self, timeout: float) -> AuditorModelResponse:
        with self._lock:
            if self._resolved:
                raise RuntimeError("auditor call handle has already been resolved")
            if not self._connection.poll(timeout):
                raise concurrent.futures.TimeoutError
            try:
                ok, payload = self._connection.recv()
            except EOFError as exc:
                raise RuntimeError("auditor client process exited without a response") from exc
            finally:
                self._connection.close()
                self._resolved = self._stop_process()
            if not self._resolved:
                raise RuntimeError("auditor client process could not be terminated after responding")
            if not ok:
                raise RuntimeError(str(payload))
            if not isinstance(payload, _Response):
                raise TypeError("auditor client process returned an invalid response")
            return payload

    def cancel(self) -> bool:
        with self._lock:
            if self._resolved:
                return not self.is_alive
            self._resolved = self._stop_process()
            self._connection.close()
            return self._resolved

    @property
    def is_alive(self) -> bool:
        if self._resolved:
            return False
        return self._process.is_alive() or _process_group_exists(self._process_group_id)

    def _stop_process(self) -> bool:
        return _stop_isolated_process(self._process, self._process_group_id)


class ProcessCancellableAuditorModelClient:
    """Run a synchronous model client in a killable process per audit call."""

    def __init__(self, client: AuditorModelClient, *, start_method: str | None = None) -> None:
        methods = multiprocessing.get_all_start_methods()
        resolved_method = start_method or ("fork" if "fork" in methods else "spawn")
        if resolved_method not in methods:
            raise ValueError(f"unsupported multiprocessing start method: {resolved_method}")
        self._client = client
        self._context: Any = multiprocessing.get_context(resolved_method)

    def generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> AuditorModelResponse:
        return self._client.generate(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
        )

    def start_generate(
        self,
        *,
        model: str,
        prompt: str,
        max_tokens: int,
        temperature: float,
        role: str = "",
    ) -> AuditorCallHandle:
        if not _process_group_isolation_supported():
            raise AuditorSubmissionNotStartedError(
                "auditor client process isolation requires POSIX sessions and process groups",
            )

        response_parent: Connection | None = None
        response_child: Connection | None = None
        control_parent: Connection | None = None
        control_child: Connection | None = None
        process: multiprocessing.Process | None = None
        process_group_id: int | None = None
        dispatch_release_started = False
        try:
            response_parent, response_child = self._context.Pipe(duplex=False)
            control_parent, control_child = self._context.Pipe(duplex=True)
            process = self._context.Process(
                target=_run_generate,
                args=(
                    self._client,
                    response_child,
                    control_child,
                    model,
                    prompt,
                    max_tokens,
                    temperature,
                    role,
                ),
                daemon=True,
            )
            process.start()
            response_child.close()
            response_child = None
            control_child.close()
            control_child = None

            if not control_parent.poll(_PROCESS_READY_TIMEOUT_SECONDS):
                raise AuditorSubmissionNotStartedError(
                    "auditor client process did not prove process-group isolation",
                )
            ready = control_parent.recv()
            process_group_id = _validated_process_group(process, ready)
            handle = ProcessAuditorCallHandle(
                process,
                response_parent,
                process_group_id=process_group_id,
            )
            dispatch_release_started = True
            control_parent.send(_DISPATCH_MESSAGE)
            control_parent.close()
            control_parent = None
            return handle
        except Exception as exc:
            _close_connections(response_parent, response_child, control_parent, control_child)
            if process is not None and getattr(process, "pid", None) is not None:
                if process_group_id is None:
                    _stop_unisolated_process(process)
                else:
                    _stop_isolated_process(process, process_group_id)
            if isinstance(exc, AuditorSubmissionNotStartedError):
                raise
            if dispatch_release_started:
                raise RuntimeError("auditor client failed while releasing provider dispatch") from exc
            raise AuditorSubmissionNotStartedError("auditor client process could not start") from exc


def build_cancellable_auditor_client(
    client: AuditorModelClient,
    *,
    process_start_method: str | None = None,
) -> ProcessCancellableAuditorModelClient:
    """Wrap an existing synchronous client in a hard-cancellable boundary."""

    return ProcessCancellableAuditorModelClient(client, start_method=process_start_method)


def _run_generate(
    client: AuditorModelClient,
    connection: Connection,
    control: Connection,
    model: str,
    prompt: str,
    max_tokens: int,
    temperature: float,
    role: str,
) -> None:
    try:
        process_id = _establish_process_group()
        control.send((True, process_id, os.getpgrp(), os.getsid(0)))
        if control.recv() != _DISPATCH_MESSAGE:
            return
    except BaseException as exc:
        try:
            control.send((False, f"{type(exc).__name__}: {exc}"))
        except BaseException:
            pass
        connection.close()
        return
    finally:
        control.close()

    try:
        response = client.generate(
            model=model,
            prompt=prompt,
            max_tokens=max_tokens,
            temperature=temperature,
            role=role,
        )
        text = response.text
        if not isinstance(text, str):
            raise TypeError("auditor response text must be a string")
        usage = getattr(response, "usage", None)
        connection.send(
            (
                True,
                _Response(
                    text=text,
                    usage=_Usage(
                        input_tokens=_usage_int(usage, "input_tokens"),
                        output_tokens=_usage_int(usage, "output_tokens"),
                    ),
                ),
            )
        )
    except BaseException as exc:
        try:
            connection.send((False, f"{type(exc).__name__}: {exc}"))
        except BaseException:
            pass
    finally:
        connection.close()
    # Keep the session leader alive until the parent has received the response
    # and terminated the complete group. This prevents a PID/PGID reuse race
    # and lets the parent prove that provider-spawned descendants are gone.
    while True:
        time.sleep(3600.0)


def _process_group_isolation_supported() -> bool:
    return os.name == "posix" and all(
        callable(getattr(os, name, None))
        for name in ("getpgrp", "getsid", "killpg", "setsid", "waitpid")
    )


def _establish_process_group() -> int:
    if not _process_group_isolation_supported():
        raise RuntimeError("POSIX process-group isolation is unavailable")
    os.setsid()
    process_id = os.getpid()
    if os.getpgrp() != process_id or os.getsid(0) != process_id:
        raise RuntimeError("auditor worker did not become an isolated session leader")
    signal.signal(signal.SIGTERM, _terminate_worker)
    return process_id


def _terminate_worker(signum: int, _frame: object) -> None:
    # TERM reaches every member of the group concurrently. Keep the leader
    # alive briefly to reap its direct provider children before exiting; KILL
    # remains the parent's bounded fallback for uncooperative descendants.
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    deadline = time.monotonic() + _WORKER_REAP_TIMEOUT_SECONDS
    while True:
        try:
            child_id, _status = os.waitpid(-1, os.WNOHANG)
        except ChildProcessError:
            break
        except OSError:
            break
        if child_id == 0:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.01)
    os._exit(128 + signum)


def _validated_process_group(process: multiprocessing.Process, ready: object) -> int:
    if (
        not isinstance(ready, tuple)
        or len(ready) != 4
        or ready[0] is not True
        or any(isinstance(value, bool) or not isinstance(value, int) for value in ready[1:])
    ):
        detail = ready[1] if isinstance(ready, tuple) and len(ready) > 1 else "invalid readiness response"
        raise AuditorSubmissionNotStartedError(f"auditor process isolation failed: {detail}")
    process_id, process_group_id, session_id = ready[1:]
    if process.pid is None or process_id != process.pid:
        raise AuditorSubmissionNotStartedError("auditor process reported an invalid process identity")
    if process_group_id != process_id or session_id != process_id:
        raise AuditorSubmissionNotStartedError("auditor process did not enter a dedicated POSIX session")
    return int(process_group_id)


def _stop_isolated_process(process: multiprocessing.Process, process_group_id: int) -> bool:
    if process_group_id <= 1 or process_group_id == os.getpgrp():
        _stop_unisolated_process(process)
        return False
    _signal_process_group(process_group_id, signal.SIGTERM)
    if _wait_for_process_group_exit(process, process_group_id, _PROCESS_TERM_TIMEOUT_SECONDS):
        return True
    _signal_process_group(process_group_id, signal.SIGKILL)
    if _wait_for_process_group_exit(process, process_group_id, _PROCESS_KILL_TIMEOUT_SECONDS):
        return True
    _stop_unisolated_process(process)
    return False


def _signal_process_group(process_group_id: int, requested_signal: signal.Signals) -> bool:
    try:
        os.killpg(process_group_id, requested_signal)
    except ProcessLookupError:
        return True
    except (OSError, ValueError):
        return False
    return True


def _wait_for_process_group_exit(
    process: multiprocessing.Process,
    process_group_id: int,
    timeout: float,
) -> bool:
    deadline = time.monotonic() + timeout
    while True:
        process.join(timeout=0)
        if not process.is_alive() and not _process_group_exists(process_group_id):
            return True
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return False
        time.sleep(min(0.01, remaining))


def _process_group_exists(process_group_id: int) -> bool:
    try:
        os.killpg(process_group_id, 0)
    except ProcessLookupError:
        return False
    except (OSError, ValueError):
        return True
    return True


def _stop_unisolated_process(process: multiprocessing.Process) -> None:
    process.join(timeout=0.05)
    if process.is_alive():
        process.terminate()
        process.join(timeout=1.0)
    if process.is_alive() and hasattr(process, "kill"):
        process.kill()
        process.join(timeout=1.0)


def _close_connections(*connections: Connection | None) -> None:
    for connection in connections:
        if connection is not None:
            connection.close()


def _usage_int(usage: Any, field: str) -> int:
    value = getattr(usage, field, 0)
    return value if isinstance(value, int) and value >= 0 else 0


__all__ = [
    "ProcessAuditorCallHandle",
    "ProcessCancellableAuditorModelClient",
    "build_cancellable_auditor_client",
]
