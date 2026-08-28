"""Killable local isolation for restricted Python snippets.

This module supports a deliberately narrow set of non-root Linux and macOS
hosts. It forks a short-lived child so callers can pass scenario objects without
serializing them, while the parent retains control of timeout and termination.
Child results cross the boundary as size-limited JSON -- never pickle -- so a
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
import selectors
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import Any, Literal

import autocontext.execution._isolation_platform as _isolation_platform
import autocontext.execution._isolation_seccomp as _isolation_seccomp
from autocontext.execution._isolation_protocol import (
    decode_isolated_response as _decode_isolated_response,
)
from autocontext.execution._isolation_protocol import (
    describe_wait_status as _describe_wait_status,
)
from autocontext.execution._isolation_protocol import write_all as _write_all
from autocontext.execution._process_group import signal_owned_process_group

DEFAULT_MAX_MEMORY_MB = 256
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576
DEFAULT_MAX_CHILD_TASKS = 64
_ERROR_MESSAGE_LIMIT = 2_000
_PROTOCOL_VERSION = 1
_ChildState = Literal["running", "exited", "ownership_lost"]
_STRANDED_CHILDREN: set[int] = set()
_STRANDED_CHILDREN_LOCK = threading.RLock()
_STRANDED_CHILD_REAPER: threading.Thread | None = None
_ISOLATION_OWNERSHIP_POISONED = False


class IsolationUnavailableError(RuntimeError):
    """Raised before execution when the local isolation boundary is unavailable."""


class IsolatedExecutionTimeout(TimeoutError):
    """Raised after the isolated child exceeds its wall-clock budget."""


class IsolatedExecutionError(RuntimeError):
    """Raised when the isolated child fails or returns an invalid response."""


class IsolatedOutputLimitError(IsolatedExecutionError):
    """Raised when the child attempts to return more than its IPC allowance."""


class _ChildOwnershipLost(IsolatedExecutionError):
    """The isolation leader was reaped outside this boundary."""


def local_isolation_available() -> bool:
    """Return whether this process can safely start the local child boundary."""
    native_thread_count = _native_thread_count()
    admission_is_safe = (
        os.name == "posix"
        and hasattr(os, "fork")
        and _child_ownership_primitives_available()
        and threading.current_thread() is threading.main_thread()
        and threading.active_count() == 1
        and native_thread_count == 1
        and _sigchld_disposition_is_safe()
        and _descendant_containment_supported()
    )
    if not admission_is_safe:
        return False
    return _registered_children_are_clear() and not _ISOLATION_OWNERSHIP_POISONED


def _registered_children_are_clear() -> bool:
    """Reap completed stranded leaders and block while ownership is uncertain."""
    global _ISOLATION_OWNERSHIP_POISONED
    with _STRANDED_CHILDREN_LOCK:
        if _ISOLATION_OWNERSHIP_POISONED:
            return False
        for pid in tuple(_STRANDED_CHILDREN):
            child_state = _child_state_without_reaping(pid)
            if child_state == "running":
                continue
            if child_state == "ownership_lost":
                _ISOLATION_OWNERSHIP_POISONED = True
                _STRANDED_CHILDREN.discard(pid)
                return False
            # Keep the exited leader unreaped while killing any surviving group
            # members, so its numeric process-group id cannot be reused.
            try:
                _signal_process_group(pid, signal.SIGKILL)
            except IsolatedExecutionError:
                _ISOLATION_OWNERSHIP_POISONED = True
                return False
            try:
                os.waitpid(pid, 0)
            except ChildProcessError:
                _ISOLATION_OWNERSHIP_POISONED = True
                _STRANDED_CHILDREN.discard(pid)
                return False
            _STRANDED_CHILDREN.discard(pid)
        return not _STRANDED_CHILDREN


def _register_stranded_child(pid: int) -> None:
    global _STRANDED_CHILD_REAPER
    reaper_to_start: threading.Thread | None = None
    with _STRANDED_CHILDREN_LOCK:
        _STRANDED_CHILDREN.add(pid)
        if _STRANDED_CHILD_REAPER is None:
            reaper_to_start = threading.Thread(
                target=_reap_stranded_children,
                name="autocontext-isolated-child-reaper",
                daemon=True,
            )
            _STRANDED_CHILD_REAPER = reaper_to_start
    if reaper_to_start is not None:
        try:
            reaper_to_start.start()
        except RuntimeError:
            # The registry still blocks later forks and lets the next
            # availability probe reap the child synchronously.
            with _STRANDED_CHILDREN_LOCK:
                if _STRANDED_CHILD_REAPER is reaper_to_start:
                    _STRANDED_CHILD_REAPER = None


def _reap_stranded_children() -> None:
    """Bound zombie lifetime after a child misses the termination deadline."""
    global _STRANDED_CHILD_REAPER
    try:
        while True:
            if _registered_children_are_clear():
                return
            with _STRANDED_CHILDREN_LOCK:
                if _ISOLATION_OWNERSHIP_POISONED or not _STRANDED_CHILDREN:
                    return
            time.sleep(0.05)
    finally:
        with _STRANDED_CHILDREN_LOCK:
            if _STRANDED_CHILD_REAPER is threading.current_thread():
                _STRANDED_CHILD_REAPER = None


def _child_ownership_lost(message: str) -> _ChildOwnershipLost:
    global _ISOLATION_OWNERSHIP_POISONED
    with _STRANDED_CHILDREN_LOCK:
        _ISOLATION_OWNERSHIP_POISONED = True
    return _ChildOwnershipLost(message)


def _sigchld_disposition_is_safe() -> bool:
    """A custom/ignored SIGCHLD disposition may reap the leader behind us."""
    try:
        return signal.getsignal(signal.SIGCHLD) is signal.SIG_DFL
    except (AttributeError, OSError, ValueError):
        return False


def _child_ownership_primitives_available() -> bool:
    """Require every primitive used to observe the child without reaping it."""
    return (
        getattr(os, "waitid", None) is not None
        and getattr(os, "P_PID", None) is not None
        and getattr(os, "WNOWAIT", None) is not None
        and getattr(os, "WEXITED", None) is not None
        and getattr(os, "WNOHANG", None) is not None
    )


def _native_thread_count() -> int | None:
    return _isolation_platform._native_thread_count()


def _darwin_native_thread_count() -> int | None:
    return _isolation_platform._darwin_native_thread_count()


def _safe_unprivileged_uid() -> int | None:
    return _isolation_platform._safe_unprivileged_uid()


def _descendant_containment_supported() -> bool:
    """Return whether the child can be prevented from escaping its kill group."""
    if _safe_unprivileged_uid() is None:
        return False
    if sys.platform.startswith("linux"):
        try:
            machine = os.uname().machine.lower()
        except (AttributeError, OSError):
            return False
        capability_masks = _linux_capability_masks()
        return (
            machine in {"aarch64", "amd64", "arm64", "x86_64"}
            and capability_masks is not None
            and not any(capability_masks)
        )
    if sys.platform == "darwin":
        try:
            import resource

            return hasattr(resource, "RLIMIT_NPROC")
        except (AttributeError, ImportError, OSError):
            return False
    return False


@contextmanager
def _temporary_isolation_directory() -> Iterator[str]:
    """Enter the private work directory before allocating result-pipe FDs."""
    temporary_directory: tempfile.TemporaryDirectory[str] | None = None
    try:
        temporary_directory = tempfile.TemporaryDirectory(
            prefix="autocontext-isolated-",
            ignore_cleanup_errors=True,
        )
        work_dir = temporary_directory.__enter__()
    except OSError as exc:
        if temporary_directory is not None:
            temporary_directory.cleanup()
        raise IsolationUnavailableError(
            "unable to create the isolation working directory"
        ) from exc
    try:
        yield work_dir
    finally:
        temporary_directory.__exit__(None, None, None)


def _open_isolation_result_pipe() -> tuple[int, int]:
    """Create a non-stdio result pipe even when the host launched without stdio."""
    read_fd, write_fd = os.pipe()
    descriptors = [read_fd, write_fd]
    opened_descriptors = set(descriptors)
    try:
        if any(fd <= 2 for fd in descriptors):
            import fcntl

            for index, descriptor in enumerate(descriptors):
                if descriptor > 2:
                    continue
                replacement = fcntl.fcntl(descriptor, fcntl.F_DUPFD_CLOEXEC, 3)
                opened_descriptors.add(replacement)
                descriptors[index] = replacement
            for descriptor in (read_fd, write_fd):
                if descriptor > 2:
                    continue
                os.close(descriptor)
                opened_descriptors.discard(descriptor)
        return descriptors[0], descriptors[1]
    except BaseException:
        for descriptor in opened_descriptors:
            try:
                os.close(descriptor)
            except OSError:
                pass
        raise


def run_isolated_json(
    fn: Callable[[], Any],
    *,
    timeout_seconds: float,
    max_memory_mb: int = DEFAULT_MAX_MEMORY_MB,
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
) -> Any:
    """Run ``fn`` in a killable child and return its JSON-compatible result.

    The function fails closed on unsupported or multithreaded processes.
    ``fork`` is used so scenario and strategy objects need not be picklable.
    Only bounded JSON is accepted from the child; Python pickle is never used
    across the trust boundary. On Linux, ``max_memory_mb`` bounds virtual
    address-space growth beyond mappings inherited at fork; stricter inherited
    ``RLIMIT_AS`` caps remain in force.
    """
    if not local_isolation_available():
        raise IsolationUnavailableError(
            "local Python isolation requires the main thread of a single-threaded, "
            "non-root supported Linux or macOS host with native-thread accounting, "
            "fork/waitid, and enforceable child-process containment"
        )
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise ValueError("timeout_seconds must be a positive finite number")
    if max_memory_mb < 32:
        raise ValueError("max_memory_mb must be at least 32")
    if max_output_bytes < 1_024:
        raise ValueError("max_output_bytes must be at least 1024")

    with _temporary_isolation_directory() as work_dir:
        try:
            read_fd, write_fd = _open_isolation_result_pipe()
        except OSError as exc:
            raise IsolationUnavailableError(
                "unable to create the isolation result pipe"
            ) from exc
        try:
            child_exit = os._exit
            # Current-thread trace/profile callbacks can run before the child
            # reaches ``_run_child``. Fork with them disabled, then restore the
            # exact callbacks only in the parent. The helper performs the final
            # isolation precondition checks after disabling those callbacks.
            pid = _fork_with_clean_interpreter_hooks()
        except BaseException as exc:
            for descriptor in (read_fd, write_fd):
                try:
                    os.close(descriptor)
                except OSError:
                    pass
            if isinstance(exc, IsolationUnavailableError):
                raise
            if isinstance(exc, OSError):
                raise IsolationUnavailableError(
                    "unable to start the isolated child"
                ) from exc
            raise
        if pid == 0:
            try:
                try:
                    os.close(read_fd)
                except OSError:
                    child_exit(1)
                _run_child(
                    write_fd,
                    work_dir,
                    fn,
                    timeout_seconds=timeout_seconds,
                    max_memory_mb=max_memory_mb,
                    max_output_bytes=max_output_bytes,
                )
            finally:
                child_exit(1)

        try:
            try:
                os.close(write_fd)
            except OSError as exc:
                _terminate_process_tree(pid)
                raise IsolatedExecutionError(
                    "unable to close the parent isolation pipe"
                ) from exc
            try:
                raw, status = _collect_child(
                    pid,
                    read_fd,
                    timeout_seconds=timeout_seconds,
                    max_output_bytes=max_output_bytes,
                )
            except (
                IsolatedExecutionTimeout,
                IsolatedOutputLimitError,
                _ChildOwnershipLost,
            ):
                # Timeout/output-limit paths normally terminate and reap. Lost
                # ownership poisons this boundary instead of risking a numeric
                # pid/process-group signal after external reaping.
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

    if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0:
        detail = _describe_wait_status(status)
        raise IsolatedExecutionError(f"isolated child failed after execution ({detail})")
    if not raw:
        detail = _describe_wait_status(status)
        raise IsolatedExecutionError(f"isolated child exited without a response ({detail})")

    try:
        response = _decode_isolated_response(raw)
    except (UnicodeDecodeError, json.JSONDecodeError, ValueError) as exc:
        raise IsolatedExecutionError("isolated child returned malformed JSON") from exc
    if not isinstance(response, dict) or response.get("version") != _PROTOCOL_VERSION:
        raise IsolatedExecutionError("isolated child returned an invalid protocol response")

    response_status = response.get("status")
    if response_status == "ok":
        return response.get("value")
    if response_status == "output_limit":
        raise IsolatedOutputLimitError("isolated child result exceeded the output limit")
    if response_status == "unavailable":
        raise IsolationUnavailableError("isolated child could not enforce process containment")
    if response_status == "error":
        error_type = response.get("error_type", "Exception")
        message = response.get("message", "isolated execution failed")
        if not isinstance(error_type, str) or not isinstance(message, str):
            raise IsolatedExecutionError("isolated child returned an invalid error response")
        raise IsolatedExecutionError(f"{error_type}: {message}")
    raise IsolatedExecutionError("isolated child returned an unknown response status")


def _clear_inherited_interpreter_hooks() -> None:
    """Remove parent tracing/profiling callbacks copied across ``fork``."""
    sys.settrace(None)
    sys.setprofile(None)
    threading.settrace(None)
    threading.setprofile(None)


def _restore_interpreter_hooks(hooks: tuple[Any, Any, Any, Any]) -> None:
    """Restore trace/profile callbacks after a parent-side fork attempt."""
    sys_trace, sys_profile, thread_trace, thread_profile = hooks
    threading.setprofile(thread_profile)
    threading.settrace(thread_trace)
    sys.setprofile(sys_profile)
    sys.settrace(sys_trace)


def _fork_with_clean_interpreter_hooks() -> int:
    """Fork without exposing the child to parent instrumentation callbacks."""
    hooks = (
        sys.gettrace(),
        sys.getprofile(),
        threading.gettrace(),
        threading.getprofile(),
    )
    try:
        _clear_inherited_interpreter_hooks()
    except BaseException as exc:
        try:
            _restore_interpreter_hooks(hooks)
        except BaseException as restore_exc:
            raise IsolationUnavailableError(
                "unable to disable or restore interpreter hooks before fork"
            ) from restore_exc
        raise IsolationUnavailableError(
            "unable to disable interpreter hooks before fork"
        ) from exc
    try:
        # Recheck immediately adjacent to fork and after disabling callbacks
        # that could mutate these preconditions. Native pthreads created via
        # extensions/ctypes are invisible to ``threading.active_count()``;
        # CPython's post-fork warning is diagnostic only and explicitly clears
        # warning exceptions, so it cannot enforce this boundary.
        if _native_thread_count() != 1:
            raise IsolationUnavailableError(
                "native thread state changed before isolated child startup"
            )
        if not _sigchld_disposition_is_safe():
            raise IsolationUnavailableError(
                "SIGCHLD disposition changed before isolated child startup"
            )
        if not _child_ownership_primitives_available():
            raise IsolationUnavailableError(
                "child ownership primitives changed before isolated child startup"
            )
        pid = os.fork()
    except BaseException:
        try:
            _restore_interpreter_hooks(hooks)
        except BaseException as restore_exc:
            raise IsolationUnavailableError(
                "unable to restore interpreter hooks after fork failure"
            ) from restore_exc
        raise
    if pid != 0:
        try:
            _restore_interpreter_hooks(hooks)
        except BaseException as exc:
            try:
                _terminate_process_tree(pid)
            except BaseException as cleanup_exc:
                raise IsolationUnavailableError(
                    "unable to restore interpreter hooks or clean up the child"
                ) from cleanup_exc
            raise IsolationUnavailableError(
                "unable to restore interpreter hooks after fork; child terminated"
            ) from exc
    return pid


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
    # Instrumentation callbacks can retain parent locks and deadlock a helper
    # thread after fork. They are also ambient parent code that does not belong
    # inside the isolated execution boundary.
    _clear_inherited_interpreter_hooks()
    try:
        try:
            try:
                os.setsid()
            except OSError as exc:
                raise IsolationUnavailableError(
                    "unable to create the isolated child process group"
                ) from exc
            if os.getpgrp() != os.getpid():
                raise IsolationUnavailableError(
                    "unable to verify the isolated child process group"
                )
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
        except IsolationUnavailableError:
            response = {
                "version": _PROTOCOL_VERSION,
                "status": "unavailable",
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
    """Reduce ambient capabilities and apply mandatory/best-effort limits."""
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
    raise IsolationUnavailableError("unable to enumerate inherited file descriptors")


def _linux_virtual_memory_bytes() -> int:
    """Return address space already inherited by the forked Linux child."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm_file:
            fields = statm_file.readline().split()
        page_count = int(fields[0])
        page_size = os.sysconf("SC_PAGE_SIZE")
    except (IndexError, OSError, UnicodeError, ValueError) as exc:
        raise IsolationUnavailableError(
            "unable to establish inherited Linux address-space usage"
        ) from exc
    if page_count < 1 or not isinstance(page_size, int) or page_size < 1:
        raise IsolationUnavailableError(
            "unable to establish inherited Linux address-space usage"
        )
    return page_count * page_size


