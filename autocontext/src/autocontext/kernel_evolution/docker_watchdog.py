"""Crash-independent host watchdog for Docker kernel worker deadlines."""

from __future__ import annotations

import math
import os
import re
import signal
import stat
import subprocess
import sys
import threading
import time
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from autocontext.execution.docker_isolation import sanitized_docker_environment
from autocontext.kernel_evolution import _process_control

DOCKER_KERNEL_OWNER_LABEL = "ai.autocontext.kernel-worker"
CLEANUP_TIMEOUT_SECONDS = 10.0
_POLL_SECONDS = 0.2


def crash_safe_container_creation_policy() -> dict[str, bool | str]:
    """Describe the unavailable creator-supervisor ownership boundary."""

    return {
        "required": "supervised-create-before-coordinator-ownership/v1",
        "available": False,
        "reason": (
            "protected accelerator evidence requires crash-safe container creation; "
            "the v1 coordinator-owned docker create path is unsupported"
        ),
    }


def docker_container_missing(completed: subprocess.CompletedProcess[Any]) -> bool:
    detail = f"{completed.stderr or ''}\n{completed.stdout or ''}".casefold()
    return "no such container" in detail or "no such object" in detail


def is_unix_socket(path: Path) -> bool:
    """Return whether a path currently names a Unix-domain socket."""

    try:
        return stat.S_ISSOCK(path.lstat().st_mode)
    except OSError:
        return False


def create_docker_container(command: Sequence[str], *, timeout_seconds: float) -> None:
    """Create, but do not start, one fully configured Docker container."""

    if len(command) < 2 or command[1] != "run":
        raise RuntimeError("Docker authority command must begin with 'docker run'")
    if not math.isfinite(timeout_seconds) or timeout_seconds <= 0:
        raise subprocess.TimeoutExpired(command, max(0.0, timeout_seconds))
    completed = subprocess.run(  # noqa: S603
        [command[0], "create", *command[2:]],
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout_seconds,
        env=sanitized_docker_environment(),
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-240:]
        raise RuntimeError(f"Docker authority container creation failed: {detail or 'unknown error'}")


def start_attached_docker_container(
    docker_binary: str,
    container_name: str,
    *,
    capture_output: bool,
) -> subprocess.Popen[bytes]:
    """Start and attach to a container that already has a live watchdog."""

    output = subprocess.PIPE if capture_output else subprocess.DEVNULL
    return subprocess.Popen(  # noqa: S603
        [docker_binary, "start", "--attach", container_name],
        stdin=subprocess.DEVNULL,
        stdout=output,
        stderr=output,
        start_new_session=True,
        env=sanitized_docker_environment(),
    )


def launch_bounded_output_drains(
    process: subprocess.Popen[bytes],
    *,
    max_output_bytes: int,
    stdout: _process_control.BoundedOutput,
    stderr: _process_control.BoundedOutput,
    quota_exceeded: threading.Event,
    terminate: Callable[[], None],
) -> list[threading.Thread]:
    """Drain both attached pipes with the shared quota and termination signal."""

    assert process.stdout is not None and process.stderr is not None
    drains = [
        threading.Thread(
            target=_process_control.drain_bounded,
            args=(stream, max_output_bytes, result, quota_exceeded, terminate),
            daemon=True,
        )
        for stream, result in ((process.stdout, stdout), (process.stderr, stderr))
    ]
    for thread in drains:
        thread.start()
    return drains


def remove_docker_container(docker_binary: str, container_identity: str) -> None:
    """Force-remove one exact Docker container identity."""

    completed = subprocess.run(  # noqa: S603
        [docker_binary, "rm", "-f", container_identity],
        check=False,
        capture_output=True,
        text=True,
        timeout=CLEANUP_TIMEOUT_SECONDS,
        env=sanitized_docker_environment(),
    )
    if completed.returncode != 0 and not docker_container_missing(completed):
        raise RuntimeError((completed.stderr or completed.stdout).strip()[-240:])


def verify_docker_container_removed(docker_binary: str, container_identity: str) -> None:
    """Fail unless Docker confirms that the exact identity is absent."""

    completed = subprocess.run(  # noqa: S603
        [docker_binary, "inspect", container_identity],
        check=False,
        capture_output=True,
        text=True,
        timeout=CLEANUP_TIMEOUT_SECONDS,
        env=sanitized_docker_environment(),
    )
    if completed.returncode == 0:
        raise RuntimeError("authority container remained after teardown")
    if not docker_container_missing(completed):
        detail = (completed.stderr or completed.stdout).strip()[-240:]
        raise RuntimeError(f"authority container removal verification failed: {detail or 'unknown error'}")


def docker_image_available(docker_binary: str, image: str) -> bool:
    """Return whether the exact pinned image is present without pulling it."""

    completed = subprocess.run(  # noqa: S603
        [docker_binary, "image", "inspect", image],
        check=False,
        capture_output=True,
        text=True,
        timeout=CLEANUP_TIMEOUT_SECONDS,
        env=sanitized_docker_environment(),
    )
    return completed.returncode == 0


