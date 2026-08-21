"""Authenticated Docker supervisor lifecycle for the GPU kernel worker."""

from __future__ import annotations

import os
import secrets
import signal
import subprocess
import threading
import time
from pathlib import Path
from typing import Any, Protocol

from autocontext.execution.docker_isolation import sanitized_docker_environment
from autocontext.kernel_evolution import _process_control
from autocontext.kernel_evolution.benchmark import KernelBenchmarkExecution, KernelBenchmarkExecutionOutcome
from autocontext.kernel_evolution.docker_supervisor import (
    MAX_SUPERVISOR_WIRE_BYTES,
    DockerSupervisorCompletion,
    DockerSupervisorStatusCollector,
    encode_ack,
    encode_start,
    normalized_adapter_exit_code,
)
from autocontext.kernel_evolution.docker_watchdog import launch_deadline_watchdog, terminate_process_group

_REPORT_READER_CODE = r"""
import os
import stat
import sys

path = sys.argv[1]
limit = int(sys.argv[2])
before = os.lstat(path)
if stat.S_ISLNK(before.st_mode) or not stat.S_ISREG(before.st_mode) or before.st_size > limit:
    raise SystemExit(3)
flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0)
descriptor = os.open(path, flags)
try:
    opened = os.fstat(descriptor)
    identity = (opened.st_dev, opened.st_ino, opened.st_mode, opened.st_size, opened.st_mtime_ns, opened.st_ctime_ns)
    if not stat.S_ISREG(opened.st_mode) or (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino):
        raise SystemExit(4)
    payload = bytearray()
    while chunk := os.read(descriptor, min(64 * 1024, limit + 1 - len(payload))):
        payload.extend(chunk)
        if len(payload) > limit:
            raise SystemExit(5)
    after = os.fstat(descriptor)
    current = os.lstat(path)
    if identity != (after.st_dev, after.st_ino, after.st_mode, after.st_size, after.st_mtime_ns, after.st_ctime_ns):
        raise SystemExit(6)
    if identity != (current.st_dev, current.st_ino, current.st_mode, current.st_size, current.st_mtime_ns, current.st_ctime_ns):
        raise SystemExit(7)
    sys.stdout.buffer.write(payload)
finally:
    os.close(descriptor)
"""


class DockerWorkerRuntimeHost(Protocol):
    docker_binary: str

    def _create_container(self, command: list[str], *, expires_at: float) -> None: ...

    def _copy_report(
        self,
        container_name: str,
        report_path: Path,
        completion: DockerSupervisorCompletion,
        *,
        timeout_seconds: float,
    ) -> None: ...

    def _verify_copied_report(
        self,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
        completion: DockerSupervisorCompletion,
    ) -> None: ...

    def _read_report(
        self,
        report_path: Path,
        report_root_identity: _process_control.FilesystemObjectIdentity,
    ) -> dict[str, Any] | None: ...

    def _container_oom(self, container_name: str, *, timeout_seconds: float) -> bool: ...

    def _remove_container(self, container_name: str) -> None: ...

    def _verify_removed(self, container_name: str) -> None: ...


def _candidate_stdout(stdout_wire: bytes, collector: DockerSupervisorStatusCollector) -> bytes:
    frame = collector.authenticated_frame
    if frame is None:
        return stdout_wire
    control = b"\n" + frame
    marker = stdout_wire.rfind(control)
    if marker < 0:
        raise RuntimeError("authenticated Docker supervisor frame was absent from captured output")
    return stdout_wire[:marker] + stdout_wire[marker + len(control) :]


def copy_live_tmpfs_report(
    *,
    docker_binary: str,
    container_name: str,
    report_path: Path,
    container_python: str,
    max_report_bytes: int,
    timeout_seconds: float,
) -> None:
    """Read a bounded report through a trusted exec process while tmpfs is mounted."""

    if not container_python.startswith("/") or any(character in container_python for character in "\r\n\0"):
        raise RuntimeError("Docker supervisor authenticated an unsafe Python executable path")
    completed = subprocess.run(  # noqa: S603
        [
            docker_binary,
            "exec",
            "--user",
            f"{os.getuid()}:{os.getgid()}",
            "--workdir",
            "/",
            container_name,
            container_python,
            "-I",
            "-B",
            "-S",
            "-c",
            _REPORT_READER_CODE,
            "/output/report.json",
            str(max_report_bytes),
        ],
        check=False,
        capture_output=True,
        timeout=max(0.001, timeout_seconds),
        env=sanitized_docker_environment(),
    )
    if completed.returncode != 0 or len(completed.stdout) > max_report_bytes:
        detail = (completed.stderr or completed.stdout[-240:]).decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Docker supervisor report extraction failed: {detail or 'unknown error'}")
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_CLOEXEC", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(report_path, flags, 0o600)
    try:
        pending = memoryview(completed.stdout)
        while pending:
            written = os.write(descriptor, pending)
            pending = pending[written:]
    finally:
        os.close(descriptor)


