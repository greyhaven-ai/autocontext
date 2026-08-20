"""Private process-tree and bounded-output primitives for kernel benchmarks."""

from __future__ import annotations

import errno
import os
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO, cast

REPORT_MONITOR_INTERVAL_SECONDS = 0.002
CLEANUP_JOIN_TIMEOUT_SECONDS = 2.0
CLEANUP_PROCESS_TIMEOUT_SECONDS = 2.0
CLEANUP_FORCE_KILL_TIMEOUT_SECONDS = 1.0
CLEANUP_JOB_TIMEOUT_SECONDS = 2.0
JOB_POLL_INTERVAL_SECONDS = 0.01
WINDOWS_SYSTEM_ENVIRONMENT = ("SystemRoot", "WINDIR", "ComSpec", "PATHEXT")
CONFINED_ENVIRONMENT = ("HOME", "USERPROFILE", "TEMP", "TMP", "TMPDIR")

FilesystemObjectIdentity = tuple[int, int, int]


def _remove_environment_value(environment: dict[str, str], name: str) -> None:
    folded_name = name.casefold()
    for existing in tuple(environment):
        if existing.casefold() == folded_name:
            del environment[existing]


def _set_environment_value(environment: dict[str, str], name: str, value: str) -> None:
    """Set one logical environment key without Windows case aliases."""

    _remove_environment_value(environment, name)
    environment[name] = value


def _host_environment_value(name: str) -> str | None:
    folded_name = name.casefold()
    return next((value for key, value in os.environ.items() if key.casefold() == folded_name), None)


def build_benchmark_environment(temp_dir: Path, custom: Mapping[str, str]) -> dict[str, str]:
    """Build the minimal benchmark environment with runner-owned confinement paths."""

    environment = {
        "PATH": os.environ.get("PATH", ""),
        "PYTHONUNBUFFERED": "1",
        **custom,
    }
    if sys.platform == "win32":
        for name in WINDOWS_SYSTEM_ENVIRONMENT:
            value = _host_environment_value(name)
            if value is None:
                _remove_environment_value(environment, name)
            else:
                _set_environment_value(environment, name, value)
    for name in CONFINED_ENVIRONMENT:
        _set_environment_value(environment, name, str(temp_dir))
    return environment


@dataclass(slots=True)
class BoundedOutput:
    text: str = ""
    exceeded: bool = False
    read_failed: bool = False


@dataclass(frozen=True, slots=True)
class ReportLimits:
    max_bytes: int
    max_entries: int
    max_depth: int

    def __post_init__(self) -> None:
        if self.max_bytes < 1:
            raise ValueError("max report bytes must be positive")
        if self.max_entries < 1:
            raise ValueError("max report entries must be positive")
        if self.max_depth < 1:
            raise ValueError("max report depth must be positive")


def filesystem_object_identity(value: os.stat_result) -> FilesystemObjectIdentity:
    """Return identity fields that remain stable while directory contents change."""

    return (value.st_dev, value.st_ino, stat.S_IFMT(value.st_mode))


def filesystem_snapshot_identity(value: os.stat_result) -> tuple[int, int, int, int, int, int]:
    """Return metadata used to reject file or directory changes across an operation."""

    return (value.st_dev, value.st_ino, value.st_mode, value.st_size, value.st_mtime_ns, value.st_ctime_ns)


def _require_anchored_directory(
    value: os.stat_result,
    expected: FilesystemObjectIdentity,
    path: Path,
) -> None:
    if stat.S_ISLNK(value.st_mode) or not stat.S_ISDIR(value.st_mode):
        raise ValueError("benchmark report directory must remain a regular directory during execution")
    if filesystem_object_identity(value) != expected:
        raise ValueError(f"benchmark report directory changed while being inspected: {path}")


def _directory_open_flags() -> int:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
    return flags


def _open_anchored_directory(path: Path, expected: FilesystemObjectIdentity) -> int:
    before = path.lstat()
    _require_anchored_directory(before, expected, path)
    descriptor = os.open(path, _directory_open_flags())
    try:
        opened = os.fstat(descriptor)
        _require_anchored_directory(opened, expected, path)
        _require_anchored_directory(path.lstat(), expected, path)
        return descriptor
    except BaseException:
        os.close(descriptor)
        raise


@contextmanager
def _scandir_anchored(
    path: Path,
    expected: FilesystemObjectIdentity,
) -> Iterator[Iterator[os.DirEntry[str]]]:
    if os.scandir in os.supports_fd:
        descriptor = _open_anchored_directory(path, expected)
        try:
            with os.scandir(descriptor) as entries:
                yield entries
            _require_anchored_directory(os.fstat(descriptor), expected, path)
            _require_anchored_directory(path.lstat(), expected, path)
        finally:
            os.close(descriptor)
        return

    before = path.lstat()
    _require_anchored_directory(before, expected, path)
    with os.scandir(path) as entries:
        yield entries
    _require_anchored_directory(path.lstat(), expected, path)


