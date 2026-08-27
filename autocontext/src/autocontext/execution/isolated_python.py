"""Killable local isolation for restricted Python snippets.

This module is deliberately small and POSIX-only.  It forks a short-lived
child so callers can pass scenario objects without serializing them, while the
parent retains control of wall-clock timeout and process termination.  Child
results cross the boundary as size-limited JSON -- never pickle -- so a
compromised child cannot trigger deserialization code in the parent.

This is containment, not a filesystem or network sandbox.  The child receives
an empty environment, a private working directory, closed inherited file
descriptors, and best-effort resource limits.  It still runs as the invoking
user and can address host paths or the network if restricted Python execution
is bypassed.  Use a microVM/container sandbox for mutually untrusted tenants.
"""
from __future__ import annotations

import json
import math
import os
import select
import signal
import tempfile
import threading
import time
from collections.abc import Callable
from typing import Any

DEFAULT_MAX_MEMORY_MB = 256
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
DEFAULT_MAX_CHILD_TASKS = 64
_ERROR_MESSAGE_LIMIT = 2_000
_PROTOCOL_VERSION = 1


class IsolationUnavailableError(RuntimeError):
    """Raised before execution when the local isolation boundary is unavailable."""


class IsolatedExecutionTimeout(TimeoutError):
    """Raised after the isolated child exceeds its wall-clock budget."""


class IsolatedExecutionError(RuntimeError):
    """Raised when the isolated child fails or returns an invalid response."""


class IsolatedOutputLimitError(IsolatedExecutionError):
    """Raised when the child attempts to return more than its IPC allowance."""


def local_isolation_available() -> bool:
    """Return whether this call site can safely start the local child boundary."""
    return (
        os.name == "posix"
        and hasattr(os, "fork")
        and hasattr(os, "waitid")
        and hasattr(os, "WNOWAIT")
        and threading.current_thread() is threading.main_thread()
    )


