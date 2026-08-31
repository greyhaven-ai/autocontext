from __future__ import annotations

import multiprocessing
import os
import queue
import signal
import struct
import threading
import time
from pathlib import Path
from typing import Any

import pytest

from autocontext.config import AppSettings
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.loop.controller import LoopController
from autocontext.server import _run_process_ipc as run_process_ipc_module
from autocontext.server import _run_process_monitor as run_process_monitor_module
from autocontext.server import run_manager as run_manager_module
from autocontext.server.run_manager import RunManager
from autocontext.storage import SQLiteStore

_requires_run_process_ownership = pytest.mark.skipif(
    os.name == "posix" and not run_manager_module._run_process_ownership_primitives_available(),
    reason="requires non-reaping child-process ownership primitives",
)


def _install_fake_run_process_ownership_primitives(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(run_manager_module.os, "waitid", lambda *_args: None, raising=False)
    for name, value in (("P_PID", 1), ("WNOWAIT", 2), ("WEXITED", 4), ("WNOHANG", 8)):
        monkeypatch.setattr(run_manager_module.os, name, value, raising=False)


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        monitor_enabled=False,
    )


def _kill_recorded_process(pid_path: Path) -> None:
    try:
        pid = int(pid_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return
    try:
        os.kill(pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _spawn_result_holding_descendant(
    control_connection: Any,
    event_connection: Any,
    heartbeat_path: str,
    pid_path: str,
) -> None:
    os.setsid()
    heartbeat = Path(heartbeat_path)
    descendant_pid = os.fork()
    if descendant_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        counter = 0
        while True:
            heartbeat.write_text(str(counter), encoding="utf-8")
            counter += 1
            time.sleep(0.01)
    Path(pid_path).write_text(str(descendant_pid), encoding="utf-8")
    deadline = time.monotonic() + 2.0
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "descendant-cleanup-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    control_connection.close()
    event_connection.close()


def _spawn_result_holding_detached_descendant(
    control_connection: Any,
    event_connection: Any,
    heartbeat_path: str,
    pid_path: str,
) -> None:
    os.setsid()
    heartbeat = Path(heartbeat_path)
    descendant_pid = os.fork()
    if descendant_pid == 0:
        # Escape the leader's session/process group while deliberately retaining
        # both inherited Pipe handles, so neither channel can reach EOF.
        os.setsid()
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        counter = 0
        while True:
            heartbeat.write_text(str(counter), encoding="utf-8")
            counter += 1
            time.sleep(0.01)
    Path(pid_path).write_text(str(descendant_pid), encoding="utf-8")
    deadline = time.monotonic() + 2.0
    while not heartbeat.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "detached-descendant-cleanup-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    control_connection.close()
    event_connection.close()


def _spawn_buffered_events(
    control_connection: Any,
    event_connection: Any,
    event_count: int,
) -> None:
    os.setsid()
    for index in range(event_count):
        run_manager_module._send_json_message(
            event_connection,
            {
                "type": "event",
                "event": "buffered_event",
                "payload": {"index": index},
                "channel": "generation",
            },
        )
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "buffered-events-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    control_connection.close()
    event_connection.close()


def _spawn_one_event_and_result(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "event",
            "event": "blocking_event",
            "payload": {},
            "channel": "generation",
        },
    )
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "blocked-subscriber-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    control_connection.close()
    event_connection.close()


def _spawn_partial_event_frame_with_holder(
    control_connection: Any,
    event_connection: Any,
    holder_pid_path: str,
) -> None:
    os.setsid()
    holder_pid = os.fork()
    if holder_pid == 0:
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
        while True:
            time.sleep(1.0)
    Path(holder_pid_path).write_text(str(holder_pid), encoding="utf-8")
    os.write(event_connection.fileno(), struct.pack("!i", 100) + b"{")
    control_connection.close()
    event_connection.close()


def _spawn_control_request_waiter(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    proxy = run_manager_module._ProcessLoopController(control_connection)
    proxy.take_hint()
    event_connection.close()


def _spawn_abrupt_exit(
    control_connection: Any,
    event_connection: Any,
) -> None:
    del control_connection, event_connection
    os.setsid()
    os._exit(7)


def _spawn_process_group_and_exit() -> None:
    if os.name == "posix":
        os.setsid()


def _spawn_success_result_then_abnormal_exit(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "abnormal-success-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    del control_connection, event_connection
    os._exit(7)


def _spawn_event_flood_with_control_request(
    control_connection: Any,
    event_connection: Any,
    control_completed_path: str,
) -> None:
    os.setsid()
    emitter = run_manager_module._ProcessEventEmitter(event_connection)
    controller = run_manager_module._ProcessLoopController(control_connection)
    stop_flood = threading.Event()

    def flood() -> None:
        index = 0
        while not stop_flood.is_set() and index < 2:
            emitter.emit("flood_event", {"index": index})
            index += 1

    flood_thread = threading.Thread(target=flood, daemon=True)
    flood_thread.start()
    controller.stop_requested()
    Path(control_completed_path).write_text("done", encoding="utf-8")
    stop_flood.set()
    flood_thread.join(timeout=2.0)
    emitter.send_result(run_id="fair-control-run", best_score=1.0)
    control_connection.close()
    emitter.close()


def _spawn_queued_control_then_exit(
    control_connection: Any,
    event_connection: Any,
    request_count: int,
) -> None:
    os.setsid()
    for _index in range(request_count):
        run_manager_module._send_json_message(
            control_connection,
            {"type": "control", "operation": "poll_chat", "args": []},
        )
    control_connection.close()
    event_connection.close()


def _spawn_terminal_then_event_messages(
    control_connection: Any,
    event_connection: Any,
    trailing_message: dict[str, Any],
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "terminal-sequence-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    run_manager_module._send_json_message(event_connection, trailing_message)
    control_connection.close()
    event_connection.close()


def _spawn_terminal_then_control_request(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "terminal-control-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    run_manager_module._send_json_message(
        control_connection,
        {"type": "control", "operation": "take_hint", "args": []},
    )
    time.sleep(1.0)


def _spawn_hidden_terminal_then_control_request(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    for index in range(65):
        run_manager_module._send_json_message(
            event_connection,
            {
                "type": "event",
                "event": "prefix_event",
                "payload": {"index": index},
                "channel": "generation",
            },
        )
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "hidden-terminal-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    run_manager_module._send_json_message(
        control_connection,
        {
            "type": "control",
            "operation": "take_hint",
            "args": [],
            "token": None,
        },
    )
    time.sleep(1.0)


def _spawn_control_with_omitted_token(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        control_connection,
        {"type": "control", "operation": "is_paused", "args": []},
    )
    event_connection.close()
    time.sleep(1.0)


def _spawn_control_with_stale_second_token(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    request = {
        "type": "control",
        "operation": "is_paused",
        "args": [],
        "token": None,
    }
    run_manager_module._send_json_message(control_connection, request)
    response = run_manager_module._receive_json_message(control_connection)
    assert isinstance(response.get("next_token"), str)
    run_manager_module._send_json_message(control_connection, request)
    event_connection.close()
    time.sleep(1.0)


def _spawn_result_with_unexpected_run_id(
    control_connection: Any,
    event_connection: Any,
) -> None:
    os.setsid()
    run_manager_module._send_json_message(
        event_connection,
        {
            "type": "result",
            "run_id": "different-run",
            "ok": True,
            "best_score": 1.0,
        },
    )
    control_connection.close()
    event_connection.close()


@_requires_run_process_ownership
def test_start_run_clears_a_leaked_stop_from_a_prior_run(tmp_path: Path) -> None:
    # The controller is reused across runs. A stop that terminated an earlier run
    # leaves _stop_requested set; without a reset, the next run would die at its
    # first boundary. start_run must clear it so a fresh run completes normally.
    settings = _make_settings(tmp_path)
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event, payload: recorded.append((event, payload)))

    # Simulate a stop that terminated a previous run and was never cleared.
    controller.request_stop("stale-command", "prior run")
    assert controller.stop_requested()

    manager = RunManager(controller, events, settings)
    run_id = manager.start_run("grid_ctf", generations=1, client_run_id="fresh-run")
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)
    assert manager._thread is not None and not manager._thread.is_alive()

    store = SQLiteStore(settings.db_path)
    run = store.get_run(run_id)
    assert run is not None
    # The fresh run must complete, not inherit the stale stop.
    assert run["status"] == "completed"
    # No stop receipt should have fired for the fresh run.
    assert not any(event == "run_stopped" for event, _ in recorded)
    # And the controller's stop flag was reset at start.
    assert not controller.stop_requested()


@_requires_run_process_ownership
def test_start_run_aborts_stale_pre_run_chat_and_still_starts(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    submit_finished = threading.Event()
    failures: list[str] = []

    def submit_before_run() -> None:
        try:
            controller.submit_chat("user", "stale")
        except RuntimeError as exc:
            failures.append(str(exc))
        finally:
            submit_finished.set()

    submitter = threading.Thread(target=submit_before_run, daemon=True)
    submitter.start()
    deadline = time.monotonic() + 1.0
    while controller.pending_chat_count() != 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    try:
        run_id = manager.start_run("grid_ctf", generations=1, run_id="chat-session-run")
    finally:
        if not submit_finished.is_set():
            controller.abort_pending_chats("previous interactive chat session ended")
        submitter.join(timeout=1.0)

    assert submit_finished.is_set()
    assert not submitter.is_alive()
    assert failures == ["previous interactive chat session ended"]
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)
    assert not manager._thread.is_alive()
    run = SQLiteStore(settings.db_path).get_run(run_id)
    assert run is not None and run["status"] == "completed"


@_requires_run_process_ownership
def test_start_run_without_client_id_is_addressable_by_run_id(tmp_path: Path) -> None:
    # StartRunCmd may omit client_run_id, but StopCmd always requires one. The
    # run must still be stoppable via the server run id returned in run_accepted.
    settings = _make_settings(tmp_path)
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    manager = RunManager(controller, events, settings)

    # Park the run at its first boundary so it stays active while we address it.
    controller.pause()
    run_id = manager.start_run("grid_ctf", generations=1)  # no client_run_id
    try:
        # The active scope falls back to the returned run id.
        assert manager._active_client_run_id == run_id
        # A stop scoped to that run id is accepted (not scope_mismatch).
        assert manager.stop_run(run_id, "cmd-1", None) == "accepted"
    finally:
        controller.resume()
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)
    assert not manager._thread.is_alive()

    store = SQLiteStore(settings.db_path)
    run = store.get_run(run_id)
    assert run is not None and run["status"] == "stopped"


@_requires_run_process_ownership
def test_rlm_run_uses_spawned_main_thread_isolation_and_relays_events(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path).model_copy(
        update={
            "rlm_enabled": True,
            "rlm_max_turns": 5,
        }
    )
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event, payload: recorded.append((event, payload)))
    manager = RunManager(controller, events, settings)

    run_id = manager.start_run("grid_ctf", generations=1, run_id="spawned-rlm-run")
    assert manager._process is not None
    assert manager._process.pid != os.getpid()
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)

    assert not manager._thread.is_alive()
    store = SQLiteStore(settings.db_path)
    run = store.get_run(run_id)
    assert run is not None and run["status"] == "completed"
    with store.connection() as connection:
        metrics = connection.execute(
            "SELECT role, status FROM agent_role_metrics WHERE run_id = ? ORDER BY role",
            (run_id,),
        ).fetchall()
    assert metrics
    assert {row["role"] for row in metrics}.issuperset({"analyst", "architect"})
    assert all(row["status"] in {"completed", "truncated"} for row in metrics)
    assert any(event == "generation_started" for event, _payload in recorded)