def _report_quota_error(kind: str, limit: int) -> ValueError:
    return ValueError(f"benchmark report directory exceeded max_report_{kind}={limit} during execution")


def inspect_report_tree(
    report_dir: Path,
    limits: ReportLimits,
    expected_root: FilesystemObjectIdentity,
) -> None:
    """Stream over a report tree while enforcing bounded bytes, entries, and depth."""

    total_size = 0
    entry_count = 0
    pending: list[tuple[Path, int, FilesystemObjectIdentity]] = [(report_dir, 0, expected_root)]
    while pending:
        directory, directory_depth, expected_directory = pending.pop()
        try:
            with _scandir_anchored(directory, expected_directory) as entries:
                for entry in entries:
                    entry_count += 1
                    if entry_count > limits.max_entries:
                        raise _report_quota_error("entries", limits.max_entries)
                    entry_depth = directory_depth + 1
                    if entry_depth > limits.max_depth:
                        raise _report_quota_error("depth", limits.max_depth)
                    try:
                        entry_stat = entry.stat(follow_symlinks=False)
                    except FileNotFoundError:
                        continue
                    entry_path = directory / entry.name
                    if stat.S_ISLNK(entry_stat.st_mode):
                        raise ValueError(f"benchmark report directory cannot contain symlinks: {entry_path}")
                    if stat.S_ISDIR(entry_stat.st_mode):
                        pending.append((entry_path, entry_depth, filesystem_object_identity(entry_stat)))
                    elif stat.S_ISREG(entry_stat.st_mode):
                        total_size += entry_stat.st_size
                        if total_size > limits.max_bytes:
                            raise _report_quota_error("bytes", limits.max_bytes)
                    else:
                        raise ValueError(f"benchmark report directory cannot contain special files: {entry_path}")
        except FileNotFoundError:
            if directory == report_dir:
                raise ValueError("benchmark report directory disappeared during execution") from None
            continue


def _lstat_entry(path: Path, parent_descriptor: int | None) -> os.stat_result:
    if parent_descriptor is not None and os.stat in os.supports_dir_fd and os.stat in os.supports_follow_symlinks:
        return os.stat(path.name, dir_fd=parent_descriptor, follow_symlinks=False)
    return path.lstat()


def read_bounded_regular_file(
    path: Path,
    max_bytes: int,
    *,
    expected_parent: FilesystemObjectIdentity,
    description: str = "report",
) -> bytes:
    """Read a stable regular file through a descriptor, never exceeding ``max_bytes``."""

    if max_bytes < 1:
        raise ValueError("max report bytes must be positive")
    parent = path.parent
    parent_descriptor: int | None = None
    if os.open in os.supports_dir_fd:
        parent_descriptor = _open_anchored_directory(parent, expected_parent)
    else:
        _require_anchored_directory(parent.lstat(), expected_parent, parent)

    descriptor: int | None = None
    try:
        before = _lstat_entry(path, parent_descriptor)
        if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode):
            raise ValueError(f"{description} path must be a regular file")
        if before.st_size > max_bytes:
            raise ValueError(f"{description} exceeds {max_bytes} bytes")

        flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_CLOEXEC", 0)
        flags |= getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
        if parent_descriptor is not None:
            descriptor = os.open(path.name, flags, dir_fd=parent_descriptor)
        else:
            descriptor = os.open(path, flags)
        opened_before = os.fstat(descriptor)
        if not stat.S_ISREG(opened_before.st_mode) or filesystem_snapshot_identity(opened_before) != filesystem_snapshot_identity(
            before
        ):
            raise ValueError(f"{description} path changed while opening")

        payload = bytearray()
        while chunk := os.read(descriptor, min(64 * 1024, max_bytes + 1 - len(payload))):
            payload.extend(chunk)
            if len(payload) > max_bytes:
                raise ValueError(f"{description} exceeds {max_bytes} bytes")

        opened_after = os.fstat(descriptor)
        current = _lstat_entry(path, parent_descriptor)
        if filesystem_snapshot_identity(opened_before) != filesystem_snapshot_identity(
            opened_after
        ) or filesystem_snapshot_identity(opened_after) != filesystem_snapshot_identity(current):
            raise ValueError(f"{description} path changed while reading")
        if parent_descriptor is not None:
            _require_anchored_directory(os.fstat(parent_descriptor), expected_parent, parent)
        _require_anchored_directory(parent.lstat(), expected_parent, parent)
        return bytes(payload)
    finally:
        if descriptor is not None:
            os.close(descriptor)
        if parent_descriptor is not None:
            os.close(parent_descriptor)


