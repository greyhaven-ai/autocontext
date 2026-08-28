"""Security regressions for the local Python child-process boundary."""
from __future__ import annotations

import ctypes
import os
import select
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

import autocontext.execution._process_group as process_group_module
import autocontext.execution.isolated_python as isolation_module
from autocontext.execution.isolated_python import (
    IsolatedExecutionError,
    IsolatedExecutionTimeout,
    IsolatedOutputLimitError,
    IsolationUnavailableError,
    run_isolated_json,
)

_requires_supported_local_isolation = pytest.mark.skipif(
    not isolation_module.local_isolation_available(),
    reason="requires a host with supported local child-process isolation",
)


def _install_fake_waitid_constants(monkeypatch: pytest.MonkeyPatch) -> None:
    for name, value in (("P_PID", 1), ("WNOWAIT", 2), ("WEXITED", 4), ("WNOHANG", 8)):
        monkeypatch.setattr(process_group_module.os, name, value, raising=False)


def _install_fake_child_ownership_primitives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(isolation_module.os, "waitid", lambda *_args: None, raising=False)
    for name, value in (("P_PID", 1), ("WNOWAIT", 2), ("WEXITED", 4), ("WNOHANG", 8)):
        monkeypatch.setattr(isolation_module.os, name, value, raising=False)


def _kill_recorded_process(pid_path: Path) -> None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@_requires_supported_local_isolation
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


@_requires_supported_local_isolation
def test_parent_kills_non_cooperative_child_at_wall_timeout() -> None:
    def never_returns() -> None:
        while True:
            pass

    started = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated_json(never_returns, timeout_seconds=0.1)
    assert time.monotonic() - started < 2.0


@_requires_supported_local_isolation
def test_child_output_is_size_limited() -> None:
    with pytest.raises(IsolatedOutputLimitError):
        run_isolated_json(
            lambda: "x" * 100_000,
            timeout_seconds=1.0,
            max_output_bytes=1_024,
        )


@_requires_supported_local_isolation
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

    try:
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
        assert after == before
    finally:
        _kill_recorded_process(descendant_pid_file)