@_requires_run_process_ownership
def test_spawned_run_receives_and_reports_minimum_generation_floor(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    events = EventStreamEmitter(settings.event_stream_path)
    recorded: list[tuple[str, dict[str, Any]]] = []
    events.subscribe(lambda event, payload: recorded.append((event, payload)))
    manager = RunManager(LoopController(), events, settings)

    run_id = manager.start_run(
        "grid_ctf",
        generations=2,
        minimum_generations=2,
        run_id="spawned-minimum-floor-run",
    )
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)

    assert not manager._thread.is_alive()
    store = SQLiteStore(settings.db_path)
    run = store.get_run(run_id)
    assert run is not None and run["status"] == "completed"
    assert run["minimum_generations"] == 2
    assert store.count_completed_generations(run_id) == 2
    assert any(event == "run_started" and payload.get("minimum_generations") == 2 for event, payload in recorded)


def test_start_run_rejects_nondefault_sigchld_without_spawning(tmp_path: Path) -> None:
    if os.name != "posix":
        pytest.skip("SIGCHLD admission is POSIX-specific")
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    previous_handler = signal.getsignal(signal.SIGCHLD)
    signal.signal(signal.SIGCHLD, signal.SIG_IGN)
    try:
        with pytest.raises(RuntimeError, match="default SIGCHLD"):
            manager.start_run("grid_ctf", generations=1, run_id="unsafe-sigchld")
    finally:
        signal.signal(signal.SIGCHLD, previous_handler)

    assert not manager.is_active
    assert manager._process is None


def test_start_run_rejects_a_minimum_above_the_maximum(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )

    with pytest.raises(
        ValueError,
        match="minimum_generations must not exceed generations",
    ):
        manager.start_run(
            "grid_ctf",
            generations=2,
            minimum_generations=3,
        )