def drain_bounded(
    stream: BinaryIO,
    limit: int,
    result: BoundedOutput,
    quota_exceeded: threading.Event,
    terminate: Callable[[], None],
) -> None:
    kept = bytearray()
    try:
        while chunk := os.read(stream.fileno(), 64 * 1024):
            remaining = limit - len(kept)
            if remaining > 0:
                kept.extend(chunk[:remaining])
            if len(chunk) > remaining and not result.exceeded:
                result.exceeded = True
                quota_exceeded.set()
                terminate()
    except (OSError, ValueError):
        result.read_failed = True
    result.text = kept.decode("utf-8", errors="replace")


def monitor_report(
    report_path: Path,
    limits: ReportLimits,
    expected_root: FilesystemObjectIdentity,
    stop: threading.Event,
    errors: list[str],
    quota_exceeded: threading.Event,
    terminate: Callable[[], None],
) -> None:
    report_dir = report_path.parent

    def inspect() -> bool:
        try:
            inspect_report_tree(report_dir, limits, expected_root)
        except ValueError as exc:
            errors.append(str(exc))
        except OSError as exc:
            errors.append(f"benchmark report could not be inspected during execution: {exc}")
        else:
            return False
        quota_exceeded.set()
        terminate()
        return True

    while not stop.wait(REPORT_MONITOR_INTERVAL_SECONDS):
        if inspect():
            return
    inspect()


class WindowsJob:
    """Minimal kill-on-close Job Object wrapper, constructed only on Windows."""

    def __init__(self, handle: Any, kernel32: Any) -> None:
        self._handle = handle
        self._kernel32 = kernel32
        self._assigned = False

    @property
    def assigned(self) -> bool:
        return self._assigned

    @classmethod
    def create(cls) -> WindowsJob:
        import ctypes

        windows_ctypes = cast(Any, ctypes)

        class BasicLimitInformation(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_int64),
                ("PerJobUserTimeLimit", ctypes.c_int64),
                ("LimitFlags", ctypes.c_uint32),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", ctypes.c_uint32),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", ctypes.c_uint32),
                ("SchedulingClass", ctypes.c_uint32),
            ]

        class IoCounters(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_uint64),
                ("WriteOperationCount", ctypes.c_uint64),
                ("OtherOperationCount", ctypes.c_uint64),
                ("ReadTransferCount", ctypes.c_uint64),
                ("WriteTransferCount", ctypes.c_uint64),
                ("OtherTransferCount", ctypes.c_uint64),
            ]

        class ExtendedLimitInformation(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", BasicLimitInformation),
                ("IoInfo", IoCounters),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        kernel32 = windows_ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateJobObjectW.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
        kernel32.CreateJobObjectW.restype = ctypes.c_void_p
        kernel32.SetInformationJobObject.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
        kernel32.SetInformationJobObject.restype = ctypes.c_int
        kernel32.AssignProcessToJobObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
        kernel32.AssignProcessToJobObject.restype = ctypes.c_int
        kernel32.TerminateJobObject.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
        kernel32.TerminateJobObject.restype = ctypes.c_int
        kernel32.QueryInformationJobObject.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_void_p,
            ctypes.c_uint32,
            ctypes.c_void_p,
        ]
        kernel32.QueryInformationJobObject.restype = ctypes.c_int
        kernel32.CloseHandle.argtypes = [ctypes.c_void_p]
        kernel32.CloseHandle.restype = ctypes.c_int
        handle = kernel32.CreateJobObjectW(None, None)
        if not handle:
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        information = ExtendedLimitInformation()
        information.BasicLimitInformation.LimitFlags = 0x00002000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(information), ctypes.sizeof(information)):
            error = windows_ctypes.WinError(windows_ctypes.get_last_error())
            if not kernel32.CloseHandle(handle):
                close_error = windows_ctypes.WinError(windows_ctypes.get_last_error())
                raise OSError(f"{error}; Windows Job handle close also failed: {close_error}") from error
            raise error
        return cls(handle, kernel32)

    def assign(self, proc: subprocess.Popen[bytes]) -> None:
        import ctypes

        windows_ctypes = cast(Any, ctypes)
        process_handle = ctypes.c_void_p(int(cast(Any, proc)._handle))
        if not self._kernel32.AssignProcessToJobObject(self._handle, process_handle):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        self._assigned = True

    def terminate(self) -> None:
        import ctypes

        if not self._assigned or not self._handle:
            return
        windows_ctypes = cast(Any, ctypes)
        if not self._kernel32.TerminateJobObject(self._handle, 1):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())

    def active_process_count(self) -> int:
        import ctypes

        windows_ctypes = cast(Any, ctypes)

        class BasicAccountingInformation(ctypes.Structure):
            _fields_ = [
                ("TotalUserTime", ctypes.c_int64),
                ("TotalKernelTime", ctypes.c_int64),
                ("ThisPeriodTotalUserTime", ctypes.c_int64),
                ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                ("TotalPageFaultCount", ctypes.c_uint32),
                ("TotalProcesses", ctypes.c_uint32),
                ("ActiveProcesses", ctypes.c_uint32),
                ("TotalTerminatedProcesses", ctypes.c_uint32),
            ]

        if not self._handle:
            raise RuntimeError("Windows Job handle is closed")
        information = BasicAccountingInformation()
        if not self._kernel32.QueryInformationJobObject(
            self._handle,
            1,
            ctypes.byref(information),
            ctypes.sizeof(information),
            None,
        ):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())
        return int(information.ActiveProcesses)

    def wait_until_empty(self, timeout_seconds: float) -> bool:
        deadline = time.monotonic() + timeout_seconds
        while self.active_process_count() != 0:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False
            time.sleep(min(JOB_POLL_INTERVAL_SECONDS, remaining))
        return True

    def close(self) -> None:
        import ctypes

        if not self._handle:
            return
        windows_ctypes = cast(Any, ctypes)
        handle = self._handle
        self._handle = None
        if not self._kernel32.CloseHandle(handle):
            raise windows_ctypes.WinError(windows_ctypes.get_last_error())