@_requires_supported_local_isolation
def test_successful_result_kills_descendants_that_close_the_result_pipe(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A valid response must not let an IPC-detached descendant survive."""
    heartbeat = tmp_path / "success-descendant-heartbeat.txt"
    descendant_pid_file = tmp_path / "success-descendant-pid.txt"

    monkeypatch.setattr(isolation_module, "_apply_resource_limits", lambda **_kwargs: None)

    def spawn_detached_from_ipc_descendant() -> dict[str, bool]:
        descendant_pid = os.fork()
        if descendant_pid == 0:
            # Closing the inherited result pipe forces EOF before the leader's
            # process group has become empty.
            os.closerange(3, 4_096)
            counter = 0
            while True:
                heartbeat.write_text(str(counter), encoding="utf-8")
                counter += 1
                time.sleep(0.01)
        descendant_pid_file.write_text(str(descendant_pid), encoding="utf-8")
        deadline = time.monotonic() + 1.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return {"complete": True}

    try:
        assert run_isolated_json(spawn_detached_from_ipc_descendant, timeout_seconds=2.0) == {
            "complete": True
        }

        assert heartbeat.exists()
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.1)
        after = heartbeat.read_text(encoding="utf-8")
        assert after == before
    finally:
        _kill_recorded_process(descendant_pid_file)


@_requires_supported_local_isolation
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
    try:
        with pytest.raises(KeyboardInterrupt):
            run_isolated_json(run_process_group, timeout_seconds=2.0)

        assert leader_pids
        with pytest.raises(ChildProcessError):
            os.waitpid(leader_pids[0], os.WNOHANG)

        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.1)
        after = heartbeat.read_text(encoding="utf-8")
        assert after == before
    finally:
        _kill_recorded_process(descendant_pid_file)


@_requires_supported_local_isolation
def test_child_result_is_json_not_pickle(tmp_path: Path) -> None:
    marker = tmp_path / "pickle-executed.txt"

    class PickleOnlyPayload:
        def __reduce__(self) -> tuple[object, tuple[str, str]]:
            return Path.write_text, (marker, "unsafe")  # type: ignore[return-value]

    with pytest.raises(IsolatedExecutionError, match="not JSON-compatible"):
        run_isolated_json(lambda: PickleOnlyPayload(), timeout_seconds=1.0)
    assert not marker.exists()


@_requires_supported_local_isolation
def test_child_can_start_a_bounded_helper_thread() -> None:
    """Supported injected capabilities may use a small helper thread pool."""

    def run_helper() -> bool:
        completed: list[bool] = []
        helper = threading.Thread(target=lambda: completed.append(True))
        helper.start()
        helper.join(timeout=1.0)
        return not helper.is_alive() and completed == [True]

    assert run_isolated_json(run_helper, timeout_seconds=2.0) is True


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin uses process-only RLIMIT_NPROC")
@_requires_supported_local_isolation
def test_darwin_child_denies_fork_but_allows_helper_thread() -> None:
    def try_thread_and_fork() -> dict[str, object]:
        completed: list[bool] = []
        helper = threading.Thread(target=lambda: completed.append(True))
        helper.start()
        helper.join(timeout=1.0)
        try:
            descendant_pid = os.fork()
        except OSError as exc:
            return {
                "thread_completed": completed == [True] and not helper.is_alive(),
                "fork_denied": True,
                "fork_errno": exc.errno,
            }
        if descendant_pid == 0:
            os._exit(0)
        os.waitpid(descendant_pid, 0)
        return {"thread_completed": completed == [True], "fork_denied": False}

    result = run_isolated_json(try_thread_and_fork, timeout_seconds=2.0)
    assert result["thread_completed"] is True
    assert result["fork_denied"] is True


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp regression")
def test_linux_success_cleanup_blocks_setsid_escape(tmp_path: Path) -> None:
    heartbeat = tmp_path / "setsid-success-heartbeat.txt"
    descendant_pid_file = tmp_path / "setsid-success-pid.txt"

    def attempt_escape_then_return() -> dict[str, bool]:
        descendant_pid = os.fork()
        if descendant_pid == 0:
            try:
                os.setsid()
            except OSError:
                pass
            os.closerange(3, 4_096)
            counter = 0
            while True:
                heartbeat.write_text(str(counter), encoding="utf-8")
                counter += 1
                time.sleep(0.01)
        descendant_pid_file.write_text(str(descendant_pid), encoding="utf-8")
        deadline = time.monotonic() + 1.0
        while not heartbeat.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        return {"complete": True}

    try:
        assert run_isolated_json(attempt_escape_then_return, timeout_seconds=2.0) == {
            "complete": True
        }
        assert heartbeat.exists()
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.1)
        after = heartbeat.read_text(encoding="utf-8")
        assert after == before
    finally:
        _kill_recorded_process(descendant_pid_file)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp regression")
def test_linux_timeout_cleanup_blocks_setsid_escape(tmp_path: Path) -> None:
    heartbeat = tmp_path / "setsid-timeout-heartbeat.txt"
    descendant_pid_file = tmp_path / "setsid-timeout-pid.txt"

    def attempt_escape_then_hang() -> None:
        descendant_pid = os.fork()
        if descendant_pid == 0:
            try:
                os.setsid()
            except OSError:
                pass
            counter = 0
            while True:
                heartbeat.write_text(str(counter), encoding="utf-8")
                counter += 1
                time.sleep(0.01)
        descendant_pid_file.write_text(str(descendant_pid), encoding="utf-8")
        while True:
            time.sleep(0.01)

    try:
        with pytest.raises(IsolatedExecutionTimeout):
            run_isolated_json(attempt_escape_then_hang, timeout_seconds=0.25)

        assert heartbeat.exists()
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.1)
        after = heartbeat.read_text(encoding="utf-8")
        assert after == before
    finally:
        _kill_recorded_process(descendant_pid_file)


def test_linux_process_limit_is_relative_to_the_current_uid_task_baseline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[int, tuple[int, int]]] = []
    current_limit = [10_000, 10_000]

    def set_limit(resource_id: int, limits: tuple[int, int]) -> None:
        applied.append((resource_id, limits))
        current_limit[:] = limits

    fake_resource = SimpleNamespace(
        RLIMIT_NPROC=7,
        RLIM_INFINITY=-1,
        getrlimit=lambda _resource_id: tuple(current_limit),
        setrlimit=set_limit,
    )
    monkeypatch.setattr(isolation_module.sys, "platform", "linux")
    monkeypatch.setattr(isolation_module, "_safe_unprivileged_uid", lambda: 501)
    monkeypatch.setattr(isolation_module, "_linux_capability_masks", lambda: (0, 0, 0, 0))
    monkeypatch.setattr(isolation_module, "_linux_same_uid_task_count", lambda: 137)

    isolation_module._apply_linux_process_limit(fake_resource)

    expected = 137 + isolation_module.DEFAULT_MAX_CHILD_TASKS
    assert applied == [(fake_resource.RLIMIT_NPROC, (expected, expected))]


@pytest.mark.parametrize(
    "capability_masks",
    [
        (0, 1 << 7, 0, 0),  # permitted CAP_SETUID can become effective in-child
        (0, 0, 1 << 21, 0),  # effective CAP_SYS_ADMIN bypasses RLIMIT_NPROC
        (1, 0, 0, 0),
        (0, 0, 0, 1),
    ],
)
def test_linux_process_limit_rejects_any_available_capability(
    monkeypatch: pytest.MonkeyPatch,
    capability_masks: tuple[int, int, int, int],
) -> None:
    fake_resource = SimpleNamespace(RLIMIT_NPROC=7, RLIM_INFINITY=-1)
    monkeypatch.setattr(isolation_module.sys, "platform", "linux")
    monkeypatch.setattr(isolation_module, "_safe_unprivileged_uid", lambda: 501)
    monkeypatch.setattr(
        isolation_module,
        "_linux_capability_masks",
        lambda: capability_masks,
    )

    with pytest.raises(IsolationUnavailableError, match="empty capability masks"):
        isolation_module._apply_linux_process_limit(fake_resource)


def test_darwin_process_containment_sets_and_verifies_single_process_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    applied: list[tuple[int, tuple[int, int]]] = []
    current_limit = [10_000, 16_000]

    def set_limit(resource_id: int, limits: tuple[int, int]) -> None:
        applied.append((resource_id, limits))
        current_limit[:] = limits

    fake_resource = SimpleNamespace(
        RLIMIT_NPROC=7,
        getrlimit=lambda _resource_id: tuple(current_limit),
        setrlimit=set_limit,
    )
    monkeypatch.setattr(isolation_module.sys, "platform", "darwin")
    monkeypatch.setattr(isolation_module, "_safe_unprivileged_uid", lambda: 501)

    isolation_module._apply_descendant_containment(fake_resource)

    assert applied == [(fake_resource.RLIMIT_NPROC, (1, 1))]


@pytest.mark.parametrize(
    "identities",
    [
        (0, 501, 501),
        (501, 501, 502),
        (501, 502, 501),
    ],
)
def test_process_containment_rejects_root_or_switchable_uid_identities(
    monkeypatch: pytest.MonkeyPatch,
    identities: tuple[int, int, int],
) -> None:
    monkeypatch.setattr(isolation_module.sys, "platform", "linux")
    monkeypatch.setattr(
        isolation_module.os,
        "getresuid",
        lambda: identities,
        raising=False,
    )

    assert isolation_module._safe_unprivileged_uid() is None


def test_darwin_identity_check_requires_untainted_matching_nonroot_uid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIsSetUGid:
        argtypes: list[object] = []
        restype: object | None = None

        def __call__(self) -> int:
            return 0

    fake_libsystem = SimpleNamespace(issetugid=FakeIsSetUGid())
    monkeypatch.setattr(isolation_module.sys, "platform", "darwin")
    monkeypatch.setattr(isolation_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(isolation_module.os, "geteuid", lambda: 501)
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: fake_libsystem)

    assert isolation_module._safe_unprivileged_uid() == 501


def test_darwin_identity_check_rejects_set_id_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeIsSetUGid:
        argtypes: list[object] = []
        restype: object | None = None

        def __call__(self) -> int:
            return 1

    fake_libsystem = SimpleNamespace(issetugid=FakeIsSetUGid())
    monkeypatch.setattr(isolation_module.sys, "platform", "darwin")
    monkeypatch.setattr(isolation_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(isolation_module.os, "geteuid", lambda: 501)
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: fake_libsystem)

    assert isolation_module._safe_unprivileged_uid() is None


def test_darwin_identity_check_fails_closed_on_ctypes_argument_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FailingIsSetUGid:
        argtypes: list[object] = []
        restype: object | None = None

        def __call__(self) -> int:
            raise ctypes.ArgumentError("invalid ABI")

    fake_libsystem = SimpleNamespace(issetugid=FailingIsSetUGid())
    monkeypatch.setattr(isolation_module.sys, "platform", "darwin")
    monkeypatch.setattr(isolation_module.os, "getuid", lambda: 501)
    monkeypatch.setattr(isolation_module.os, "geteuid", lambda: 501)
    monkeypatch.setattr(ctypes, "CDLL", lambda _path: fake_libsystem)

    assert isolation_module._safe_unprivileged_uid() is None


def test_darwin_thread_cleanup_attempts_every_port_after_one_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    deallocated: list[int] = []
    array_deallocations: list[tuple[int, int, int]] = []

    class FakeCall:
        argtypes: list[object] = []
        restype: object | None = None

        def __init__(self, callback: Any) -> None:
            self._callback = callback

        def __call__(self, *args: int) -> int:
            return self._callback(*args)

    def deallocate_port(_task_port: int, thread_port: int) -> int:
        deallocated.append(thread_port)
        if thread_port == 10:
            raise OSError("first port cleanup failed")
        return 0

    def deallocate_array(task_port: int, address: int, size: int) -> int:
        array_deallocations.append((task_port, address, size))
        return 0

    libsystem = SimpleNamespace(
        mach_port_deallocate=FakeCall(deallocate_port),
        vm_deallocate=FakeCall(deallocate_array),
    )
    monkeypatch.setattr(
        ctypes,
        "cast",
        lambda _ports, _pointer_type: SimpleNamespace(value=1234),
    )

    assert not isolation_module._isolation_platform._release_darwin_thread_ports(
        libsystem,
        99,
        [10, 11, 12],
        3,
    )
    assert deallocated == [10, 11, 12]
    assert array_deallocations == [(99, 1234, 3 * ctypes.sizeof(ctypes.c_uint))]


def test_linux_process_limit_rechecks_uid_identities_in_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_resource = SimpleNamespace(RLIMIT_NPROC=7, RLIM_INFINITY=-1)
    monkeypatch.setattr(isolation_module.sys, "platform", "linux")
    monkeypatch.setattr(isolation_module, "_safe_unprivileged_uid", lambda: None)

    with pytest.raises(IsolationUnavailableError, match="matching non-root UID"):
        isolation_module._apply_linux_process_limit(fake_resource)


def test_darwin_process_limit_rechecks_uid_identities_in_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake_resource = SimpleNamespace(RLIMIT_NPROC=7)
    monkeypatch.setattr(isolation_module.sys, "platform", "darwin")
    monkeypatch.setattr(isolation_module, "_safe_unprivileged_uid", lambda: None)

    with pytest.raises(IsolationUnavailableError, match="containment is unavailable"):
        isolation_module._apply_descendant_containment(fake_resource)


def test_linux_seccomp_rules_validate_arch_and_normalize_x32_before_denials() -> None:
    rules = isolation_module._linux_process_group_filter_rules(
        "x86_64",
        errno_value=1,
    )

    assert rules[0] == (0x20, 0, 0, 4)  # load seccomp_data.arch
    assert rules[1] == (0x15, 1, 0, 0xC000003E)  # require AUDIT_ARCH_X86_64
    assert rules[2] == (0x06, 0, 0, 0x80000000)  # kill mismatched compat ABI
    assert rules[3] == (0x20, 0, 0, 0)  # load seccomp_data.nr
    assert rules[4] == (0x54, 0, 0, 0xBFFFFFFF)  # clear __X32_SYSCALL_BIT
    assert rules[5][3] == 112  # setsid
    assert rules[7][3] == 109  # setpgid
    assert rules[9][3] == 272  # unshare
    assert rules[11][3] == 308  # setns
    assert rules[13][3] == 435  # clone3
    assert rules[15] == (0x15, 0, 3, 56)  # clone, else skip its argument rules
    assert rules[16] == (0x20, 0, 0, 16)  # load clone flags
    assert rules[17] == (0x45, 0, 1, 0x10000000)  # deny CLONE_NEWUSER


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux seccomp regression")
def test_linux_child_cannot_create_a_user_namespace() -> None:
    if not isolation_module.local_isolation_available():
        pytest.skip("local Linux isolation is unavailable")

    def attempt_user_namespace() -> dict[str, int]:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.unshare.argtypes = [ctypes.c_int]
        libc.unshare.restype = ctypes.c_int
        result = libc.unshare(0x10000000)  # CLONE_NEWUSER
        return {"result": result, "errno": ctypes.get_errno()}

    assert run_isolated_json(attempt_user_namespace, timeout_seconds=1.0) == {
        "result": -1,
        "errno": 1,
    }


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


def test_main_thread_fails_closed_while_another_thread_is_alive() -> None:
    release = threading.Event()
    ready = threading.Event()

    def hold_thread() -> None:
        ready.set()
        release.wait(timeout=2.0)

    worker = threading.Thread(target=hold_thread)
    worker.start()
    assert ready.wait(timeout=1.0)
    try:
        with pytest.raises(IsolationUnavailableError, match="single-threaded"):
            run_isolated_json(lambda: True, timeout_seconds=1.0)
    finally:
        release.set()
        worker.join(timeout=1.0)

    assert not worker.is_alive()


def test_nondefault_sigchld_disposition_fails_closed() -> None:
    previous_handler = signal.getsignal(signal.SIGCHLD)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        assert not isolation_module.local_isolation_available()
        with pytest.raises(IsolationUnavailableError, match="supported Linux or macOS"):
            run_isolated_json(lambda: True, timeout_seconds=1.0)
    finally:
        signal.signal(signal.SIGCHLD, previous_handler)


@pytest.mark.parametrize(
    "primitive",
    ["waitid", "P_PID", "WNOWAIT", "WEXITED", "WNOHANG"],
)
def test_missing_child_ownership_primitive_fails_before_fork(
    primitive: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fork_called = False

    def record_fork() -> int:
        nonlocal fork_called
        fork_called = True
        raise AssertionError("fork must not be reached")

    _install_fake_child_ownership_primitives(monkeypatch)
    assert isolation_module._child_ownership_primitives_available()
    monkeypatch.setattr(isolation_module.os, primitive, None, raising=False)
    monkeypatch.setattr(isolation_module.os, "fork", record_fork)

    assert not isolation_module._child_ownership_primitives_available()
    with pytest.raises(IsolationUnavailableError):
        run_isolated_json(lambda: True, timeout_seconds=1.0)
    assert not fork_called


def test_child_ownership_primitives_are_rechecked_adjacent_to_fork(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fork_called = False

    def record_fork() -> int:
        nonlocal fork_called
        fork_called = True
        raise AssertionError("fork must not be reached")

    monkeypatch.setattr(isolation_module, "local_isolation_available", lambda: True)
    monkeypatch.setattr(
        isolation_module,
        "_child_ownership_primitives_available",
        lambda: False,
    )
    monkeypatch.setattr(isolation_module, "_native_thread_count", lambda: 1)
    monkeypatch.setattr(isolation_module, "_sigchld_disposition_is_safe", lambda: True)
    monkeypatch.setattr(isolation_module.os, "fork", record_fork)

    with pytest.raises(IsolationUnavailableError, match="ownership primitives changed"):
        run_isolated_json(lambda: True, timeout_seconds=1.0)
    assert not fork_called


def test_lost_child_ownership_never_signals_numeric_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    signals: list[tuple[int, int]] = []
    monkeypatch.setattr(
        isolation_module,
        "_child_state_without_reaping",
        lambda _pid: "ownership_lost",
    )
    monkeypatch.setattr(
        isolation_module,
        "_signal_process_group",
        lambda pid, signum: signals.append((pid, signum)),
    )
    monkeypatch.setattr(isolation_module, "_ISOLATION_OWNERSHIP_POISONED", False)
    try:
        with pytest.raises(IsolatedExecutionError, match="ownership was lost"):
            isolation_module._collect_child(
                424_242,
                read_fd,
                timeout_seconds=1.0,
                max_output_bytes=1_024,
            )
    finally:
        os.close(read_fd)

    assert signals == []


@pytest.mark.skipif(os.name != "posix", reason="high-fd regression is POSIX-specific")
def test_collect_child_supports_read_descriptor_above_select_fd_setsize(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import fcntl

    read_fd, write_fd = os.pipe()
    high_read_fd = -1
    try:
        try:
            high_read_fd = fcntl.fcntl(read_fd, fcntl.F_DUPFD_CLOEXEC, 4_097)
        except OSError:
            pytest.skip("process descriptor ceiling is below the regression fd")
        os.write(write_fd, b'{"ok":true}')
        os.close(write_fd)
        write_fd = -1
        monkeypatch.setattr(
            isolation_module,
            "_child_state_without_reaping",
            lambda _pid: "exited",
        )
        monkeypatch.setattr(isolation_module, "_signal_process_group", lambda *_args: True)
        monkeypatch.setattr(isolation_module.os, "waitpid", lambda pid, _flags: (pid, 0))

        raw, status = isolation_module._collect_child(
            424_242,
            high_read_fd,
            timeout_seconds=1.0,
            max_output_bytes=1_024,
        )
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)
        if high_read_fd >= 0:
            os.close(high_read_fd)

    assert raw == b'{"ok":true}'
    assert status == 0


@_requires_supported_local_isolation
def test_child_stuck_before_setsid_is_killed_by_owned_pid_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def never_finish_setsid() -> int:
        while True:
            time.sleep(1.0)

    monkeypatch.setattr(os, "setsid", never_finish_setsid)
    started = time.monotonic()
    with pytest.raises(IsolatedExecutionTimeout):
        run_isolated_json(lambda: True, timeout_seconds=0.05)

    assert time.monotonic() - started < 2.0


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_isolated_response_decoder_rejects_nonfinite_numbers(number: bytes) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        isolation_module._decode_isolated_response(
            b'{"version":1,"status":"ok","value":' + number + b"}"
        )


def test_isolated_response_decoder_normalizes_excessive_nesting() -> None:
    nested = b"[" * 20_000 + b"0" + b"]" * 20_000
    with pytest.raises(ValueError, match="excessively nested"):
        isolation_module._decode_isolated_response(
            b'{"version":1,"status":"ok","value":' + nested + b"}"
        )


@_requires_supported_local_isolation
def test_success_response_is_rejected_when_child_did_not_exit_cleanly(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    collect_child = isolation_module._collect_child

    def report_signal_status(
        pid: int,
        read_fd: int,
        *,
        timeout_seconds: float,
        max_output_bytes: int,
    ) -> tuple[bytes, int]:
        raw, _status = collect_child(
            pid,
            read_fd,
            timeout_seconds=timeout_seconds,
            max_output_bytes=max_output_bytes,
        )
        return raw, int(signal.SIGKILL)

    monkeypatch.setattr(isolation_module, "_collect_child", report_signal_status)
    with pytest.raises(IsolatedExecutionError, match="signal"):
        run_isolated_json(lambda: {"ok": True}, timeout_seconds=1.0)


@_requires_supported_local_isolation
def test_child_cannot_return_to_caller_after_replacing_os_exit() -> None:
    def replace_exit() -> bool:
        isolation_module.os._exit = lambda _status: None  # type: ignore[assignment]
        return True

    with pytest.raises(IsolatedExecutionError, match="exit 1"):
        run_isolated_json(replace_exit, timeout_seconds=1.0)


@_requires_supported_local_isolation
def test_child_result_pipe_close_failure_exits_before_work(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    parent_pid = os.getpid()
    original_close = os.close
    open_pipe = isolation_module._open_isolation_result_pipe
    read_descriptor = -1
    marker = tmp_path / "work-ran.txt"

    def tracked_pipe() -> tuple[int, int]:
        nonlocal read_descriptor
        read_descriptor, write_descriptor = open_pipe()
        return read_descriptor, write_descriptor

    def fail_child_read_close(descriptor: int) -> None:
        if os.getpid() != parent_pid and descriptor == read_descriptor:
            raise OSError("injected child close failure")
        original_close(descriptor)

    monkeypatch.setattr(isolation_module, "_open_isolation_result_pipe", tracked_pipe)
    monkeypatch.setattr(isolation_module.os, "close", fail_child_read_close)
    with pytest.raises(IsolatedExecutionError, match="exit 1"):
        run_isolated_json(
            lambda: marker.write_text("unsafe", encoding="utf-8"),
            timeout_seconds=1.0,
        )

    assert not marker.exists()


def test_parent_result_pipe_close_failure_terminates_before_collection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    read_descriptor, write_descriptor = os.pipe()
    original_close = os.close
    terminated: list[int] = []
    closed: list[int] = []
    collected = False

    def fail_parent_write_close(descriptor: int) -> None:
        if descriptor == write_descriptor:
            raise OSError("injected parent close failure")
        closed.append(descriptor)
        original_close(descriptor)

    def collect_child(*_args: object, **_kwargs: object) -> tuple[bytes, int]:
        nonlocal collected
        collected = True
        raise AssertionError("collection must not start after a close failure")

    monkeypatch.setattr(isolation_module, "local_isolation_available", lambda: True)
    monkeypatch.setattr(isolation_module, "_native_thread_count", lambda: 1)
    monkeypatch.setattr(isolation_module, "_sigchld_disposition_is_safe", lambda: True)
    monkeypatch.setattr(
        isolation_module,
        "_child_ownership_primitives_available",
        lambda: True,
    )
    monkeypatch.setattr(
        isolation_module,
        "_open_isolation_result_pipe",
        lambda: (read_descriptor, write_descriptor),
    )
    monkeypatch.setattr(isolation_module.os, "fork", lambda: 424_242)
    monkeypatch.setattr(isolation_module.os, "close", fail_parent_write_close)
    monkeypatch.setattr(
        isolation_module,
        "_terminate_process_tree",
        terminated.append,
    )
    monkeypatch.setattr(isolation_module, "_collect_child", collect_child)
    try:
        with pytest.raises(IsolatedExecutionError, match="parent isolation pipe"):
            run_isolated_json(lambda: True, timeout_seconds=1.0)
    finally:
        original_close(write_descriptor)

    assert terminated == [424_242]
    assert not collected
    assert read_descriptor in closed


def test_stranded_child_blocks_new_isolation_until_confirmed_reap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 424_242
    child_state = ["running"]
    clock = [0.0]
    reaper_started: list[bool] = []
    reaped: list[int] = []

    class DeferredReaper:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            reaper_started.append(True)

    def advancing_clock() -> float:
        clock[0] += 0.6
        return clock[0]

    monkeypatch.setattr(isolation_module, "_STRANDED_CHILDREN", set())
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILD_REAPER", None)
    monkeypatch.setattr(isolation_module, "_ISOLATION_OWNERSHIP_POISONED", False)
    monkeypatch.setattr(isolation_module.threading, "Thread", DeferredReaper)
    monkeypatch.setattr(
        isolation_module,
        "_child_state_without_reaping",
        lambda _pid: child_state[0],
    )
    monkeypatch.setattr(isolation_module, "_signal_process_group", lambda _pid, _signal: False)
    monkeypatch.setattr(isolation_module.os, "kill", lambda _pid, _signal: None)
    monkeypatch.setattr(isolation_module.os, "waitpid", lambda value, _options: reaped.append(value) or (value, 0))
    monkeypatch.setattr(isolation_module.time, "monotonic", advancing_clock)
    monkeypatch.setattr(isolation_module.time, "sleep", lambda _seconds: None)

    with pytest.raises(IsolatedExecutionError, match="did not exit"):
        isolation_module._terminate_process_tree(pid)

    assert reaper_started == [True]
    assert isolation_module._STRANDED_CHILDREN == {pid}
    assert not isolation_module._registered_children_are_clear()

    child_state[0] = "exited"
    assert isolation_module._registered_children_are_clear()
    assert reaped == [pid]
    assert isolation_module._STRANDED_CHILDREN == set()


@pytest.mark.skipif(
    not (sys.platform.startswith("linux") or sys.platform == "darwin"),
    reason="native thread counters are implemented for Linux and macOS",
)
def test_raw_pthread_fails_closed_even_when_python_thread_count_is_one() -> None:
    if threading.active_count() != 1:
        pytest.skip("test process already has Python-managed background threads")

    started = threading.Event()
    release = threading.Event()
    callback_type = ctypes.CFUNCTYPE(ctypes.c_void_p, ctypes.c_void_p)

    @callback_type
    def hold_native_thread(_argument: ctypes.c_void_p) -> None:
        started.set()
        release.wait(timeout=5.0)

    pthread = ctypes.c_void_p()
    libsystem = ctypes.CDLL(None)
    libsystem.pthread_create.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.c_void_p,
        callback_type,
        ctypes.c_void_p,
    ]
    libsystem.pthread_create.restype = ctypes.c_int
    libsystem.pthread_join.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    libsystem.pthread_join.restype = ctypes.c_int
    assert libsystem.pthread_create(ctypes.byref(pthread), None, hold_native_thread, None) == 0
    assert started.wait(timeout=1.0)
    try:
        assert threading.active_count() == 1
        assert not isolation_module.local_isolation_available()
        with pytest.raises(IsolationUnavailableError, match="single-threaded"):
            run_isolated_json(lambda: True, timeout_seconds=1.0)
    finally:
        release.set()
        assert libsystem.pthread_join(pthread, None) == 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Mach port accounting is Darwin-only")
def test_darwin_thread_count_releases_mach_port_rights() -> None:
    libsystem = ctypes.CDLL("/usr/lib/libSystem.B.dylib")
    task_port = ctypes.c_uint.in_dll(libsystem, "mach_task_self_").value

    def port_name_count() -> int:
        names = ctypes.POINTER(ctypes.c_uint)()
        name_count = ctypes.c_uint()
        types = ctypes.POINTER(ctypes.c_uint)()
        type_count = ctypes.c_uint()
        libsystem.mach_port_names.argtypes = [
            ctypes.c_uint,
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
            ctypes.POINTER(ctypes.c_uint),
            ctypes.POINTER(ctypes.POINTER(ctypes.c_uint)),
            ctypes.POINTER(ctypes.c_uint),
        ]
        libsystem.mach_port_names.restype = ctypes.c_int
        assert (
            libsystem.mach_port_names(
                task_port,
                ctypes.byref(names),
                ctypes.byref(name_count),
                ctypes.byref(types),
                ctypes.byref(type_count),
            )
            == 0
        )
        libsystem.vm_deallocate.argtypes = [
            ctypes.c_uint,
            ctypes.c_size_t,
            ctypes.c_size_t,
        ]
        libsystem.vm_deallocate.restype = ctypes.c_int
        for allocation, count in ((names, name_count.value), (types, type_count.value)):
            address = ctypes.cast(allocation, ctypes.c_void_p).value
            if address is not None:
                assert (
                    libsystem.vm_deallocate(
                        task_port,
                        address,
                        count * ctypes.sizeof(ctypes.c_uint),
                    )
                    == 0
                )
        return name_count.value

    before = port_name_count()
    for _ in range(50):
        assert isolation_module._darwin_native_thread_count() == 1
    after = port_name_count()
    assert after <= before + 1


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

    monkeypatch.setattr(isolation_module, "local_isolation_available", lambda: True)
    monkeypatch.setattr(isolation_module, "_child_ownership_primitives_available", lambda: True)
    monkeypatch.setattr(isolation_module, "_native_thread_count", lambda: 1)
    monkeypatch.setattr(isolation_module, "_sigchld_disposition_is_safe", lambda: True)
    monkeypatch.setattr(os, "fork", denied_fork)
    with pytest.raises(IsolationUnavailableError, match="unable to start"):
        run_isolated_json(work, timeout_seconds=1.0)
    assert not executed


def test_work_directory_failure_happens_before_result_pipe_allocation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pipe_called = False

    class FailingTemporaryDirectory:
        def __init__(self, *_args: object, **_kwargs: object) -> None:
            pass

        def __enter__(self) -> str:
            raise OSError("temporary storage unavailable")

        def cleanup(self) -> None:
            pass

    def record_pipe() -> tuple[int, int]:
        nonlocal pipe_called
        pipe_called = True
        raise AssertionError("pipe must not be allocated before tempdir entry")

    monkeypatch.setattr(isolation_module, "local_isolation_available", lambda: True)
    monkeypatch.setattr(isolation_module.tempfile, "TemporaryDirectory", FailingTemporaryDirectory)
    monkeypatch.setattr(isolation_module.os, "pipe", record_pipe)

    with pytest.raises(IsolationUnavailableError, match="working directory"):
        run_isolated_json(lambda: True, timeout_seconds=1.0)

    assert not pipe_called


@pytest.mark.skipif(os.name != "posix", reason="missing-stdio regression is POSIX-specific")
@_requires_supported_local_isolation
def test_isolation_result_pipe_survives_missing_standard_descriptors() -> None:
    status_read, status_write = os.pipe()
    script = """
import json
import os
import sys
from autocontext.execution.isolated_python import run_isolated_json

status_fd = int(sys.argv[1])
try:
    result = run_isolated_json(lambda: {"ok": True}, timeout_seconds=1.0)
    payload = {"result": result}
except BaseException as exc:
    payload = {"error": type(exc).__name__, "message": str(exc)}
os.write(status_fd, json.dumps(payload).encode("utf-8"))
"""

    def close_standard_descriptors() -> None:
        for descriptor in (0, 1, 2):
            try:
                os.close(descriptor)
            except OSError:
                pass

    process = subprocess.Popen(
        [sys.executable, "-c", script, str(status_write)],
        pass_fds=(status_write,),
        preexec_fn=close_standard_descriptors,
    )
    os.close(status_write)
    try:
        readable, _writable, _exceptional = select.select([status_read], [], [], 5.0)
        assert readable, "isolated child did not publish a result before the deadline"
        payload = os.read(status_read, 16_384)
        assert process.wait(timeout=5.0) == 0
    finally:
        os.close(status_read)
        if process.poll() is None:
            try:
                process.terminate()
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=1.0)
    assert payload == b'{"result": {"ok": true}}'


@_requires_supported_local_isolation
def test_child_setsid_failure_fails_closed_before_work_executes(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    marker = tmp_path / "setsid-work-executed.txt"

    def denied_setsid() -> int:
        raise PermissionError("setsid blocked")

    monkeypatch.setattr(os, "setsid", denied_setsid)
    with pytest.raises(IsolationUnavailableError, match="process containment"):
        run_isolated_json(
            lambda: marker.write_text("unsafe", encoding="utf-8"),
            timeout_seconds=1.0,
        )

    assert not marker.exists()


@_requires_supported_local_isolation
def test_child_fails_closed_when_inherited_descriptors_cannot_be_enumerated(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    source_fd = os.open(tmp_path / "inherited-high-fd.txt", os.O_CREAT | os.O_RDWR)
    high_fd = 4_097
    try:
        try:
            os.dup2(source_fd, high_fd, inheritable=True)
        except OSError:
            pytest.skip("process descriptor ceiling is below the regression fd")
        original_listdir = os.listdir

        def unavailable_fd_roots(path: os.PathLike[str] | str) -> list[str]:
            if os.fspath(path) in {"/proc/self/fd", "/dev/fd"}:
                raise OSError("fd directory unavailable")
            return original_listdir(path)

        marker = tmp_path / "work-executed.txt"
        monkeypatch.setattr(isolation_module.os, "listdir", unavailable_fd_roots)
        with pytest.raises(IsolationUnavailableError, match="process containment"):
            run_isolated_json(
                lambda: marker.write_text("unsafe", encoding="utf-8"),
                timeout_seconds=1.0,
            )
        assert not marker.exists()
    finally:
        os.close(source_fd)
        try:
            os.close(high_fd)
        except OSError:
            pass


def test_group_signal_denial_retains_isolated_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 424_242
    read_fd, write_fd = os.pipe()
    os.close(write_fd)
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILDREN", set())
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILD_REAPER", object())
    monkeypatch.setattr(isolation_module, "_child_state_without_reaping", lambda _pid: "exited")
    monkeypatch.setattr(
        isolation_module,
        "signal_owned_process_group",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )
    try:
        with pytest.raises(IsolatedExecutionError, match="signaling was denied"):
            isolation_module._collect_child(
                pid,
                read_fd,
                timeout_seconds=1.0,
                max_output_bytes=1_024,
            )
    finally:
        os.close(read_fd)
    assert isolation_module._STRANDED_CHILDREN == {pid}


def test_darwin_group_signal_tolerates_delayed_zombie_visibility(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    exited = SimpleNamespace(si_pid=424_246)
    observations = iter((None, exited))
    monkeypatch.setattr(process_group_module.sys, "platform", "darwin")
    monkeypatch.setattr(
        process_group_module.os,
        "killpg",
        lambda _pid, _signum: (_ for _ in ()).throw(PermissionError("transitioning")),
    )
    _install_fake_waitid_constants(monkeypatch)
    monkeypatch.setattr(
        process_group_module.os,
        "waitid",
        lambda *_args: next(observations),
        raising=False,
    )

    assert process_group_module.signal_owned_process_group(424_246, signal.SIGTERM) is False


def test_darwin_group_signal_persistent_denial_still_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(process_group_module.sys, "platform", "darwin")
    monkeypatch.setattr(process_group_module, "_DARWIN_EXIT_OBSERVATION_GRACE_SECONDS", 0.0)
    monkeypatch.setattr(
        process_group_module.os,
        "killpg",
        lambda _pid, _signum: (_ for _ in ()).throw(PermissionError("denied")),
    )
    _install_fake_waitid_constants(monkeypatch)
    monkeypatch.setattr(process_group_module.os, "waitid", lambda *_args: None, raising=False)

    with pytest.raises(PermissionError, match="denied"):
        process_group_module.signal_owned_process_group(424_247, signal.SIGTERM)


def test_termination_signal_denial_retains_isolated_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 424_243
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILDREN", set())
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILD_REAPER", object())
    monkeypatch.setattr(isolation_module, "_child_state_without_reaping", lambda _pid: "running")
    monkeypatch.setattr(
        isolation_module,
        "signal_owned_process_group",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )
    with pytest.raises(IsolatedExecutionError, match="signaling was denied"):
        isolation_module._terminate_process_tree(pid)
    assert isolation_module._STRANDED_CHILDREN == {pid}


def test_direct_term_signal_denial_retains_isolated_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 424_244
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILDREN", set())
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILD_REAPER", object())
    monkeypatch.setattr(isolation_module, "_child_state_without_reaping", lambda _pid: "running")
    monkeypatch.setattr(isolation_module, "_signal_process_group", lambda _pid, _signal: False)
    monkeypatch.setattr(
        isolation_module.os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(IsolatedExecutionError, match="process signaling was denied"):
        isolation_module._terminate_process_tree(pid)
    assert isolation_module._STRANDED_CHILDREN == {pid}


def test_direct_kill_signal_denial_retains_isolated_leader(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = 424_245
    clock = iter((0.0, 1.0))
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILDREN", set())
    monkeypatch.setattr(isolation_module, "_STRANDED_CHILD_REAPER", object())
    monkeypatch.setattr(isolation_module, "_child_state_without_reaping", lambda _pid: "running")
    monkeypatch.setattr(isolation_module.time, "monotonic", lambda: next(clock))
    monkeypatch.setattr(
        isolation_module,
        "_signal_process_group",
        lambda _pid, signum: signum == signal.SIGTERM,
    )
    monkeypatch.setattr(
        isolation_module.os,
        "kill",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )

    with pytest.raises(IsolatedExecutionError, match="process signaling was denied"):
        isolation_module._terminate_process_tree(pid)
    assert isolation_module._STRANDED_CHILDREN == {pid}