def _apply_resource_limits(
    *,
    timeout_seconds: float,
    max_memory_mb: int,
    max_output_bytes: int,
) -> None:
    try:
        import resource
    except ImportError as exc:
        raise IsolationUnavailableError("process resource limits are unavailable") from exc

    memory_bytes = max_memory_mb * 1024 * 1024
    inherited_address_space_bytes = (
        _linux_virtual_memory_bytes() if sys.platform.startswith("linux") else None
    )
    cpu_seconds = max(1, math.ceil(timeout_seconds))
    requested: list[tuple[int, int]] = [
        (resource.RLIMIT_CORE, 0),
        (resource.RLIMIT_CPU, cpu_seconds),
        (resource.RLIMIT_FSIZE, max_output_bytes),
        (resource.RLIMIT_NOFILE, 32),
    ]
    memory_limits: tuple[tuple[str, int], ...]
    if inherited_address_space_bytes is not None:
        # A fork already owns the parent's mappings. Treat the configured
        # memory budget as bounded child growth; an absolute limit below the
        # inherited address space prevents even a small helper thread. RLIMIT_AS
        # covers both mmap and data growth, so a separate Linux DATA cap would
        # reintroduce the same inherited-baseline bug.
        memory_limits = (
            ("RLIMIT_AS", inherited_address_space_bytes + memory_bytes),
        )
    else:
        memory_limits = (
            ("RLIMIT_AS", memory_bytes),
            ("RLIMIT_DATA", memory_bytes),
        )
    for name, value in memory_limits:
        limit = getattr(resource, name, None)
        if limit is not None:
            requested.append((limit, value))
    for resource_id, value in requested:
        try:
            soft, hard = resource.getrlimit(resource_id)
            effective = value
            for inherited_limit in (soft, hard):
                if inherited_limit != resource.RLIM_INFINITY:
                    effective = min(effective, inherited_limit)
            if (
                inherited_address_space_bytes is not None
                and resource_id == getattr(resource, "RLIMIT_AS", None)
                and effective <= inherited_address_space_bytes
            ):
                raise IsolationUnavailableError(
                    "the inherited address-space limit leaves no child memory allowance"
                )
            resource.setrlimit(resource_id, (effective, effective))
        except (OSError, ValueError):
            # Some kernels expose a limit but do not enforce or permit lowering it.
            continue

    _apply_linux_process_limit(resource)
    _apply_descendant_containment(resource)


