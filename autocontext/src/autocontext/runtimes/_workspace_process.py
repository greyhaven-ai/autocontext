"""Bounded subprocess execution for local runtime workspaces."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import IO

DEFAULT_RUNTIME_PROCESS_OUTPUT_LIMIT_BYTES = 1_048_576
_PROCESS_TERMINATION_GRACE_SECONDS = 0.25
_PROCESS_DRAIN_GRACE_SECONDS = 0.25
_PROCESS_POLL_SECONDS = 0.01
_SHELL_ENV_KEYS = (
    "PATH",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "SystemRoot",
    "ComSpec",
    "PATHEXT",
)


@dataclass(frozen=True, slots=True)
class BoundedProcessResult:
    stdout: str
    stderr: str
    exit_code: int


def default_shell_env() -> dict[str, str]:
    """Return the small ambient environment safe for fallback shell use."""
    return {key: os.environ[key] for key in _SHELL_ENV_KEYS if key in os.environ}


def run_bounded_process(
    command: str | Sequence[str],
    *,
    cwd: str | os.PathLike[str],
    env: Mapping[str, str],
    shell: bool,
    timeout_ms: int | None,
    output_limit_bytes: int = DEFAULT_RUNTIME_PROCESS_OUTPUT_LIMIT_BYTES,
) -> BoundedProcessResult:
    """Run one command with bounded output and process-tree cleanup."""
    if output_limit_bytes < 0:
        raise ValueError("output_limit_bytes must be non-negative")
    timeout_seconds = None if timeout_ms is None else max(0.0, timeout_ms / 1_000)
    if sys.platform == "win32":
        creation_flags = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            creationflags=creation_flags,
        )
    else:
        process = subprocess.Popen(
            command,
            cwd=cwd,
            env=dict(env),
            shell=shell,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=False,
            bufsize=0,
            start_new_session=True,
        )
    process_group = process.pid if sys.platform != "win32" else None
    stdout = bytearray()
    stderr = bytearray()
    output_exceeded = threading.Event()
    stdout_done = threading.Event()
    stderr_done = threading.Event()
    stdout_thread = _start_reader(
        process.stdout,
        stdout,
        output_exceeded,
        stdout_done,
        output_limit_bytes,
    )
    stderr_thread = _start_reader(
        process.stderr,
        stderr,
        output_exceeded,
        stderr_done,
        output_limit_bytes,
    )
    deadline = None if timeout_seconds is None else time.monotonic() + timeout_seconds
    exit_observed_at: float | None = None
    outcome: str | None = None

    try:
        while True:
            now = time.monotonic()
            if output_exceeded.is_set():
                outcome = "output"
                break
            if deadline is not None and now >= deadline:
                outcome = "timeout"
                break
            if _child_exited_without_reaping(process):
                if stdout_done.is_set() and stderr_done.is_set():
                    break
                if exit_observed_at is None:
                    exit_observed_at = now
                elif now - exit_observed_at >= _PROCESS_DRAIN_GRACE_SECONDS:
                    # A descendant inherited a pipe after its parent exited.
                    # Close out the process group so response collection is bounded.
                    _signal_process_group(process, process_group, signal.SIGKILL)
                    break
            wait_seconds = _PROCESS_POLL_SECONDS
            if deadline is not None:
                wait_seconds = min(wait_seconds, max(0.0, deadline - now))
            output_exceeded.wait(wait_seconds)
    except BaseException:
        _force_cleanup(process, process_group, stdout_thread, stderr_thread)
        raise

    try:
        if outcome is not None:
            _terminate_process_tree(process, process_group)
        return_code = _reap_process(process)
        _drain_readers(process, stdout_thread, stderr_thread)
    except BaseException:
        _force_cleanup(process, process_group, stdout_thread, stderr_thread)
        raise
    stdout_text = _decode_bounded_output(stdout, output_limit_bytes)
    stderr_text = _decode_bounded_output(stderr, output_limit_bytes)
    if outcome == "output":
        return BoundedProcessResult(
            stdout=stdout_text,
            stderr=stderr_text or "Command output exceeded the 1 MiB per-stream limit",
            exit_code=125,
        )
    if outcome == "timeout":
        return BoundedProcessResult(
            stdout=stdout_text,
            stderr=stderr_text or "Command timed out",
            exit_code=124,
        )
    return BoundedProcessResult(stdout=stdout_text, stderr=stderr_text, exit_code=return_code)


def _start_reader(
    stream: IO[bytes] | None,
    output: bytearray,
    output_exceeded: threading.Event,
    done: threading.Event,
    output_limit_bytes: int,
) -> threading.Thread:
    def read_output() -> None:
        try:
            if stream is None:
                return
            while chunk := stream.read(65_536):
                remaining = max(0, output_limit_bytes - len(output))
                output.extend(chunk[:remaining])
                if len(chunk) > remaining:
                    output_exceeded.set()
        except (OSError, ValueError):
            pass
        finally:
            done.set()

    thread = threading.Thread(target=read_output, daemon=True)
    thread.start()
    return thread


def _decode_bounded_output(output: bytearray, limit_bytes: int) -> str:
    text = bytes(output).decode("utf-8", errors="replace")
    encoded = text.encode("utf-8")
    if len(encoded) <= limit_bytes:
        return text
    return encoded[:limit_bytes].decode("utf-8", errors="ignore")


def _child_exited_without_reaping(process: subprocess.Popen[bytes]) -> bool:
    waitid = getattr(os, "waitid", None)
    no_wait = getattr(os, "WNOWAIT", None)
    process_id_type = getattr(os, "P_PID", None)
    if sys.platform != "win32" and waitid is not None and no_wait is not None and process_id_type is not None:
        try:
            info = waitid(process_id_type, process.pid, os.WEXITED | os.WNOHANG | no_wait)
        except ChildProcessError:
            return True
        return info is not None
    return process.poll() is not None


def _terminate_process_tree(
    process: subprocess.Popen[bytes],
    process_group: int | None,
) -> None:
    if sys.platform == "win32":
        _terminate_windows_process_tree(process)
        return
    _signal_process_group(process, process_group, signal.SIGTERM)
    deadline = time.monotonic() + _PROCESS_TERMINATION_GRACE_SECONDS
    while time.monotonic() < deadline:
        time.sleep(_PROCESS_POLL_SECONDS)
    # Always escalate the group: the leader may have exited while a hostile
    # descendant ignored SIGTERM. The unreaped leader still reserves the pgid.
    _signal_process_group(process, process_group, signal.SIGKILL)


def _signal_process_group(
    process: subprocess.Popen[bytes],
    process_group: int | None,
    signum: int,
) -> None:
    if process_group is not None:
        try:
            os.killpg(process_group, signum)
            return
        except (ProcessLookupError, PermissionError):
            pass
    try:
        process.send_signal(signum)
    except (ProcessLookupError, OSError):
        pass


def _terminate_windows_process_tree(process: subprocess.Popen[bytes]) -> None:
    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    taskkill = os.path.join(system_root, "System32", "taskkill.exe")
    try:
        killer = subprocess.Popen(
            [taskkill, "/pid", str(process.pid), "/t", "/f"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            env=default_shell_env(),
        )
        killer.wait(timeout=_PROCESS_TERMINATION_GRACE_SECONDS)
    except (OSError, subprocess.TimeoutExpired):
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass


def _force_cleanup(
    process: subprocess.Popen[bytes],
    process_group: int | None,
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> None:
    if sys.platform == "win32":
        _terminate_windows_process_tree(process)
    else:
        _signal_process_group(process, process_group, signal.SIGKILL)
    try:
        _reap_process(process)
    except BaseException:
        pass
    _drain_readers(process, stdout_thread, stderr_thread)


def _reap_process(process: subprocess.Popen[bytes]) -> int:
    try:
        return process.wait(timeout=_PROCESS_DRAIN_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            process.kill()
        except (ProcessLookupError, OSError):
            pass
        try:
            return process.wait(timeout=_PROCESS_DRAIN_GRACE_SECONDS)
        except subprocess.TimeoutExpired:
            return -1


def _drain_readers(
    process: subprocess.Popen[bytes],
    stdout_thread: threading.Thread,
    stderr_thread: threading.Thread,
) -> None:
    deadline = time.monotonic() + _PROCESS_DRAIN_GRACE_SECONDS
    for thread in (stdout_thread, stderr_thread):
        thread.join(max(0.0, deadline - time.monotonic()))
    for stream in (process.stdout, process.stderr):
        if stream is not None:
            try:
                stream.close()
            except (OSError, ValueError):
                pass
