"""Security regressions for the local Python child-process boundary."""
from __future__ import annotations

import os
import signal
import threading
import time
from pathlib import Path

import pytest

import autocontext.execution.isolated_python as isolation_module
from autocontext.execution.isolated_python import (
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolatedOutputLimitError,
    IsolationUnavailableError,
    run_isolated_json,
)


def test_child_does_not_inherit_environment_or_open_file_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    secret_name = "AUTOCONTEXT_ISOLATION_TEST_SECRET"
    monkeypatch.setenv(secret_name, "must-not-cross-boundary")
    inherited_file = (tmp_path / "inherited.txt").open("w", encoding="utf-8")
    inherited_fd = inherited_file.fileno()

    def inspect_ambient_state() -> dict[str, object]:
        try:
            os.fstat(inherited_fd)
            fd_open = True
        except OSError:
            fd_open = False
        return {
            "secret": os.environ.get(secret_name),
            "fd_open": fd_open,
        }

    try:
        result = run_isolated_json(inspect_ambient_state, timeout_seconds=1.0)
    finally:
        inherited_file.close()

    assert result == {"secret": None, "fd_open": False}


def test_parent_kills_non_cooperative_child_at_wall_timeout() -> None:
    def never_returns() -> None:
        while True:
            pass

    started = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated_json(never_returns, timeout_seconds=0.1)
    assert time.monotonic() - started < 2.0


def test_child_output_is_size_limited() -> None:
    with pytest.raises(IsolatedOutputLimitError):
        run_isolated_json(
            lambda: "x" * 100_000,
            timeout_seconds=1.0,
            max_output_bytes=1_024,
        )


def test_output_limit_kills_term_resistant_descendants(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The leader stays unreaped until its entire process group is killed."""
    heartbeat = tmp_path / "descendant-heartbeat.txt"
    descendant_pid_file = tmp_path / "descendant-pid.txt"

    def no_resource_limits(**_kwargs: object) -> None:
        # This regression intentionally permits one descendant so cleanup can
        # be verified independently of the production child-task ceiling.
        return None

    monkeypatch.setattr(isolation_module, "_apply_resource_limits", no_resource_limits)

    def spawn_descendant_and_overflow() -> str:
        descendant_pid = os.fork()
        if descendant_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            counter = 0
            while True:
                heartbeat.write_text(str(counter), encoding="utf-8")
                counter += 1
                time.sleep(0.01)
        descendant_pid_file.write_text(str(descendant_pid), encoding="utf-8")
        deadline = time.monotonic() + 1.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return "x" * 100_000

    with pytest.raises(IsolatedOutputLimitError):
        run_isolated_json(
            spawn_descendant_and_overflow,
            timeout_seconds=2.0,
            max_output_bytes=1_024,
        )

    assert heartbeat.exists()
    before = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.1)
    after = heartbeat.read_text(encoding="utf-8")
    if after != before:
        # Avoid leaking a runaway process if this assertion regresses.
        os.kill(int(descendant_pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
    assert after == before


def test_collection_interrupt_kills_and_reaps_child_group(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Unexpected BaseException during parent collection cannot orphan work."""
    heartbeat = tmp_path / "interrupt-descendant-heartbeat.txt"
    descendant_pid_file = tmp_path / "interrupt-descendant-pid.txt"
    leader_pids: list[int] = []

    monkeypatch.setattr(isolation_module, "_apply_resource_limits", lambda **_kwargs: None)

    def run_process_group() -> None:
        descendant_pid = os.fork()
        if descendant_pid == 0:
            signal.signal(signal.SIGTERM, signal.SIG_IGN)
            counter = 0
            while True:
                heartbeat.write_text(str(counter), encoding="utf-8")
                counter += 1
                time.sleep(0.01)
        descendant_pid_file.write_text(str(descendant_pid), encoding="utf-8")
        while True:
            time.sleep(0.01)

    def interrupt_collection(pid: int, _read_fd: int, **_kwargs: object) -> tuple[bytes, int]:
        leader_pids.append(pid)
        deadline = time.monotonic() + 2.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        if not heartbeat.exists():
            raise AssertionError("isolated descendant did not start")
        raise KeyboardInterrupt

    monkeypatch.setattr(isolation_module, "_collect_child", interrupt_collection)
    with pytest.raises(KeyboardInterrupt):
        run_isolated_json(run_process_group, timeout_seconds=2.0)

    assert leader_pids
    with pytest.raises(ChildProcessError):
        os.waitpid(leader_pids[0], os.WNOHANG)

    before = heartbeat.read_text(encoding="utf-8")
    time.sleep(0.1)
    after = heartbeat.read_text(encoding="utf-8")
    if after != before:
        os.kill(int(descendant_pid_file.read_text(encoding="utf-8")), signal.SIGKILL)
    assert after == before


def test_child_result_is_json_not_pickle(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-executed.txt"

    class PickleOnlyPayload:
        def __reduce__(self) -> tuple[object, tuple[str, str]]:
            return Path.write_text, (marker, "unsafe")  # type: ignore[return-value]

    with pytest.raises(IsolatedExecutionError, match="not JSON-compatible"):
        run_isolated_json(lambda: PickleOnlyPayload(), timeout_seconds=1.0)
    assert not marker.exists()


def test_child_can_start_a_bounded_helper_thread() -> None:
    """Supported injected capabilities may use a small helper thread pool."""

    def run_helper() -> bool:
        completed: list[bool] = []
        helper = threading.Thread(target=lambda: completed.append(True))
        helper.start()
        helper.join(timeout=1.0)
        return not helper.is_alive() and completed == [True]

    assert run_isolated_json(run_helper, timeout_seconds=2.0) is True


def test_worker_thread_fails_closed_without_starting_child() -> None:
    failures: list[BaseException] = []

    def call_from_worker() -> None:
        try:
            run_isolated_json(lambda: True, timeout_seconds=1.0)
        except BaseException as exc:
            failures.append(exc)

    worker = threading.Thread(target=call_from_worker)
    worker.start()
    worker.join(timeout=1.0)

    assert not worker.is_alive()
    assert len(failures) == 1
    assert isinstance(failures[0], IsolationUnavailableError)


def test_fork_denial_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    executed = False

    def denied_fork() -> int:
        raise PermissionError("fork blocked by sandbox")

    def work() -> bool:
        nonlocal executed
        executed = True
        return True

    monkeypatch.setattr(os, "fork", denied_fork)
    with pytest.raises(IsolationUnavailableError, match="unable to start"):
        run_isolated_json(work, timeout_seconds=1.0)
    assert not executed