def _apply_descendant_containment(resource_module: Any) -> None:
    """Prevent descendants from leaving the process group killed by the parent."""
    if sys.platform.startswith("linux"):
        _install_linux_process_group_filter()
        return
    if sys.platform == "darwin":
        process_limit = getattr(resource_module, "RLIMIT_NPROC", None)
        if process_limit is None or _safe_unprivileged_uid() is None:
            raise IsolationUnavailableError("process-count containment is unavailable")
        try:
            resource_module.setrlimit(process_limit, (1, 1))
            soft, hard = resource_module.getrlimit(process_limit)
        except (OSError, ValueError) as exc:
            raise IsolationUnavailableError("unable to enforce the child process limit") from exc
        if soft > 1 or hard > 1:
            raise IsolationUnavailableError("unable to verify the child process limit")
        return
    raise IsolationUnavailableError("process descendant containment is unsupported")


def _install_linux_process_group_filter() -> None:
    try:
        _isolation_seccomp._install_linux_process_group_filter()
    except _isolation_seccomp._LinuxContainmentUnavailable as exc:
        raise IsolationUnavailableError(str(exc)) from exc


def _linux_process_group_filter_rules(
    machine: str,
    *,
    errno_value: int,
) -> tuple[tuple[int, int, int, int], ...]:
    try:
        return _isolation_seccomp._linux_process_group_filter_rules(
            machine,
            errno_value=errno_value,
        )
    except _isolation_seccomp._LinuxContainmentUnavailable as exc:
        raise IsolationUnavailableError(str(exc)) from exc