def test_start_run_rejects_missing_child_ownership_primitives_without_spawning(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("waitid admission is POSIX-specific")
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    context_requested = False

    def record_context(_method: str) -> object:
        nonlocal context_requested
        context_requested = True
        raise AssertionError("spawn context must not be requested")

    monkeypatch.setattr(
        run_manager_module,
        "_run_process_ownership_primitives_available",
        lambda: False,
    )
    monkeypatch.setattr(multiprocessing, "get_context", record_context)

    with pytest.raises(RuntimeError, match="child ownership primitives"):
        manager.start_run("grid_ctf", generations=1, run_id="unsafe-waitid")

    assert not context_requested
    assert not manager.is_active


@pytest.mark.parametrize(
    "primitive",
    ["waitid", "P_PID", "WNOWAIT", "WEXITED", "WNOHANG"],
)
def test_run_process_ownership_admission_requires_every_wait_primitive(
    primitive: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    if os.name != "posix":
        pytest.skip("waitid admission is POSIX-specific")
    _install_fake_run_process_ownership_primitives(monkeypatch)
    assert run_manager_module._run_process_ownership_primitives_available()
    monkeypatch.setattr(run_manager_module.os, primitive, None, raising=False)

    assert not run_manager_module._run_process_ownership_primitives_available()


def test_run_process_ownership_primitives_are_rechecked_adjacent_to_start(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    checks = iter((True, False))

    class FakeConnection:
        def close(self) -> None:
            pass

    class FakeProcess:
        pid = None
        start_called = False

        def start(self) -> None:
            self.start_called = True

        def close(self) -> None:
            pass

    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex: bool) -> tuple[FakeConnection, FakeConnection]:
            del duplex
            return FakeConnection(), FakeConnection()

        @staticmethod
        def Process(**_kwargs: object) -> FakeProcess:
            return process

    monkeypatch.setattr(
        run_manager_module,
        "_run_process_ownership_primitives_available",
        lambda: next(checks),
    )
    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: FakeContext())

    with pytest.raises(RuntimeError, match="changed before interactive run startup"):
        manager.start_run("grid_ctf", generations=1, run_id="ownership-recheck")

    assert not process.start_called
    assert not manager.is_active


def test_control_plane_secrets_are_consumed_before_spawn_bootstrap(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    monkeypatch.setenv("AUTOCONTEXT_SERVER_TOKEN", "must-not-reach-spawn")
    monkeypatch.setenv(
        "AutoContext_Server_Credentials_File",
        "/must/not/reach/spawn.json",
    )

    class FakeConnection:
        def close(self) -> None:
            pass

    class FakeProcess:
        pid = None

        def close(self) -> None:
            pass

    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex: bool) -> tuple[FakeConnection, FakeConnection]:
            del duplex
            return FakeConnection(), FakeConnection()

        @staticmethod
        def Process(**_kwargs: object) -> FakeProcess:
            return process

    def assert_sanitized_before_start(_process: object) -> None:
        assert all(
            key.upper()
            not in {
                "AUTOCONTEXT_SERVER_AUTH_KEYS",
                "AUTOCONTEXT_SERVER_CREDENTIALS_FILE",
                "AUTOCONTEXT_SERVER_TOKEN",
                "AUTOCONTEXT_SERVER_TOKEN_FILE",
            }
            for key in os.environ
        )
        raise RuntimeError("sentinel after sanitized spawn boundary")

    monkeypatch.setattr(
        run_manager_module,
        "_run_process_ownership_primitives_available",
        lambda: True,
    )
    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: FakeContext())
    monkeypatch.setattr(run_manager_module, "_start_owned_run_process", assert_sanitized_before_start)

    with pytest.raises(RuntimeError, match="sentinel after sanitized spawn boundary"):
        manager.start_run("grid_ctf", generations=1, run_id="sanitized-spawn")

    assert not manager.is_active


@_requires_run_process_ownership
def test_unrelated_process_start_cannot_reap_owned_run_leader() -> None:
    if os.name != "posix":
        pytest.skip("non-reaping child ownership is POSIX-specific")
    context = multiprocessing.get_context("spawn")
    runner = context.Process(target=_spawn_process_group_and_exit)
    unrelated = context.Process(target=time.sleep, args=(0.01,))
    run_manager_module._start_owned_run_process(runner)
    try:
        assert run_manager_module.wait([runner.sentinel], timeout=5.0)
        assert run_manager_module._run_process_state_without_reaping(runner) == "exited"

        # BaseProcess.start() invokes multiprocessing's process-global cleanup.
        # The runner must remain our waitable child across that unrelated call.
        unrelated.start()
        unrelated.join(timeout=5.0)
        assert unrelated.exitcode == 0
        assert run_manager_module._run_process_state_without_reaping(runner) == "exited"
        assert run_manager_module._terminate_run_process(runner)
    finally:
        if unrelated.pid is not None:
            unrelated.join(timeout=1.0)
            if unrelated.is_alive():
                unrelated.kill()
                unrelated.join(timeout=1.0)
        unrelated.close()
        runner.join(timeout=1.0)
        if runner.is_alive():
            runner.kill()
            runner.join(timeout=1.0)
        runner.close()