def terminate_process_group(proc: subprocess.Popen[bytes], *, description: str) -> None:
    """Terminate and reap a process session, escalating to SIGKILL fail-closed."""

    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        proc.wait(timeout=CLEANUP_TIMEOUT_SECONDS / 2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        try:
            proc.wait(timeout=CLEANUP_TIMEOUT_SECONDS / 2)
        except subprocess.TimeoutExpired as exc:
            raise RuntimeError(f"{description} process group remained alive after SIGKILL") from exc
    if proc.poll() is None:
        raise RuntimeError(f"{description} process remained alive after termination")


def terminate_attached_docker_processes(
    processes: Iterable[tuple[str, subprocess.Popen[bytes] | None]],
) -> list[str]:
    """Boundedly terminate and reap attached Docker CLI process groups."""

    errors: list[str] = []
    for role, process in processes:
        if process is None:
            continue
        try:
            terminate_process_group(process, description=f"{role} authority Docker attach")
        except (OSError, RuntimeError, subprocess.SubprocessError) as exc:
            errors.append(f"{role} Docker attach: {type(exc).__name__}: {exc}")
    return errors


def launch_deadline_watchdog(
    docker_binary: str,
    container_name: str,
    expires_at: float,
    ready_path: Path,
) -> subprocess.Popen[bytes]:
    """Start a detached helper and wait until it owns the deadline."""

    watchdog = subprocess.Popen(  # noqa: S603
        [
            sys.executable,
            str(Path(__file__).resolve()),
            "--docker-deadline-watchdog",
            docker_binary,
            container_name,
            f"{expires_at:.9f}",
            str(ready_path),
            str(os.getpid()),
        ],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        close_fds=True,
        start_new_session=True,
        env=sanitized_docker_environment(),
    )
    ready_deadline = time.monotonic() + min(2.0, CLEANUP_TIMEOUT_SECONDS)
    while not ready_path.exists():
        if watchdog.poll() is not None:
            raise RuntimeError("Docker deadline watchdog exited before becoming ready")
        if time.monotonic() >= ready_deadline:
            terminate_process_group(watchdog, description="Docker deadline watchdog")
            raise RuntimeError("Docker deadline watchdog did not become ready")
        time.sleep(0.01)
    return watchdog


def run_deadline_watchdog(
    docker_binary: str,
    container_name: str,
    expires_at: float,
    ready_path: Path,
    coordinator_pid: int | None = None,
) -> int:
    """Remove the owned container at its deadline or when its coordinator dies."""

    if (
        not docker_binary
        or not container_name
        or any(char in docker_binary for char in "\r\n\0")
        or re.fullmatch(r"[A-Za-z0-9_.-]+", container_name) is None
        or not math.isfinite(expires_at)
        or expires_at <= 0
        or (coordinator_pid is not None and coordinator_pid < 2)
    ):
        return 2
    try:
        ready_path.write_text("ready\n", encoding="ascii")
    except OSError:
        return 2
    deadline = time.monotonic() + max(0.0, expires_at - time.time())
    while time.monotonic() < deadline and (coordinator_pid is None or os.getppid() == coordinator_pid):
        time.sleep(min(_POLL_SECONDS, max(0.0, deadline - time.monotonic())))

    env = sanitized_docker_environment()
    filter_value = f"label={DOCKER_KERNEL_OWNER_LABEL}={container_name}"
    # The container exists before this watchdog is launched. Keep ownership
    # until removal is verified, including across a temporary daemon outage;
    # a fixed retry window could otherwise abandon live GPU work.
    while True:
        try:
            listed = subprocess.run(  # noqa: S603
                [docker_binary, "ps", "-aq", "--filter", filter_value],
                check=False,
                capture_output=True,
                text=True,
                timeout=min(2.0, CLEANUP_TIMEOUT_SECONDS),
                env=env,
            )
            if listed.returncode == 0:
                ids = [item for item in listed.stdout.splitlines() if item.strip()]
                if not ids:
                    return 0
                subprocess.run(  # noqa: S603
                    [docker_binary, "rm", "-f", *ids],
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=min(2.0, CLEANUP_TIMEOUT_SECONDS),
                    env=env,
                )
        except (OSError, subprocess.SubprocessError):
            pass
        time.sleep(_POLL_SECONDS)


def _entrypoint(argv: Sequence[str]) -> int:
    if len(argv) != 7 or argv[1] != "--docker-deadline-watchdog":
        return 2
    try:
        expires_at = float(argv[4])
        coordinator_pid = int(argv[6])
    except ValueError:
        return 2
    return run_deadline_watchdog(argv[2], argv[3], expires_at, Path(argv[5]), coordinator_pid)


if __name__ == "__main__":  # pragma: no cover - exercised as a detached host helper
    raise SystemExit(_entrypoint(sys.argv))