def run_isolated_json(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> Any:
    """Run ``fn`` in a killable child and return its JSON-compatible result.

    The function fails closed on non-POSIX platforms and from worker threads.
    ``fork`` is used so scenario and strategy objects need not be picklable.
    Only bounded JSON is accepted from the child; Python pickle is never used
    across the trust boundary.
    """
    if not local_isolation_available():
        raise IsolationUnavailableError(
            "local Python isolation requires POSIX waitid support and the process main thread"
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
    if max_memory_mb < 32:
        raise ValueError("max_memory_mb must be at least 32")
    if max_output_bytes < 1_024:
        raise ValueError("max_output_bytes must be at least 1024")

    try:
        read_fd, write_fd = os.pipe()
    except OSError as exc:
        raise IsolationUnavailableError("unable to create the isolation result pipe") from exc
    with tempfile.TemporaryDirectory(
        prefix="autocontext-isolated-",
        ignore_cleanup_errors=True,
    ) as work_dir:
        try:
            pid = os.fork()
        except OSError as exc:
            os.close(read_fd)
            os.close(write_fd)
            raise IsolationUnavailableError("unable to start the isolated child") from exc
        if pid == 0:
            os.close(read_fd)
            _run_child(
                write_fd,
                work_dir,
                fn,
                timeout_seconds=timeout_seconds,
                max_memory_mb=max_memory_mb,
                max_output_bytes=max_output_bytes,
            )

        os.close(write_fd)
        try:
            try:
                raw, status = _collect_child(
                    pid,
                    read_fd,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                )
            except (IsolatedExecutionTimeout, IsolatedOutputLimitError):
                # These paths already terminate and reap before raising.
                raise
            except BaseException:
                # Operator interrupts and low-level select/read/wait failures
                # must not orphan a hostile child or its process group.
                _terminate_process_tree(pid)
                raise
        finally:
            try:
                os.close(read_fd)
            except OSError:
                pass

    if not raw:
        detail = _describe_wait_status(status)
        raise IsolatedExecutionError(f"isolated child exited without a response ({detail})")

    try:
        response = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise IsolatedExecutionError("isolated child returned malformed JSON") from exc
    if not isinstance(response, dict) or response.get("version") != _PROTOCOL_VERSION:
        raise IsolatedExecutionError("isolated child returned an invalid protocol response")

    response_status = response.get("status")
    if response_status == "ok":
        return response.get("value")
    if response_status == "output_limit":
        raise IsolatedOutputLimitError("isolated child result exceeded the output limit")
    if response_status == "error":
        error_type = response.get("error_type", "Exception")
        message = response.get("message", "isolated execution failed")
        if not isinstance(error_type, str) or not isinstance(message, str):
            raise IsolatedExecutionError("isolated child returned an invalid error response")
        raise IsolatedExecutionError(f"{error_type}: {message}")
    raise IsolatedExecutionError("isolated child returned an unknown response status")


def _run_child(
    write_fd: int,
    work_dir: str,
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
    max_memory_mb: int,
    max_output_bytes: int,
) -> None:
    """Child entrypoint.  This function never returns to inherited caller code."""
    try:
        try:
            os.setsid()
        except OSError:
            pass
        _harden_child(
            write_fd,
            work_dir,
            timeout_seconds=timeout_seconds,
            max_memory_mb=max_memory_mb,
            max_output_bytes=max_output_bytes,
        )
        try:
            response: dict[str, Any] = {
                "version": _PROTOCOL_VERSION,
                "status": "ok",
                "value": fn(),
            }
        except BaseException as exc:  # child must report failures without escaping the boundary
            response = {
                "version": _PROTOCOL_VERSION,
                "status": "error",
                "error_type": type(exc).__name__[:128],
                "message": str(exc)[:_ERROR_MESSAGE_LIMIT],
            }

        try:
            encoded = json.dumps(
                response,
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            ).encode("utf-8")
        except BaseException as exc:
            encoded = json.dumps(
                {
                    "version": _PROTOCOL_VERSION,
                    "status": "error",
                    "error_type": type(exc).__name__[:128],
                    "message": "isolated result is not JSON-compatible",
                },
                separators=(",", ":"),
            ).encode("utf-8")

        if len(encoded) > max_output_bytes:
            encoded = json.dumps(
                {"version": _PROTOCOL_VERSION, "status": "output_limit"},
                separators=(",", ":"),
            ).encode("utf-8")
        _write_all(write_fd, encoded)
    except BaseException:
        pass
    finally:
        try:
            os.close(write_fd)
        except OSError:
            pass
        os._exit(0)


def _harden_child(
    write_fd: int,
    work_dir: str,
    *,
    timeout_seconds: float,
    max_memory_mb: int,
    max_output_bytes: int,
) -> None:
    """Reduce ambient capabilities and apply best-effort POSIX limits."""
    devnull = os.open(os.devnull, os.O_RDWR)
    try:
        for standard_fd in (0, 1, 2):
            os.dup2(devnull, standard_fd)
    finally:
        if devnull > 2 and devnull != write_fd:
            os.close(devnull)

    _close_inherited_fds(write_fd)
    os.environ.clear()
    os.umask(0o077)
    os.chdir(work_dir)
    _apply_resource_limits(
        timeout_seconds=timeout_seconds,
        max_memory_mb=max_memory_mb,
        max_output_bytes=max_output_bytes,
    )


def _close_inherited_fds(preserve_fd: int) -> None:
    """Close inherited descriptors so host files/sockets do not leak to snippets."""
    for fd_root in ("/proc/self/fd", "/dev/fd"):
        try:
            descriptors = [int(value) for value in os.listdir(fd_root) if value.isdigit()]
        except OSError:
            continue
        for fd in descriptors:
            if fd > 2 and fd != preserve_fd:
                try:
                    os.close(fd)
                except OSError:
                    pass
        return

    # Conservative fallback for POSIX systems without an fd pseudo-filesystem.
    upper_bound = 4_096
    if preserve_fd > 3:
        os.closerange(3, preserve_fd)
    if preserve_fd + 1 < upper_bound:
        os.closerange(preserve_fd + 1, upper_bound)


def _apply_resource_limits(
    *,
    timeout_seconds: float,
    max_memory_mb: int,
    max_output_bytes: int,
) -> None:
    try:
        import resource
    except ImportError:
        return

    memory_bytes = max_memory_mb * 1024 * 1024
    cpu_seconds = max(1, math.ceil(timeout_seconds))
    requested: list[tuple[int, int]] = [
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, max_output_bytes),
        (resource.RLIMIT_NOFILE, 32),
    ]
    for name in ("RLIMIT_AS", "RLIMIT_DATA"):
        limit = getattr(resource, name, None)
        if limit is not None:
            requested.append((limit, memory_bytes))
    process_limit = getattr(resource, "RLIMIT_NPROC", None)
    if process_limit is not None:
        # RLIMIT_NPROC also counts pthreads on Linux.  A zero limit therefore
        # broke supported injected capabilities such as ``llm_batch`` before
        # they could start their bounded helper pool.  Keep a small ceiling as
        # defense in depth; this is a per-UID kernel limit, not a substitute for
        # the per-cgroup/process isolation required for hostile multi-tenant code.
        requested.append((process_limit, DEFAULT_MAX_CHILD_TASKS))

    for resource_id, value in requested:
        try:
            _, hard = resource.getrlimit(resource_id)
            effective = value if hard == resource.RLIM_INFINITY else min(value, hard)
            resource.setrlimit(resource_id, (effective, effective))
        except (OSError, ValueError):
            # Some kernels expose a limit but do not enforce or permit lowering it.
            continue


def _write_all(fd: int, payload: bytes) -> None:
    view = memoryview(payload)
    while view:
        written = os.write(fd, view)
        view = view[written:]


def _collect_child(
    pid: int,
    read_fd: int,
    *,
    timeout_seconds: float,
    max_output_bytes: int,
) -> tuple[bytes, int]:
    deadline = time.monotonic() + timeout_seconds
    output = bytearray()
    exited = False
    eof = False

    # Observe child exit without reaping it.  Keeping the leader as a zombie
    # reserves ``pid`` until every process-group cleanup decision is complete,
    # so a later killpg cannot target a newly reused group id.
    while not exited or not eof:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            _terminate_process_tree(pid)
            raise IsolatedExecutionTimeout(
                f"isolated execution exceeded {timeout_seconds:.3g}s"
            )

        if not exited:
            exited = _child_exited_without_reaping(pid)

        readable, _, _ = select.select([read_fd], [], [], min(remaining, 0.05))
        if readable:
            chunk = os.read(read_fd, 65_536)
            if chunk:
                output.extend(chunk)
                if len(output) > max_output_bytes:
                    _terminate_process_tree(pid)
                    raise IsolatedOutputLimitError(
                        "isolated child response exceeded the output limit"
                    )
            else:
                eof = True

        if exited and not eof and not readable:
            # A descendant may have inherited the pipe.  The child is complete;
            # terminate its process group so the response cannot be held open.
            _signal_process_group(pid, signal.SIGKILL)

    _, status = os.waitpid(pid, 0)
    return bytes(output), status


def _child_exited_without_reaping(pid: int) -> bool:
    """Use waitid/WNOWAIT so the child pid cannot be reused before cleanup."""
    waitid = getattr(os, "waitid", None)
    no_wait = getattr(os, "WNOWAIT", None)
    process_id_type = getattr(os, "P_PID", None)
    if waitid is None or no_wait is None or process_id_type is None:
        return False
    try:
        info = waitid(process_id_type, pid, os.WEXITED | os.WNOHANG | no_wait)
    except ChildProcessError:
        return True
    return info is not None


def _terminate_process_tree(pid: int) -> None:
    """Terminate the child group while its unreaped leader prevents id reuse."""
    if not _child_is_still_owned(pid):
        return
    _signal_process_group(pid, signal.SIGTERM)
    try:
        os.kill(pid, signal.SIGTERM)
    except ProcessLookupError:
        pass

    # Give cooperative descendants a short grace period, but always follow
    # with SIGKILL.  Reaping the leader first would permit process-group id
    # reuse and make that final group signal unsafe.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline and not _child_exited_without_reaping(pid):
        time.sleep(0.01)

    _signal_process_group(pid, signal.SIGKILL)
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    try:
        os.waitpid(pid, 0)
    except ChildProcessError:
        pass


def _child_is_still_owned(pid: int) -> bool:
    """Return false once the pid is no longer an unreaped child of this process."""
    waitid = getattr(os, "waitid", None)
    process_id_type = getattr(os, "P_PID", None)
    no_wait = getattr(os, "WNOWAIT", None)
    if waitid is None or process_id_type is None or no_wait is None:
        return False
    try:
        waitid(process_id_type, pid, os.WEXITED | os.WNOHANG | no_wait)
    except ChildProcessError:
        return False
    return True


def _signal_process_group(pid: int, signum: int) -> None:
    try:
        os.killpg(pid, signum)
    except (ProcessLookupError, PermissionError):
        pass


def _describe_wait_status(status: int) -> str:
    if os.WIFSIGNALED(status):
        return f"signal {os.WTERMSIG(status)}"
    if os.WIFEXITED(status):
        return f"exit {os.WEXITSTATUS(status)}"
    return "unknown status"