@_requires_run_process_ownership
def test_monitor_thread_start_failure_reaps_spawned_process(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    cleanup_results: list[bool] = []
    cleaned_processes: list[Any] = []
    repaired_runs: list[str] = []
    terminate_process = run_manager_module._terminate_run_process

    def record_cleanup(process: Any) -> bool:
        result = terminate_process(process)
        cleaned_processes.append(process)
        cleanup_results.append(result)
        return result

    def fail_to_start_monitor(_thread: threading.Thread) -> None:
        raise RuntimeError("monitor thread start failed")

    monkeypatch.setattr(run_manager_module, "_terminate_run_process", record_cleanup)
    monkeypatch.setattr(
        manager,
        "_repair_interrupted_run_state",
        lambda run_id, _run_session: repaired_runs.append(run_id),
    )
    monkeypatch.setattr(threading.Thread, "start", fail_to_start_monitor)

    with pytest.raises(RuntimeError, match="monitor thread start failed"):
        manager.start_run("grid_ctf", generations=1, run_id="monitor-start-failure")

    assert cleanup_results == [True]
    assert not manager.is_active
    assert manager._process is None
    assert manager._thread is None
    assert repaired_runs == ["monitor-start-failure"]
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        assert all(process not in run_manager_module._ACTIVE_RUN_PROCESSES for process in cleaned_processes)


def test_monitor_start_failure_never_releases_lock_with_commands_open(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(_make_settings(tmp_path).event_stream_path),
        _make_settings(tmp_path),
    )
    monkeypatch.setattr(run_manager_module, "_run_process_ownership_primitives_available", lambda: True)
    start_entered = threading.Event()
    observed: list[bool] = []

    def observe_publication() -> None:
        assert start_entered.wait(timeout=1.0)
        with manager._lock:
            observed.append(manager._commands_open)

    observer = threading.Thread(target=observe_publication)
    observer.start()

    class FakeConnection:
        def close(self) -> None:
            pass

    class FakeProcess:
        pid = 123

        def start(self) -> None:
            pass

        def close(self) -> None:
            pass

    process = FakeProcess()

    class FakeContext:
        @staticmethod
        def Pipe(*, duplex: bool) -> tuple[FakeConnection, FakeConnection]:
            del duplex
            return FakeConnection(), FakeConnection()

        @staticmethod
        def Process(**_kwargs: object) -> FakeProcess:
            return process

    class FailingMonitorThread:
        def __init__(self, **_kwargs: object) -> None:
            pass

        def start(self) -> None:
            start_entered.set()
            raise RuntimeError("monitor unavailable")

    monkeypatch.setattr(multiprocessing, "get_context", lambda _method: FakeContext())
    monkeypatch.setattr(threading, "Thread", FailingMonitorThread)
    monkeypatch.setattr(run_manager_module, "_terminate_run_process", lambda _process: True)

    with pytest.raises(RuntimeError, match="monitor unavailable"):
        manager.start_run("grid_ctf", generations=1, run_id="locked-start-failure")

    observer.join(timeout=1.0)
    assert observed == [False]
    assert not manager.is_active


@pytest.mark.parametrize(
    "failure_stage",
    ["get_context", "first_pipe", "second_pipe", "process", "process_start"],
)
def test_start_run_allocation_failure_closes_partial_ipc_and_resets_state(
    failure_stage: str,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    monkeypatch.setattr(run_manager_module, "_run_process_ownership_primitives_available", lambda: True)
    connections: list[Any] = []
    repaired_runs: list[str] = []
    monkeypatch.setattr(
        manager,
        "_repair_interrupted_run_state",
        lambda run_id, _run_session: repaired_runs.append(run_id),
    )

    class FakeConnection:
        def __init__(self) -> None:
            self.closed = False
            connections.append(self)

        def close(self) -> None:
            self.closed = True

    class FakeContext:
        def __init__(self) -> None:
            self.pipe_calls = 0

        def Pipe(self, *, duplex: bool) -> tuple[Any, Any]:
            del duplex
            self.pipe_calls += 1
            if failure_stage == "first_pipe" and self.pipe_calls == 1:
                raise OSError("first pipe failed")
            if failure_stage == "second_pipe" and self.pipe_calls == 2:
                raise OSError("second pipe failed")
            return FakeConnection(), FakeConnection()

        def Process(self, **_kwargs: Any) -> Any:
            if failure_stage == "process":
                raise OSError("process allocation failed")
            if failure_stage == "process_start":
                return FakeStartFailureProcess()
            raise AssertionError("process creation should be the requested failure")

    class FakeStartFailureProcess:
        pid = None

        def start(self) -> None:
            raise OSError("process start failed")

        def terminate(self) -> None:
            pass

        def kill(self) -> None:
            pass

        def close(self) -> None:
            pass

    if failure_stage == "get_context":
        monkeypatch.setattr(
            multiprocessing,
            "get_context",
            lambda _method: (_ for _ in ()).throw(OSError("context failed")),
        )
    else:
        monkeypatch.setattr(multiprocessing, "get_context", lambda _method: FakeContext())

    with pytest.raises(OSError):
        manager.start_run("grid_ctf", generations=1, run_id=f"failure-{failure_stage}")

    assert not manager.is_active
    assert manager._process is None
    assert manager._thread is None
    assert all(connection.closed for connection in connections)
    assert repaired_runs == []


def test_unreaped_process_keeps_manager_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    monkeypatch.setattr(run_manager_module, "_run_process_ownership_primitives_available", lambda: True)

    class FakeProcess:
        pass

    class FakeReaperThread:
        def __init__(self, **_kwargs: object) -> None:
            self.started = False

        def start(self) -> None:
            self.started = True

    process = FakeProcess()
    manager._active = True
    manager._active_client_run_id = "unreaped-run"
    manager._process = process
    monkeypatch.setattr(threading, "Thread", FakeReaperThread)

    manager._retain_unreaped_process(
        process,
        "unreaped-run",
        run_session=manager._run_session,
    )

    assert manager.is_active
    assert manager._process is process
    assert manager._cleanup_thread is not None
    assert manager._cleanup_thread.started
    with pytest.raises(RuntimeError, match="already active"):
        manager.start_run("grid_ctf", generations=1)

    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.discard(process)


def test_cleanup_retry_repairs_durable_state_only_after_reap(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    process = object()
    outcomes = iter((False, True))
    calls: list[str] = []

    def terminate(_process: Any) -> bool:
        calls.append("terminate")
        return next(outcomes)

    monkeypatch.setattr(run_manager_module, "_REAPER_RETRY_DELAYS", (0.0, 0.0))
    monkeypatch.setattr(run_manager_module, "_terminate_run_process", terminate)
    monkeypatch.setattr(
        manager,
        "_repair_interrupted_run_state",
        lambda _run_id, _run_session: calls.append("repair"),
    )
    monkeypatch.setattr(
        manager,
        "_finalize_reaped_process",
        lambda _process, **_kwargs: calls.append("finalize"),
    )

    manager._retry_unreaped_process_cleanup(
        process,
        "retry-run",
        manager._run_session,
    )

    assert calls == ["terminate", "terminate", "repair", "finalize"]


def test_reaped_process_close_failure_still_clears_manager_state(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )

    class CloseFailureProcess:
        def close(self) -> None:
            raise OSError("close failed")

    process = CloseFailureProcess()
    manager._active = True
    manager._active_client_run_id = "close-failure-run"
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    manager._finalize_reaped_process(
        process,
        run_session=manager._run_session,
    )

    assert not manager.is_active
    assert manager._process is None
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        assert process not in run_manager_module._ACTIVE_RUN_PROCESSES


def test_stale_finalizer_cannot_clear_a_new_startup_reservation(tmp_path: Path) -> None:
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(_make_settings(tmp_path).event_stream_path),
        _make_settings(tmp_path),
    )

    class OldProcess:
        closed = False

        def close(self) -> None:
            self.closed = True

    old_process = OldProcess()
    manager._run_session = 2
    manager._active = True
    manager._active_run_id = "new-run"
    manager._active_client_run_id = "new-client-run"
    manager._process = None
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(old_process)

    manager._finalize_reaped_process(old_process, run_session=1)

    assert old_process.closed
    assert manager.is_active
    assert manager._active_run_id == "new-run"
    assert manager._active_client_run_id == "new-client-run"
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        assert old_process not in run_manager_module._ACTIVE_RUN_PROCESSES


def test_process_finalization_serializes_against_concurrent_cleanup(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    close_entered = threading.Event()
    allow_close = threading.Event()
    cleanup_done = threading.Event()
    signals: list[tuple[int, int]] = []

    class ClosingProcess:
        def __init__(self) -> None:
            self.closed = False

        @property
        def pid(self) -> int:
            if self.closed:
                raise ValueError("process object is closed")
            return 424_242

        def close(self) -> None:
            close_entered.set()
            assert allow_close.wait(timeout=1.0)
            self.closed = True

    process = ClosingProcess()
    manager._active = True
    manager._process = process
    monkeypatch.setattr(
        run_manager_module,
        "_signal_run_process_group",
        lambda pid, signum: signals.append((pid, signum)) or True,
    )

    finalizer = threading.Thread(
        target=manager._finalize_reaped_process,
        args=(process,),
        kwargs={"run_session": manager._run_session},
    )

    def cleanup() -> None:
        run_manager_module._terminate_run_process(process)
        cleanup_done.set()

    concurrent_cleanup = threading.Thread(target=cleanup)
    finalizer.start()
    assert close_entered.wait(timeout=1.0)
    concurrent_cleanup.start()
    assert not cleanup_done.wait(timeout=0.05)
    allow_close.set()
    finalizer.join(timeout=1.0)
    concurrent_cleanup.join(timeout=1.0)

    assert cleanup_done.is_set()
    assert signals == []


def test_lost_run_process_ownership_never_signals_reused_pid_or_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[str] = []

    class OwnershipLostProcess:
        pid = 424_242

        def terminate(self) -> None:
            calls.append("terminate")

        def kill(self) -> None:
            calls.append("kill")

    process = OwnershipLostProcess()
    monkeypatch.setattr(
        run_manager_module,
        "_run_process_state_without_reaping",
        lambda _process: "ownership_lost",
    )
    monkeypatch.setattr(
        run_manager_module,
        "_signal_run_process_group",
        lambda _pid, _signum: calls.append("killpg") or True,
    )

    assert not run_manager_module._terminate_run_process(process)
    assert calls == []


def test_group_signal_denial_keeps_run_process_unreaped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class ExitedProcess:
        pid = 424_243

        @property
        def exitcode(self) -> int:
            raise AssertionError("denied group cleanup must not reap the leader")

    monkeypatch.setattr(
        run_manager_module,
        "_run_process_state_without_reaping",
        lambda _process: "exited",
    )
    monkeypatch.setattr(
        run_manager_module,
        "signal_owned_process_group",
        lambda _pid, _signal: (_ for _ in ()).throw(PermissionError("denied")),
    )
    assert not run_manager_module._terminate_run_process(ExitedProcess())


def test_windows_exited_runner_cleanup_does_not_require_sigkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class WindowsOS:
        name = "nt"

    class ExitedProcess:
        pid = 424_244
        exitcode_reads = 0

        @property
        def exitcode(self) -> int:
            self.exitcode_reads += 1
            return 0

    process = ExitedProcess()
    monkeypatch.setattr(run_manager_module, "os", WindowsOS())
    monkeypatch.delattr(run_manager_module.signal, "SIGKILL", raising=False)
    monkeypatch.setattr(
        run_manager_module,
        "_run_process_state_without_reaping",
        lambda _process: "exited",
    )
    monkeypatch.setattr(
        run_manager_module,
        "_signal_run_process_group",
        lambda _pid, _signum: (_ for _ in ()).throw(AssertionError("Windows has no process group signal")),
    )

    assert run_manager_module._observe_run_process_and_kill_exited_group(process) == "exited"
    assert run_manager_module._terminate_run_process(process)
    assert process.exitcode_reads == 1


@_requires_run_process_ownership
def test_monitor_reaps_result_holding_process_group_descendants(tmp_path: Path) -> None:
    if os.name != "posix":
        return
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    heartbeat = tmp_path / "run-descendant-heartbeat.txt"
    descendant_pid_path = tmp_path / "run-descendant-pid.txt"
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_result_holding_descendant,
        args=(child_control, child_events, str(heartbeat), str(descendant_pid_path)),
    )
    process.start()
    process_pid = process.pid
    child_control.close()
    child_events.close()

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "descendant-cleanup-run"),
        daemon=True,
    )
    monitor.start()
    try:
        monitor.join(timeout=5.0)
        assert not monitor.is_alive()
        assert heartbeat.exists()
        before = heartbeat.read_text(encoding="utf-8")
        time.sleep(0.1)
        after = heartbeat.read_text(encoding="utf-8")
        assert after == before
    finally:
        if monitor.is_alive() and process_pid is not None:
            try:
                os.killpg(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _kill_recorded_process(descendant_pid_path)
        run_manager_module._close_connection(parent_control)
        run_manager_module._close_connection(parent_events)
        monitor.join(timeout=2.0)


@_requires_run_process_ownership
def test_monitor_finishes_when_detached_descendant_retains_ipc_handles(tmp_path: Path) -> None:
    if os.name != "posix":
        return
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    heartbeat = tmp_path / "detached-descendant-heartbeat.txt"
    descendant_pid_path = tmp_path / "detached-descendant-pid.txt"
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_result_holding_detached_descendant,
        args=(child_control, child_events, str(heartbeat), str(descendant_pid_path)),
    )
    process.start()
    process_pid = process.pid
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(
            process,
            parent_control,
            parent_events,
            "detached-descendant-cleanup-run",
        ),
        daemon=True,
    )
    monitor.start()
    try:
        monitor.join(timeout=5.0)
        assert not monitor.is_alive()
        assert not manager.is_active
        assert manager._process is None
        assert heartbeat.exists()
    finally:
        if monitor.is_alive() and process_pid is not None:
            try:
                os.killpg(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _kill_recorded_process(descendant_pid_path)
        run_manager_module._close_connection(parent_control)
        run_manager_module._close_connection(parent_events)
        monitor.join(timeout=2.0)
        with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
            run_manager_module._ACTIVE_RUN_PROCESSES.discard(process)


@_requires_run_process_ownership
def test_monitor_post_exit_max_bounds_buffered_frames_with_slow_subscriber(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    settings = _make_settings(tmp_path)
    events = EventStreamEmitter(settings.event_stream_path)
    received: list[int] = []

    def slow_subscriber(event: str, payload: dict[str, Any]) -> None:
        if event == "buffered_event":
            time.sleep(0.08)
            received.append(payload["index"])

    events.subscribe(slow_subscriber)
    monkeypatch.setattr(run_manager_module, "_POST_EXIT_MAX_DRAIN_SECONDS", 0.5)
    manager = RunManager(LoopController(), events, settings)
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_buffered_events,
        args=(child_control, child_events, 70),
    )
    process.start()
    child_control.close()
    child_events.close()
    # The complete bounded batch fits in the pipe. Establish the exited-leader
    # precondition without reaping it so the monitor owns the absolute deadline.
    assert run_manager_module.wait([process.sentinel], timeout=5.0)
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "buffered-events-run"),
        daemon=True,
    )
    started = time.monotonic()
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert time.monotonic() - started < 2.0
    assert "interactive run exceeded its post-exit drain deadline" in caplog.text
    assert received == list(range(len(received)))
    assert len(received) < 70
    assert not manager.is_active