def _apply_linux_process_limit(resource_module: Any) -> None:
    try:
        _isolation_seccomp._apply_linux_process_limit(
            resource_module,
            max_child_tasks=DEFAULT_MAX_CHILD_TASKS,
            safe_unprivileged_uid=_safe_unprivileged_uid,
            capability_masks=_linux_capability_masks,
            same_uid_task_count=_linux_same_uid_task_count,
        )
    except _isolation_seccomp._LinuxContainmentUnavailable as exc:
        raise IsolationUnavailableError(str(exc)) from exc


def _linux_capability_masks() -> tuple[int, int, int, int] | None:
    return _isolation_seccomp._linux_capability_masks()


def _linux_same_uid_task_count() -> int | None:
    return _isolation_seccomp._linux_same_uid_task_count(_safe_unprivileged_uid)


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

    # Keep the exited leader as a zombie so its pid/pgid cannot be reused.
    with selectors.DefaultSelector() as selector:
        selector.register(read_fd, selectors.EVENT_READ)
        while not exited or not eof:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                _terminate_process_tree(pid)
                raise IsolatedExecutionTimeout(
                    f"isolated execution exceeded {timeout_seconds:.3g}s"
                )

            if not exited:
                child_state = _child_state_without_reaping(pid)
                if child_state == "ownership_lost":
                    raise _child_ownership_lost(
                        "isolated child ownership was lost before cleanup"
                    )
                exited = child_state == "exited"

            readable = selector.select(min(remaining, 0.05))
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
                # A descendant may have inherited the pipe. The child is complete;
                # terminate its process group so the response cannot be held open.
                _signal_process_group(pid, signal.SIGKILL)

    # EOF only proves that descendants closed the result descriptor; it does not
    # prove that the process group is empty. Keep the exited leader unreaped while
    # killing any remaining group members so the pgid cannot be reused underneath
    # this cleanup decision.
    if _child_state_without_reaping(pid) == "ownership_lost":
        raise _child_ownership_lost(
            "isolated child ownership was lost before cleanup"
        )
    _signal_process_group(pid, signal.SIGKILL)
    try:
        _, status = os.waitpid(pid, 0)
    except ChildProcessError as exc:
        raise _child_ownership_lost(
            "isolated child ownership was lost before it could be reaped"
        ) from exc
    return bytes(output), status