def execute_supervised_container(
    host: DockerWorkerRuntimeHost,
    command: list[str],
    *,
    container_name: str,
    report_path: Path,
    report_root_identity: _process_control.FilesystemObjectIdentity,
    timeout_seconds: float,
    execution_expires_at: float,
    hard_expires_at: float,
    max_output_bytes: int,
    max_report_bytes: int,
) -> KernelBenchmarkExecution:
    """Run one created container through authenticated completion and report extraction."""

    secret = secrets.token_bytes(32)
    collector = DockerSupervisorStatusCollector(secret, max_report_bytes=max_report_bytes)
    stdout = _process_control.BoundedOutput()
    stderr = _process_control.BoundedOutput()
    stdout_wire = bytearray()
    stdout_wire_limit = max_output_bytes + MAX_SUPERVISOR_WIRE_BYTES
    quota_exceeded = threading.Event()
    threads: list[threading.Thread] = []
    timed_out = False
    returncode: int | None = None
    error: str | None = None
    oom = False
    cleanup_error: str | None = None
    proc: subprocess.Popen[bytes] | None = None
    watchdog: subprocess.Popen[bytes] | None = None
    termination_lock = threading.Lock()

    def terminate() -> None:
        with termination_lock:
            host._remove_container(container_name)

    def observe_stdout(chunk: bytes) -> None:
        remaining = stdout_wire_limit + 1 - len(stdout_wire)
        if remaining > 0:
            stdout_wire.extend(chunk[:remaining])
        collector.feed(chunk)

    try:
        host._create_container(command, expires_at=execution_expires_at)
        if time.time() >= execution_expires_at:
            timed_out = True
        else:
            watchdog = launch_deadline_watchdog(
                host.docker_binary,
                container_name,
                hard_expires_at,
                report_path.parent / ".watchdog-ready",
            )
            if time.time() >= execution_expires_at:
                timed_out = True
                raise subprocess.TimeoutExpired("Docker supervisor startup", timeout_seconds)
            proc = subprocess.Popen(  # noqa: S603
                [host.docker_binary, "start", "--attach", "--interactive", container_name],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                close_fds=True,
                start_new_session=True,
                env=sanitized_docker_environment(),
            )
            assert proc.stdin is not None and proc.stdout is not None and proc.stderr is not None
            stdout_thread = threading.Thread(
                target=_process_control.drain_bounded,
                args=(proc.stdout, stdout_wire_limit, stdout, quota_exceeded, terminate, observe_stdout),
                daemon=True,
            )
            stderr_thread = threading.Thread(
                target=_process_control.drain_bounded,
                args=(proc.stderr, max_output_bytes, stderr, quota_exceeded, terminate),
                daemon=True,
            )
            for thread in (stdout_thread, stderr_thread):
                thread.start()
                threads.append(thread)
            proc.stdin.write(encode_start(secret))
            proc.stdin.flush()

            while True:
                completion = collector.completion
                if completion is not None:
                    returncode = completion.adapter_returncode
                    if completion.completed_at_ns >= int(execution_expires_at * 1_000_000_000):
                        timed_out = True
                        terminate()
                        break
                    candidate_output = _candidate_stdout(bytes(stdout_wire), collector)
                    if len(candidate_output) > max_output_bytes:
                        stdout.exceeded = True
                        quota_exceeded.set()
                        terminate()
                        break
                    if quota_exceeded.is_set():
                        terminate()
                        break
                    remaining = hard_expires_at - time.time()
                    if remaining <= 0:
                        raise RuntimeError("Docker supervisor report extraction deadline expired")
                    if completion.report_size is not None:
                        host._copy_report(
                            container_name,
                            report_path,
                            completion,
                            timeout_seconds=remaining,
                        )
                        host._verify_copied_report(report_path, report_root_identity, completion)
                    remaining = hard_expires_at - time.time()
                    if remaining <= 0:
                        raise RuntimeError("Docker supervisor acknowledgement deadline expired")
                    oom = host._container_oom(
                        container_name,
                        timeout_seconds=remaining,
                    )
                    if time.time() >= hard_expires_at:
                        raise RuntimeError("Docker supervisor acknowledgement deadline expired")
                    proc.stdin.write(encode_ack(secret, completion))
                    proc.stdin.flush()
                    proc.stdin.close()
                    cli_returncode = proc.wait(timeout=max(0.001, hard_expires_at - time.time()))
                    expected_cli_returncode = normalized_adapter_exit_code(completion.adapter_returncode)
                    if cli_returncode != expected_cli_returncode:
                        raise RuntimeError(
                            "Docker supervisor exit status disagreed with its authenticated adapter status"
                        )
                    break
                if quota_exceeded.is_set():
                    terminate()
                    break
                polled_returncode = proc.poll()
                if polled_returncode is not None:
                    oom = host._container_oom(
                        container_name,
                        timeout_seconds=max(0.001, hard_expires_at - time.time()),
                    )
                    if polled_returncode == 124 or time.time() >= execution_expires_at:
                        timed_out = True
                    elif not oom:
                        error = "Docker GPU worker failed: supervisor exited without an authenticated completion"
                    break
                if time.time() >= hard_expires_at:
                    timed_out = True
                    terminate()
                    break
                collector.ready.wait(timeout=min(0.01, max(0.0, hard_expires_at - time.time())))
        if returncode == -signal.SIGKILL:
            oom = True
    except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
        error = f"Docker GPU worker failed: {type(exc).__name__}: {exc}"
    finally:
        if proc is not None:
            try:
                terminate_process_group(proc, description="Docker CLI")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cleanup_error = f"Docker GPU worker teardown failed: {type(exc).__name__}: {exc}"
        for thread in threads:
            thread.join(timeout=_process_control.CLEANUP_JOIN_TIMEOUT_SECONDS)
        if proc is not None:
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe is not None:
                    pipe.close()
        try:
            candidate_output = _candidate_stdout(bytes(stdout_wire), collector)
        except RuntimeError:
            candidate_output = bytes(stdout_wire)
            if collector.completion is not None:
                stdout.read_failed = True
        if len(candidate_output) > max_output_bytes:
            stdout.exceeded = True
            quota_exceeded.set()
        stdout.text = candidate_output[:max_output_bytes].decode("utf-8", errors="replace")
        try:
            host._remove_container(container_name)
            host._verify_removed(container_name)
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            cleanup_error = f"Docker GPU worker teardown failed: {type(exc).__name__}: {exc}"
        if watchdog is not None and cleanup_error is None:
            try:
                terminate_process_group(watchdog, description="Docker deadline watchdog")
            except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
                cleanup_error = f"Docker GPU worker teardown failed: {type(exc).__name__}: {exc}"
    quota_details = []
    if stdout.exceeded:
        quota_details.append(f"stdout exceeded max_output_bytes={max_output_bytes}")
    if stderr.exceeded:
        quota_details.append(f"stderr exceeded max_output_bytes={max_output_bytes}")
    payload = (
        host._read_report(report_path, report_root_identity)
        if not timed_out and not quota_exceeded.is_set() and error is None
        else None
    )
    outcome: KernelBenchmarkExecutionOutcome = "complete"
    if cleanup_error is not None:
        outcome, error = "teardown_failed", cleanup_error
    elif oom:
        outcome, error = "oom", "Docker reported an out-of-memory kill"
    elif quota_exceeded.is_set() or quota_details:
        outcome, error = "resource_exceeded", "; ".join(quota_details) or "worker resource quota exceeded"
    elif timed_out:
        outcome, error = "timeout", f"Docker GPU worker timed out after {timeout_seconds:g}s"
    return KernelBenchmarkExecution(
        returncode=returncode,
        timed_out=timed_out,
        report_payload=payload,
        stdout=stdout.text,
        stderr=stderr.text,
        stdout_truncated=stdout.exceeded or stdout.read_failed,
        stderr_truncated=stderr.exceeded or stderr.read_failed,
        error=error,
        outcome=outcome,
    )


__all__ = ["DockerWorkerRuntimeHost", "copy_live_tmpfs_report", "execute_supervised_container"]