def test_event_relay_last_event_failure_is_not_reported_as_idle() -> None:
    def fail_emit(*_args: object, **_kwargs: object) -> None:
        raise OSError("event persistence failed")

    relay = run_process_monitor_module._EventRelay(fail_emit)
    relay.start()
    assert relay.submit(
        {
            "type": "event",
            "event": "only_event",
            "payload": {},
            "channel": "generation",
        }
    )
    relay.thread.join(timeout=1.0)

    assert not relay.thread.is_alive()
    assert not relay.is_idle
    with pytest.raises(
        run_manager_module._RunProcessProtocolError,
        match="event relay failed",
    ):
        relay.check_health()
    with pytest.raises(
        run_manager_module._RunProcessProtocolError,
        match="event relay failed",
    ):
        relay.stop(drop_pending=False)


@_requires_run_process_ownership
def test_monitor_waits_for_dequeued_final_event_and_surfaces_emit_failure(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    if os.name != "posix":
        pytest.skip("spawned process-group lifecycle test is POSIX-specific")
    event_dequeued = threading.Event()
    release_event = threading.Event()

    class PauseAfterGetQueue(queue.Queue[Any]):
        def get(self, block: bool = True, timeout: float | None = None) -> Any:
            message = super().get(block=block, timeout=timeout)
            if message is not run_process_monitor_module._RELAY_STOP:
                event_dequeued.set()
                assert release_event.wait(timeout=5.0)
            return message

    class PausingQueueModule:
        Queue = PauseAfterGetQueue
        Empty = queue.Empty
        Full = queue.Full

    settings = _make_settings(tmp_path)
    events = EventStreamEmitter(settings.event_stream_path)

    def fail_emit(*_args: object, **_kwargs: object) -> None:
        raise OSError("event persistence failed")

    monkeypatch.setattr(events, "emit", fail_emit)
    monkeypatch.setattr(run_process_monitor_module, "queue", PausingQueueModule)
    manager = RunManager(LoopController(), events, settings)
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_one_event_and_result,
        args=(child_control, child_events),
    )
    process.start()
    process_pid = process.pid
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "blocked-subscriber-run"),
        daemon=True,
    )
    monitor.start()
    try:
        assert event_dequeued.wait(timeout=2.0)
        assert run_manager_module.wait([process.sentinel], timeout=2.0)
        time.sleep(
            run_process_monitor_module._EVENT_RELAY_STOP_TIMEOUT_SECONDS + run_manager_module._POST_EXIT_IDLE_DRAIN_SECONDS + 0.1
        )
        assert monitor.is_alive()

        release_event.set()
        monitor.join(timeout=5.0)
        assert not monitor.is_alive()
        assert not manager.is_active
        assert "event relay failed" in caplog.text
    finally:
        release_event.set()
        monitor.join(timeout=2.0)
        if monitor.is_alive() and process_pid is not None:
            try:
                os.killpg(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            monitor.join(timeout=2.0)


@_requires_run_process_ownership
def test_blocked_event_subscriber_cannot_block_process_reaping(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    callback_entered = threading.Event()
    release_callback = threading.Event()
    events = EventStreamEmitter(settings.event_stream_path)

    def blocked_subscriber(_event: str, _payload: dict[str, Any]) -> None:
        callback_entered.set()
        release_callback.wait()

    events.subscribe(blocked_subscriber)
    monkeypatch.setattr(
        run_process_monitor_module,
        "_EVENT_CALLBACK_TIMEOUT_SECONDS",
        0.2,
    )
    manager = RunManager(LoopController(), events, settings)
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_one_event_and_result,
        args=(child_control, child_events),
    )
    process.start()
    process_pid = process.pid
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "blocked-subscriber-run"),
        daemon=True,
    )
    monitor.start()
    try:
        assert callback_entered.wait(timeout=2.0)
        monitor.join(timeout=2.0)

        assert not monitor.is_alive()
        assert process.exitcode is not None
        assert manager.is_active
        release_callback.set()
        assert manager._cleanup_thread is not None
        manager._cleanup_thread.join(timeout=2.0)
        assert not manager.is_active
    finally:
        release_callback.set()
        cleanup_thread = manager._cleanup_thread
        if cleanup_thread is not None:
            cleanup_thread.join(timeout=2.0)
        if monitor.is_alive() and process_pid is not None:
            try:
                os.killpg(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        run_manager_module._close_connection(parent_control)
        run_manager_module._close_connection(parent_events)
        monitor.join(timeout=2.0)
        with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
            run_manager_module._ACTIVE_RUN_PROCESSES.discard(process)


@_requires_run_process_ownership
def test_buffered_event_flood_does_not_starve_control_channel(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    events = EventStreamEmitter(settings.event_stream_path)
    events.subscribe(lambda _event, _payload: time.sleep(0.002))
    manager = RunManager(LoopController(), events, settings)
    control_completed = tmp_path / "control-completed.txt"
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_event_flood_with_control_request,
        args=(child_control, child_events, str(control_completed)),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "fair-control-run"),
        daemon=True,
    )
    monitor.start()
    deadline = time.monotonic() + 2.0
    while not control_completed.exists() and time.monotonic() < deadline:
        time.sleep(0.01)

    assert control_completed.exists()
    monitor.join(timeout=10.0)
    assert not monitor.is_alive()