def kill_process_group(proc: subprocess.Popen[bytes], windows_job: WindowsJob | None = None) -> None:
    if sys.platform == "win32":
        if windows_job is not None and windows_job.assigned:
            windows_job.terminate()
        elif proc.poll() is None:
            proc.kill()
        return
    try:
        # start_new_session=True makes the pid the process-group id; that
        # id remains usable after a nominal group-leader exit.
        os.killpg(proc.pid, 9)
    except OSError as exc:
        if exc.errno != errno.ESRCH:
            raise


class ProcessTreeController:
    """Thread-safe termination authority and teardown error collector."""

    def __init__(self, proc: subprocess.Popen[bytes], windows_job: WindowsJob | None) -> None:
        self._proc = proc
        self._windows_job = windows_job
        self._lock = threading.Lock()
        self._enabled = True
        self._termination_attempted = False
        self._errors: list[str] = []

    @property
    def errors(self) -> tuple[str, ...]:
        with self._lock:
            return tuple(self._errors)

    def record_error(self, message: str) -> None:
        with self._lock:
            self._errors.append(message)

    def terminate(self) -> None:
        with self._lock:
            if not self._enabled or self._termination_attempted:
                return
            self._termination_attempted = True
            try:
                kill_process_group(self._proc, self._windows_job)
            except OSError as exc:
                self._errors.append(f"benchmark process tree termination failed: {type(exc).__name__}: {exc}")

    def wait_for_parent(self) -> None:
        try:
            self._proc.wait(timeout=CLEANUP_PROCESS_TIMEOUT_SECONDS)
        except subprocess.TimeoutExpired:
            try:
                self._proc.kill()
            except OSError as exc:
                if exc.errno != errno.ESRCH:
                    self.record_error(f"benchmark process force-kill failed: {type(exc).__name__}: {exc}")
            try:
                self._proc.wait(timeout=CLEANUP_FORCE_KILL_TIMEOUT_SECONDS)
            except subprocess.TimeoutExpired:
                pass
            except OSError as exc:
                self.record_error(f"benchmark process wait failed: {type(exc).__name__}: {exc}")
        except OSError as exc:
            self.record_error(f"benchmark process wait failed: {type(exc).__name__}: {exc}")
        if self._proc.poll() is None:
            self.record_error("benchmark process did not stop within cleanup timeout")

    def disarm(self) -> None:
        with self._lock:
            self._enabled = False

    def wait_for_job_empty(self) -> None:
        if self._windows_job is None or not self._windows_job.assigned:
            return
        try:
            emptied = self._windows_job.wait_until_empty(CLEANUP_JOB_TIMEOUT_SECONDS)
        except (OSError, RuntimeError) as exc:
            self.record_error(f"benchmark Windows Job status query failed: {type(exc).__name__}: {exc}")
            return
        if not emptied:
            self.record_error("benchmark Windows Job did not empty within cleanup timeout")

    def close_job(self) -> None:
        if self._windows_job is None:
            return
        try:
            self._windows_job.close()
        except OSError as exc:
            self.record_error(f"benchmark Windows Job handle close failed: {type(exc).__name__}: {exc}")