def _child_state_without_reaping(pid: int) -> _ChildState:
    """Observe exit while distinguishing an externally reaped leader."""
    waitid = getattr(os, "waitid", None)
    no_wait = getattr(os, "WNOWAIT", None)
    process_id_type = getattr(os, "P_PID", None)
    if (
        waitid is None
        or no_wait is None
        or process_id_type is None
        or getattr(os, "WEXITED", None) is None
        or getattr(os, "WNOHANG", None) is None
    ):
        return "ownership_lost"
    try:
        info = waitid(process_id_type, pid, os.WEXITED | os.WNOHANG | no_wait)
    except (ChildProcessError, OSError, TypeError, ValueError):
        return "ownership_lost"
    return "exited" if info is not None else "running"


def _terminate_process_tree(pid: int) -> None:
    """Terminate the child group while its unreaped leader prevents id reuse."""
    child_state = _child_state_without_reaping(pid)
    if child_state == "ownership_lost":
        raise _child_ownership_lost(
            "isolated child ownership was lost before termination"
        )
    group_signaled = _signal_process_group(pid, signal.SIGTERM)
    if not group_signaled and _child_state_without_reaping(pid) == "running":
        _signal_process(pid, signal.SIGTERM)

    # Give cooperative descendants a short grace period, but always follow
    # with SIGKILL.  Reaping the leader first would permit process-group id
    # reuse and make that final group signal unsafe.
    deadline = time.monotonic() + 0.2
    while time.monotonic() < deadline:
        child_state = _child_state_without_reaping(pid)
        if child_state == "ownership_lost":
            raise _child_ownership_lost(
                "isolated child ownership was lost during termination"
            )
        if child_state == "exited":
            break
        time.sleep(0.01)

    if _child_state_without_reaping(pid) == "ownership_lost":
        raise _child_ownership_lost(
            "isolated child ownership was lost during termination"
        )
    group_signaled = _signal_process_group(pid, signal.SIGKILL)
    if not group_signaled and _child_state_without_reaping(pid) == "running":
        _signal_process(pid, signal.SIGKILL)
    deadline = time.monotonic() + 1.0
    while time.monotonic() < deadline:
        child_state = _child_state_without_reaping(pid)
        if child_state == "ownership_lost":
            raise _child_ownership_lost(
                "isolated child ownership was lost during final termination"
            )
        if child_state == "exited":
            break
        time.sleep(0.01)
    if child_state != "exited":
        _register_stranded_child(pid)
        raise IsolatedExecutionError("isolated child did not exit after SIGKILL")
    try:
        os.waitpid(pid, 0)
    except ChildProcessError as exc:
        raise _child_ownership_lost("isolated child ownership was lost before it could be reaped") from exc


def _signal_process_group(pid: int, signum: int) -> bool:
    try:
        return signal_owned_process_group(pid, signum)
    except PermissionError as exc:
        _register_stranded_child(pid)
        raise IsolatedExecutionError("isolated process-group signaling was denied") from exc


def _signal_process(pid: int, signum: int) -> bool:
    try:
        os.kill(pid, signum)
    except ProcessLookupError:
        return False
    except PermissionError as exc:
        _register_stranded_child(pid)
        raise IsolatedExecutionError("isolated process signaling was denied") from exc
    return True