@_requires_run_process_ownership
def test_post_exit_control_frames_do_not_mutate_reused_controller(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    commit_calls = 0
    original_commit_chat = controller.commit_chat

    def record_commit_chat(request: object) -> None:
        nonlocal commit_calls
        commit_calls += 1
        original_commit_chat(request)  # type: ignore[arg-type]

    monkeypatch.setattr(controller, "commit_chat", record_commit_chat)
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_queued_control_then_exit,
        args=(child_control, child_events, 64),
    )
    process.start()
    child_control.close()
    child_events.close()
    time.sleep(0.2)
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    started = time.monotonic()
    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "post-exit-control-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert time.monotonic() - started < 2.0
    assert commit_calls == 0


@pytest.mark.parametrize(
    "trailing_message",
    [
        {
            "type": "result",
            "run_id": "terminal-sequence-run",
            "ok": False,
            "error_type": "RuntimeError",
            "message": "duplicate",
        },
        {
            "type": "event",
            "event": "after_terminal",
            "payload": {},
            "channel": "generation",
        },
    ],
)
@_requires_run_process_ownership
def test_monitor_rejects_data_after_terminal_result(
    trailing_message: dict[str, Any],
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    recorded: list[str] = []
    events = EventStreamEmitter(settings.event_stream_path)
    events.subscribe(lambda event, _payload: recorded.append(event))
    manager = RunManager(LoopController(), events, settings)
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("terminal-sequence-run", "grid_ctf", 1, "local")
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_terminal_then_event_messages,
        args=(child_control, child_events, trailing_message),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "terminal-sequence-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert not manager.is_active
    assert "after_terminal" not in recorded
    run = store.get_run("terminal-sequence-run")
    assert run is not None and run["status"] == "failed"


@_requires_run_process_ownership
def test_terminal_result_dominates_simultaneously_ready_control_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    controller.inject_hint("must survive")
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    dispatched: list[dict[str, Any] | None] = []

    def record_dispatch(
        _connection: Any,
        request: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> bool:
        dispatched.append(request)
        return True

    monkeypatch.setattr(manager, "_dispatch_control_request", record_dispatch)
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_terminal_then_control_request,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    time.sleep(0.2)
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "terminal-control-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert dispatched == []


@pytest.mark.parametrize(
    ("target", "expected_calls"),
    [
        (_spawn_control_with_omitted_token, 0),
        (_spawn_control_with_stale_second_token, 1),
    ],
)
@_requires_run_process_ownership
def test_monitor_rejects_omitted_or_stale_controller_sequence_tokens(
    target: Any,
    expected_calls: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    is_paused_calls = 0
    original_is_paused = controller.is_paused

    def record_is_paused() -> bool:
        nonlocal is_paused_calls
        is_paused_calls += 1
        return original_is_paused()

    monkeypatch.setattr(controller, "is_paused", record_is_paused)
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(target=target, args=(child_control, child_events))
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "sequence-token-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert is_paused_calls == expected_calls
    assert not manager.is_active


@_requires_run_process_ownership
def test_event_backlog_cannot_hide_terminal_result_from_control_ordering(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    dispatched: list[dict[str, Any] | None] = []

    def record_dispatch(
        _connection: Any,
        request: dict[str, Any] | None = None,
        **_kwargs: Any,
    ) -> bool:
        dispatched.append(request)
        return True

    monkeypatch.setattr(manager, "_dispatch_control_request", record_dispatch)
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_hidden_terminal_then_control_request,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "hidden-terminal-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert dispatched == []
    assert not manager.is_active


@_requires_run_process_ownership
def test_event_relay_start_failure_still_reaps_runner_and_closes_ipc(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_control_request_waiter,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)
    monkeypatch.setattr(
        run_process_monitor_module._EventRelay,
        "start",
        lambda _self: (_ for _ in ()).throw(RuntimeError("thread unavailable")),
    )

    manager._monitor_run_process(
        process,
        parent_control,
        parent_events,
        "relay-start-failure-run",
    )

    assert not manager.is_active
    assert manager._process is None
    with pytest.raises((OSError, ValueError)):
        parent_control.fileno()
    with pytest.raises((OSError, ValueError)):
        parent_events.fileno()


@_requires_run_process_ownership
def test_monitor_rejects_terminal_result_for_another_run(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("expected-run", "grid_ctf", 1, "local")
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_result_with_unexpected_run_id,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "expected-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    run = store.get_run("expected-run")
    assert run is not None and run["status"] == "failed"


@_requires_run_process_ownership
def test_monitor_rejects_partial_frame_without_blocking_on_inherited_fd(
    tmp_path: Path,
) -> None:
    if os.name != "posix":
        pytest.skip("partial Connection stream framing is POSIX-specific")
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("partial-frame-run", "grid_ctf", 1, "local")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO generations(
                run_id, generation_index, mean_score, best_score, gate_decision, status
            ) VALUES ('partial-frame-run', 0, 0, 0, 'pending', 'running')
            """
        )
    context = multiprocessing.get_context("spawn")
    holder_pid_path = tmp_path / "partial-frame-holder-pid.txt"
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_partial_event_frame_with_holder,
        args=(child_control, child_events, str(holder_pid_path)),
    )
    process.start()
    process_pid = process.pid
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "partial-frame-run"),
        daemon=True,
    )
    monitor.start()
    try:
        monitor.join(timeout=5.0)

        run = store.get_run("partial-frame-run")
        generation = store.get_generation("partial-frame-run", 0)
        assert not monitor.is_alive()
        assert not manager.is_active
        assert run is not None and run["status"] == "failed"
        assert generation is not None and generation["status"] == "failed"
        assert generation["gate_decision"] == "stalled"
    finally:
        if monitor.is_alive() and process_pid is not None:
            try:
                os.killpg(process_pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        _kill_recorded_process(holder_pid_path)
        run_manager_module._close_connection(parent_control)
        run_manager_module._close_connection(parent_events)
        monitor.join(timeout=2.0)
        with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
            run_manager_module._ACTIVE_RUN_PROCESSES.discard(process)


@_requires_run_process_ownership
def test_abrupt_runner_exit_repairs_durable_running_state(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("abrupt-exit-run", "grid_ctf", 1, "local")
    with store.connection() as connection:
        connection.execute(
            """
            INSERT INTO generations(
                run_id, generation_index, mean_score, best_score, gate_decision, status
            ) VALUES ('abrupt-exit-run', 0, 0, 0, 'pending', 'running')
            """
        )

    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_abrupt_exit,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "abrupt-exit-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    run = store.get_run("abrupt-exit-run")
    generation = store.get_generation("abrupt-exit-run", 0)
    assert not monitor.is_alive()
    assert run is not None and run["status"] == "failed"
    assert generation is not None and generation["status"] == "failed"
    assert generation["gate_decision"] == "stalled"


@_requires_run_process_ownership
def test_success_result_requires_clean_runner_exit(tmp_path: Path) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("abnormal-success-run", "grid_ctf", 1, "local")
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_success_result_then_abnormal_exit,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "abnormal-success-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    run = store.get_run("abnormal-success-run")
    assert run is not None and run["status"] == "failed"


@pytest.mark.parametrize("terminal_status", ["stopped", "completed"])
def test_interrupted_state_repair_preserves_terminal_run_status(
    terminal_status: str,
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    manager = RunManager(
        LoopController(),
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    store = SQLiteStore(settings.db_path)
    store.migrate(manager._migrations_dir)
    store.create_run("terminal-run", "grid_ctf", 1, "local")
    if terminal_status == "stopped":
        assert store.mark_run_stopped("terminal-run")
    else:
        store.mark_run_completed("terminal-run")

    manager._repair_interrupted_run_state(
        "terminal-run",
        manager._run_session,
    )

    run = store.get_run("terminal-run")
    assert run is not None and run["status"] == terminal_status


@_requires_run_process_ownership
def test_oversized_control_response_is_protocol_fatal_instead_of_hanging(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    controller.inject_hint("x" * (run_manager_module._MAX_RUN_IPC_BYTES + 1))
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    context = multiprocessing.get_context("spawn")
    parent_control, child_control = context.Pipe(duplex=True)
    parent_events, child_events = context.Pipe(duplex=False)
    process = context.Process(
        target=_spawn_control_request_waiter,
        args=(child_control, child_events),
    )
    process.start()
    child_control.close()
    child_events.close()
    manager._active = True
    manager._process = process
    with run_manager_module._ACTIVE_RUN_PROCESSES_LOCK:
        run_manager_module._ACTIVE_RUN_PROCESSES.add(process)

    monitor = threading.Thread(
        target=manager._monitor_run_process,
        args=(process, parent_control, parent_events, "oversized-control-run"),
        daemon=True,
    )
    monitor.start()
    monitor.join(timeout=5.0)

    assert not monitor.is_alive()
    assert not manager.is_active


def test_failed_control_response_rolls_back_reserved_chat(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )
    submit_finished = threading.Event()

    def submit_chat() -> None:
        try:
            controller.submit_chat("user", "preserve me")
        except RuntimeError:
            pass
        finally:
            submit_finished.set()

    submitter = threading.Thread(target=submit_chat, daemon=True)
    submitter.start()
    deadline = time.monotonic() + 1.0
    while controller.pending_chat_count() != 1 and time.monotonic() < deadline:
        time.sleep(0.01)

    def fail_response(*_args: object, **_kwargs: object) -> None:
        raise BrokenPipeError("child exited")

    monkeypatch.setattr(
        run_manager_module,
        "_send_json_message_with_deadline",
        fail_response,
    )
    with pytest.raises(run_manager_module._RunProcessProtocolError):
        manager._dispatch_control_request(
            object(),  # type: ignore[arg-type]
            request={"type": "control", "operation": "poll_chat", "args": []},
        )

    assert controller.pending_chat_count() == 1
    assert not submit_finished.is_set()
    controller.abort_pending_chats("run ended")
    submitter.join(timeout=1.0)
    assert submit_finished.is_set()


@pytest.mark.parametrize(
    ("operation", "setter", "taker"),
    [
        ("take_hint", "inject_hint", "take_hint"),
        ("take_gate_override", "set_gate_override", "take_gate_override"),
    ],
)
def test_failed_control_response_rolls_back_one_shot_value(
    operation: str,
    setter: str,
    taker: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = _make_settings(tmp_path)
    controller = LoopController()
    getattr(controller, setter)("preserve me")
    manager = RunManager(
        controller,
        EventStreamEmitter(settings.event_stream_path),
        settings,
    )

    monkeypatch.setattr(
        run_manager_module,
        "_send_json_message_with_deadline",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(BrokenPipeError("child exited")),
    )
    with pytest.raises(run_manager_module._RunProcessProtocolError):
        manager._dispatch_control_request(
            object(),  # type: ignore[arg-type]
            request={"type": "control", "operation": operation, "args": []},
        )

    assert getattr(controller, taker)() == "preserve me"


@pytest.mark.parametrize("number", [b"NaN", b"Infinity", b"-Infinity", b"1e999"])
def test_ipc_json_decoder_rejects_nonfinite_numbers(number: bytes) -> None:
    with pytest.raises(ValueError, match="non-finite"):
        run_manager_module._decode_json_payload(b'{"value":' + number + b"}")


def test_windows_control_write_timeout_cancels_overlapped_operation() -> None:
    class FakeOverlapped:
        event = object()

        def __init__(self) -> None:
            self.cancelled = False

        def cancel(self) -> None:
            self.cancelled = True

        def GetOverlappedResult(self, wait: bool) -> tuple[int, int]:
            assert wait
            if self.cancelled:
                raise OSError("cancelled")
            return 0, 0

    overlapped = FakeOverlapped()

    class FakeWinApi:
        ERROR_IO_PENDING = 997
        WAIT_TIMEOUT = 258
        WAIT_OBJECT_0 = 0

        @staticmethod
        def WriteFile(handle: int, payload: bytes, *, overlapped: bool) -> tuple[Any, int]:
            assert handle == 123
            assert payload == b"payload"
            assert overlapped
            return globals_overlapped, FakeWinApi.ERROR_IO_PENDING

        @staticmethod
        def WaitForMultipleObjects(
            handles: list[Any],
            wait_all: bool,
            timeout_ms: int,
        ) -> int:
            assert handles == [globals_overlapped.event]
            assert not wait_all
            assert timeout_ms > 0
            return FakeWinApi.WAIT_TIMEOUT

    class FakeConnection:
        @staticmethod
        def fileno() -> int:
            return 123

    globals_overlapped = overlapped
    with pytest.raises(TimeoutError, match="timed out"):
        run_manager_module._send_windows_message_with_deadline(
            FakeConnection(),  # type: ignore[arg-type]
            b"payload",
            timeout_seconds=0.01,
            winapi_module=FakeWinApi,
        )
    assert overlapped.cancelled


def test_windows_control_write_unexpected_wait_result_is_cancelled_and_drained() -> None:
    class FakeOverlapped:
        event = object()

        def __init__(self) -> None:
            self.cancelled = False
            self.drained = False

        def cancel(self) -> None:
            self.cancelled = True

        def GetOverlappedResult(self, wait: bool) -> tuple[int, int]:
            assert wait
            self.drained = True
            raise OSError("cancelled")

    overlapped = FakeOverlapped()

    class FakeWinApi:
        ERROR_IO_PENDING = 997
        WAIT_TIMEOUT = 258
        WAIT_OBJECT_0 = 0
        WAIT_FAILED = 0xFFFFFFFF

        @staticmethod
        def WriteFile(handle: int, payload: bytes, *, overlapped: bool) -> tuple[Any, int]:
            assert handle == 123
            assert payload == b"payload"
            assert overlapped
            return globals_overlapped, FakeWinApi.ERROR_IO_PENDING

        @staticmethod
        def WaitForMultipleObjects(
            handles: list[Any],
            wait_all: bool,
            timeout_ms: int,
        ) -> int:
            assert handles == [globals_overlapped.event]
            assert not wait_all
            assert timeout_ms > 0
            return FakeWinApi.WAIT_FAILED

    class FakeConnection:
        @staticmethod
        def fileno() -> int:
            return 123

    globals_overlapped = overlapped
    with pytest.raises(OSError, match="wait failed"):
        run_manager_module._send_windows_message_with_deadline(
            FakeConnection(),  # type: ignore[arg-type]
            b"payload",
            timeout_seconds=0.01,
            winapi_module=FakeWinApi,
        )
    assert overlapped.cancelled
    assert overlapped.drained


def test_event_and_terminal_result_share_one_atomic_writer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    context = multiprocessing.get_context("spawn")
    receiver, sender = context.Pipe(duplex=False)
    emitter = run_manager_module._ProcessEventEmitter(sender)
    event_send_entered = threading.Event()
    allow_event_send = threading.Event()
    original_send = run_process_ipc_module._send_json_message
    received: list[dict[str, Any]] = []
    thread_failures: list[BaseException] = []

    def gated_send(connection: Any, message: dict[str, Any]) -> None:
        if message.get("type") == "event":
            event_send_entered.set()
            assert allow_event_send.wait(timeout=5.0)
        original_send(connection, message)

    def receive_messages() -> None:
        try:
            received.extend(
                [
                    run_manager_module._receive_json_message(receiver),
                    run_manager_module._receive_json_message(receiver),
                ]
            )
        except BaseException as exc:
            thread_failures.append(exc)

    def send_event() -> None:
        try:
            emitter.emit("large_event", {"data": "x" * 128_000})
        except BaseException as exc:
            thread_failures.append(exc)

    def send_result() -> None:
        try:
            emitter.send_result(run_id="atomic-writer-run", best_score=1.0)
        except BaseException as exc:
            thread_failures.append(exc)

    monkeypatch.setattr(run_process_ipc_module, "_send_json_message", gated_send)

    reader = threading.Thread(target=receive_messages, daemon=True)
    event_sender = threading.Thread(target=send_event, daemon=True)
    result_sender = threading.Thread(target=send_result, daemon=True)
    started_threads: list[threading.Thread] = []
    try:
        reader.start()
        started_threads.append(reader)
        event_sender.start()
        started_threads.append(event_sender)
        assert event_send_entered.wait(timeout=5.0)
        result_sender.start()
        started_threads.append(result_sender)
        allow_event_send.set()
        event_sender.join(timeout=5.0)
        result_sender.join(timeout=5.0)
        reader.join(timeout=5.0)

        assert not any(thread.is_alive() for thread in started_threads)
        assert thread_failures == []
        assert [message["type"] for message in received] == ["event", "result"]
    finally:
        allow_event_send.set()
        for thread in started_threads:
            if thread is not reader:
                thread.join(timeout=2.0)
        run_manager_module._close_connection(sender)
        if reader in started_threads:
            reader.join(timeout=2.0)
        run_manager_module._close_connection(receiver)
