from __future__ import annotations

import atexit
import json
import logging
import math
import multiprocessing
import os
import signal
import threading
import time
import uuid
from multiprocessing.connection import Connection, wait
from pathlib import Path
from typing import Any, Literal

from autocontext.config import AppSettings, load_settings
from autocontext.execution._process_group import (
    process_state_without_reaping,
    signal_owned_process_group,
)
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.scenarios import SCENARIO_REGISTRY
from autocontext.server._owned_process import start_owned_process as _start_owned_run_process
from autocontext.server._run_control_dispatch import dispatch_control_request
from autocontext.server._run_environment import build_run_environment_info
from autocontext.server._run_process_ipc import (
    _CONTROL_RESPONSE_TIMEOUT_SECONDS,
    _MAX_RUN_IPC_BYTES,
    _decode_json_payload,
    _IncrementalConnectionReader,
    _ProcessEventEmitter,
    _ProcessLoopController,
    _receive_json_message,
    _run_generation_process,
    _RunProcessProtocolError,
    _send_json_message,
    _send_json_message_with_deadline,
    _send_windows_message_with_deadline,
)
from autocontext.server._run_process_monitor import (
    _monitor_run_process as _monitor_run_process_loop,
)
from autocontext.server._run_state_repair import repair_interrupted_run_state
from autocontext.server.resource_limits import MAX_START_RUN_GENERATIONS
from autocontext.storage import SQLiteStore

logger = logging.getLogger(__name__)

__all__ = [
    "RunManager",
    "StopOutcome",
    "_CONTROL_RESPONSE_TIMEOUT_SECONDS",
    "_IncrementalConnectionReader",
    "_MAX_RUN_IPC_BYTES",
    "_ProcessEventEmitter",
    "_ProcessLoopController",
    "_RunProcessProtocolError",
    "_decode_json_payload",
    "_receive_json_message",
    "_run_generation_process",
    "_send_json_message",
    "_send_json_message_with_deadline",
    "_send_windows_message_with_deadline",
]

StopOutcome = Literal["accepted", "duplicate", "scope_mismatch", "not_active"]
RunControlOperation = Literal["pause", "resume", "inject_hint", "override_gate"]
_RunProcessState = Literal["running", "exited", "ownership_lost"]

_RESULT_EXIT_GRACE_SECONDS = 1.0
_POST_EXIT_IDLE_DRAIN_SECONDS = 0.25
_POST_EXIT_MAX_DRAIN_SECONDS = 5.0
_REAPER_RETRY_DELAYS = (0.1, 0.5, 1.0)
_ACTIVE_RUN_PROCESSES: set[Any] = set()
_ACTIVE_RUN_PROCESSES_LOCK = threading.Lock()
_RUN_PROCESS_CLEANUP_LOCK = threading.RLock()


class _RunProcessOwnershipLost(_RunProcessProtocolError):
    """The spawned runner leader was reaped outside this manager."""


def _signal_run_process_group(pid: int, signum: int) -> bool:
    if os.name != "posix":
        return False
    return signal_owned_process_group(pid, signum)


def _sigchld_disposition_is_safe() -> bool:
    """Reject dispositions that can auto-reap a spawned leader behind us."""
    if os.name != "posix":
        return True
    try:
        return signal.getsignal(signal.SIGCHLD) is signal.SIG_DFL
    except (AttributeError, OSError, ValueError):
        return False


def _run_process_ownership_primitives_available() -> bool:
    """Require non-reaping child observation before any POSIX group signal."""
    if os.name != "posix":
        return True
    return (
        getattr(os, "waitid", None) is not None
        and getattr(os, "WNOWAIT", None) is not None
        and getattr(os, "P_PID", None) is not None
        and getattr(os, "WEXITED", None) is not None
        and getattr(os, "WNOHANG", None) is not None
    )


def _run_process_state_without_reaping(process: Any) -> _RunProcessState:
    """Observe the leader while distinguishing externally lost ownership."""
    return process_state_without_reaping(process, wait)


def _observe_run_process_and_kill_exited_group(process: Any) -> _RunProcessState:
    """Best-effort group cleanup under the manager's cleanup lock."""
    with _RUN_PROCESS_CLEANUP_LOCK:
        process_state = _run_process_state_without_reaping(process)
        if process_state != "exited":
            return process_state
        try:
            pid = process.pid
        except ValueError:
            return "ownership_lost"
        if pid is None:
            return "ownership_lost"
        if os.name == "posix":
            _signal_run_process_group(pid, signal.SIGKILL)
        return "exited"


def _terminate_run_process(process: Any) -> bool:
    """Terminate/reap a runner group; return whether the leader was reaped."""
    with _RUN_PROCESS_CLEANUP_LOCK:
        try:
            return _terminate_run_process_locked(process)
        except Exception:
            logger.exception("interactive run process termination failed")
            return False


def _terminate_run_process_locked(process: Any) -> bool:
    """Implementation of termination while ``_RUN_PROCESS_CLEANUP_LOCK`` is held."""
    try:
        pid = process.pid
    except ValueError:
        # Another cleanup owner already reaped and closed this Process.
        return True
    if pid is None:
        return True
    process_state = _run_process_state_without_reaping(process)
    if process_state == "ownership_lost":
        return False
    if process_state == "running":
        group_signaled = _signal_run_process_group(pid, signal.SIGTERM)
        if not group_signaled and _run_process_state_without_reaping(process) == "running":
            try:
                process.terminate()
            except (AttributeError, ProcessLookupError, ValueError):
                pass
        deadline = time.monotonic() + 0.2
        while time.monotonic() < deadline:
            process_state = _run_process_state_without_reaping(process)
            if process_state != "running":
                break
            time.sleep(0.01)
        if process_state == "ownership_lost":
            return False
        if process_state == "running":
            # Recheck ownership immediately before numeric group/process signals.
            process_state = _run_process_state_without_reaping(process)
            if process_state != "running":
                if process_state == "ownership_lost":
                    return False
            else:
                group_signaled = os.name == "posix" and _signal_run_process_group(
                    pid,
                    signal.SIGKILL,
                )
                if not group_signaled and _run_process_state_without_reaping(process) == "running":
                    try:
                        process.kill()
                    except (AttributeError, ProcessLookupError, ValueError):
                        pass
            deadline = time.monotonic() + 1.0
            while time.monotonic() < deadline:
                process_state = _run_process_state_without_reaping(process)
                if process_state != "running":
                    break
                time.sleep(0.01)
    if process_state == "ownership_lost":
        return False
    if process_state == "running":
        return False

    # Kill group members that outlived an exited leader before polling exitcode
    # reaps it and permits its pid/process-group id to be reused.
    process_state = _run_process_state_without_reaping(process)
    if process_state != "exited":
        return False
    if os.name == "posix":
        _signal_run_process_group(pid, signal.SIGKILL)
    try:
        return process.exitcode is not None
    except ValueError:
        return True


def _shutdown_active_run_processes() -> None:
    with _ACTIVE_RUN_PROCESSES_LOCK:
        processes = list(_ACTIVE_RUN_PROCESSES)
    for process in processes:
        _terminate_run_process(process)


atexit.register(_shutdown_active_run_processes)


def _close_connection(connection: Connection) -> None:
    try:
        connection.close()
    except (OSError, ValueError):
        logger.debug("failed to close interactive run IPC connection", exc_info=True)


class RunManager:
    """Manages dynamic run creation for the interactive server."""

    def __init__(self, controller: LoopController, events: EventStreamEmitter, settings: AppSettings | None = None) -> None:
        self.controller = controller
        self.events = events
        self.settings = settings or load_settings()
        self._thread: threading.Thread | None = None
        self._cleanup_thread: threading.Thread | None = None
        self._process: Any | None = None
        self._active = False
        self._commands_open = False
        self._active_run_id: str | None = None
        self._active_client_run_id: str | None = None
        self._run_session = 0
        self._processed_stop_command_ids: set[str] = set()
        self._shutdown_requested = False
        # Serializes the run lifecycle transition (start / teardown) against
        # stop validation + controller mutation, so a stop for an old run cannot
        # validate, then land on the reused controller after a new run started.
        self._lock = threading.RLock()
        self._migrations_dir = Path(__file__).resolve().parents[2] / "migrations"

    @property
    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def _run_scope_outcome_locked(self, client_run_id: str | None) -> StopOutcome:
        if not self._active or not self._commands_open:
            return "not_active"
        if client_run_id is not None and client_run_id != self._active_client_run_id:
            return "scope_mismatch"
        return "accepted"

    def control_run(
        self,
        client_run_id: str | None,
        operation: RunControlOperation,
        value: str | None = None,
    ) -> StopOutcome:
        """Validate and apply one run-scoped interactive command atomically."""
        with self._lock:
            outcome = self._run_scope_outcome_locked(client_run_id)
            if outcome != "accepted":
                return outcome
            if operation == "pause":
                self.controller.pause()
            elif operation == "resume":
                self.controller.resume()
            elif operation == "inject_hint" and value is not None:
                self.controller.inject_hint(value)
            elif operation == "override_gate" and value is not None:
                self.controller.set_gate_override(value)
            else:
                raise ValueError("invalid interactive run control")
            return "accepted"

    def prepare_chat_run(
        self,
        client_run_id: str | None,
    ) -> tuple[StopOutcome, int | None]:
        """Validate a chat synchronously and bind later work to this run."""
        with self._lock:
            outcome = self._run_scope_outcome_locked(client_run_id)
            if outcome != "accepted":
                return outcome, None
            return "accepted", self._run_session

    def chat_run(
        self,
        run_session: int,
        role: str,
        message: str,
    ) -> tuple[StopOutcome, str | None]:
        """Admit and wait for chat only if its validated run is still active."""
        with self._lock:
            if not self._active or not self._commands_open:
                return "not_active", None
            if run_session != self._run_session:
                return "scope_mismatch", None
            request = self.controller.admit_chat(role, message)
        return "accepted", self.controller.wait_for_chat_response(request)

    def _close_run_commands(
        self,
        reason: str,
        *,
        process: Any | None = None,
        run_session: int | None = None,
    ) -> None:
        with self._lock:
            if process is not None and self._process is not process:
                return
            if run_session is not None and run_session != self._run_session:
                return
            self._commands_open = False
            self.controller.abort_pending_chats(reason)

    def stop_run(self, client_run_id: str | None, command_id: str | None, reason: str | None) -> StopOutcome:
        with self._lock:
            outcome = self._run_scope_outcome_locked(client_run_id)
            if outcome != "accepted":
                return outcome
            if command_id is not None and self._processed_stop_command_ids:
                return "duplicate"
            if command_id is not None:
                self._processed_stop_command_ids.add(command_id)
            self.controller.request_stop(command_id, reason)
            return "accepted"

    def shutdown(self, timeout_seconds: float = 5.0) -> None:
        """Boundedly stop and reap the active runner during application shutdown."""
        if not math.isfinite(timeout_seconds) or timeout_seconds < 0:
            raise ValueError("timeout_seconds must be a non-negative finite number")
        deadline = time.monotonic() + timeout_seconds
        with self._lock:
            self._shutdown_requested = True
            self._close_run_commands(
                "interactive server shut down before the chat request completed"
            )
            process = self._process
            monitor_thread = self._thread
            cleanup_thread = self._cleanup_thread
            active_run_id = self._active_run_id
            run_session = self._run_session

        if process is None:
            if cleanup_thread is not None and cleanup_thread is not threading.current_thread():
                cleanup_thread.join(timeout=max(0.0, deadline - time.monotonic()))
            return
        self.controller.request_stop(None, "interactive server shutdown")

        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=max(0.0, deadline - time.monotonic()))

        with _RUN_PROCESS_CLEANUP_LOCK:
            with self._lock:
                still_owned = self._process is process
            if still_owned:
                process_reaped = _terminate_run_process(process)
                if process_reaped:
                    if active_run_id is not None:
                        self._repair_interrupted_run_state(
                            active_run_id,
                            run_session,
                        )
                    self._finalize_reaped_process(process, run_session=run_session)
                else:
                    with self._lock:
                        cleanup_thread = self._cleanup_thread
                    if cleanup_thread is None:
                        self._retain_unreaped_process(
                            process,
                            active_run_id or "unknown-run",
                            run_session=run_session,
                        )

        if monitor_thread is not None and monitor_thread is not threading.current_thread():
            monitor_thread.join(timeout=max(0.0, deadline - time.monotonic()))
        with self._lock:
            cleanup_thread = self._cleanup_thread
        if cleanup_thread is not None and cleanup_thread is not threading.current_thread():
            cleanup_thread.join(timeout=max(0.0, deadline - time.monotonic()))

    def _dispatch_control_request(
        self,
        connection: Connection,
        request: dict[str, Any] | None = None,
        *,
        next_token: str | None = None,
    ) -> bool:
        """Serve one child controller request; return false after EOF."""
        if request is None:
            try:
                request = _receive_json_message(connection)
            except EOFError:
                return False
            except (json.JSONDecodeError, OSError, RecursionError, UnicodeDecodeError, ValueError) as exc:
                raise _RunProcessProtocolError("interactive run sent an invalid controller request") from exc

        if next_token is None:
            next_token = "legacy-direct-dispatch"
        dispatch_control_request(
            self.controller,
            connection,
            request,
            next_token=next_token,
            send_response=_send_json_message_with_deadline,
            response_timeout_seconds=_CONTROL_RESPONSE_TIMEOUT_SECONDS,
        )
        return True

    def _finalize_reaped_process(
        self,
        process: Any,
        *,
        run_session: int,
        clear_monitor_thread: bool = False,
    ) -> None:
        """Close a reaped process and release its run state despite close errors."""
        with _RUN_PROCESS_CLEANUP_LOCK:
            with _ACTIVE_RUN_PROCESSES_LOCK:
                _ACTIVE_RUN_PROCESSES.discard(process)
            try:
                process.close()
            except (OSError, ValueError):
                logger.debug("failed to close reaped interactive run process", exc_info=True)
            finally:
                with self._lock:
                    session_matches = run_session == self._run_session
                    if session_matches and (
                        self._process is None or self._process is process
                    ):
                        self._close_run_commands(
                            "interactive run ended before the chat request completed"
                        )
                        self._active_client_run_id = None
                        self._active_run_id = None
                        self._processed_stop_command_ids.clear()
                        self._process = None
                        self._active = False
                        if clear_monitor_thread:
                            self._thread = None
                        if self._cleanup_thread is threading.current_thread():
                            self._cleanup_thread = None

    def _repair_interrupted_run_state(
        self,
        actual_run_id: str,
        run_session: int,
    ) -> None:
        """Conditionally fail durable rows left running by process/IPC failure."""
        with self._lock:
            if run_session != self._run_session:
                return
            repair_interrupted_run_state(self.settings, actual_run_id, logger)

    def _retry_unreaped_process_cleanup(
        self,
        process: Any,
        actual_run_id: str,
        run_session: int,
    ) -> None:
        attempt = 0
        while True:
            delay = _REAPER_RETRY_DELAYS[
                min(attempt, len(_REAPER_RETRY_DELAYS) - 1)
            ]
            time.sleep(delay)
            with _RUN_PROCESS_CLEANUP_LOCK:
                if _terminate_run_process(process):
                    self._repair_interrupted_run_state(actual_run_id, run_session)
                    self._finalize_reaped_process(
                        process,
                        run_session=run_session,
                    )
                    logger.info(
                        "Run %s process was reaped by cleanup retry",
                        actual_run_id,
                    )
                    return
            attempt += 1
            if attempt == len(_REAPER_RETRY_DELAYS):
                logger.critical(
                    "Run %s process remains unreaped; cleanup will keep retrying",
                    actual_run_id,
                )

    def _retain_unreaped_process(
        self,
        process: Any,
        actual_run_id: str,
        *,
        run_session: int,
    ) -> None:
        """Keep ownership fail-closed and continuously retry reaping."""
        with _ACTIVE_RUN_PROCESSES_LOCK:
            _ACTIVE_RUN_PROCESSES.add(process)
        cleanup_thread: threading.Thread | None = None
        with self._lock:
            session_matches = run_session == self._run_session
            owns_manager_state = session_matches and self._process is process
            if owns_manager_state:
                self._close_run_commands(
                    "interactive run ended before the chat request completed"
                )
                self._process = process
                self._active = True
                cleanup_thread = self._cleanup_thread
                if cleanup_thread is not None and cleanup_thread.is_alive():
                    return
        try:
            cleanup_thread = threading.Thread(
                target=self._retry_unreaped_process_cleanup,
                args=(process, actual_run_id, run_session),
                name=f"autocontext-run-reaper-{actual_run_id}",
                daemon=True,
            )
            cleanup_thread.start()
            if owns_manager_state:
                with self._lock:
                    if run_session == self._run_session:
                        self._cleanup_thread = cleanup_thread
        except Exception:
            # The atexit registry retains the handle and manager state remains
            # active, so even a thread-allocation failure cannot allow overlap.
            with self._lock:
                if self._cleanup_thread is cleanup_thread:
                    self._cleanup_thread = None
            logger.exception("Run %s cleanup retry thread failed to start", actual_run_id)

    def _retain_stalled_event_relay(
        self,
        process: Any,
        actual_run_id: str,
        relay_thread: threading.Thread,
        *,
        run_session: int,
    ) -> None:
        """Keep the manager fail-closed until a blocked subscriber returns."""

        def finish_after_relay() -> None:
            relay_thread.join()
            with _RUN_PROCESS_CLEANUP_LOCK:
                self._finalize_reaped_process(
                    process,
                    run_session=run_session,
                )
                logger.info("Run %s event relay finished cleanup", actual_run_id)

        with _ACTIVE_RUN_PROCESSES_LOCK:
            _ACTIVE_RUN_PROCESSES.add(process)
        cleanup_thread: threading.Thread | None = None
        try:
            cleanup_thread = threading.Thread(
                target=finish_after_relay,
                name=f"autocontext-event-reaper-{actual_run_id}",
                daemon=True,
            )
            with self._lock:
                owns_manager_state = (
                    run_session == self._run_session and self._process is process
                )
                if owns_manager_state:
                    cleanup_thread.start()
                    self._cleanup_thread = cleanup_thread
                    self._process = None
                    self._active = True
            if not owns_manager_state:
                cleanup_thread.start()
        except Exception:
            with self._lock:
                if cleanup_thread is not None and self._cleanup_thread is cleanup_thread:
                    self._cleanup_thread = None
            logger.exception("Run %s event relay reaper failed to start", actual_run_id)

    def _monitor_run_process(
        self,
        process: Any,
        control_connection: Connection,
        event_connection: Connection,
        actual_run_id: str,
        run_session: int | None = None,
    ) -> None:
        if run_session is None:
            run_session = self._run_session
        _monitor_run_process_loop(
            self,
            process,
            control_connection,
            event_connection,
            actual_run_id,
            run_session,
            observe_process=_observe_run_process_and_kill_exited_group,
            terminate_process=_terminate_run_process,
            close_connection=_close_connection,
            wait_for_connections=wait,
            ownership_lost_error=_RunProcessOwnershipLost,
            cleanup_lock=_RUN_PROCESS_CLEANUP_LOCK,
            result_exit_grace_seconds=_RESULT_EXIT_GRACE_SECONDS,
            post_exit_idle_drain_seconds=_POST_EXIT_IDLE_DRAIN_SECONDS,
            post_exit_max_drain_seconds=_POST_EXIT_MAX_DRAIN_SECONDS,
            logger=logger,
        )

    def list_scenarios(self) -> list[str]:
        return sorted(SCENARIO_REGISTRY.keys())

    def get_environment_info(self) -> dict[str, Any]:
        """Return environment metadata for TUI display."""
        return build_run_environment_info(self.settings)

    def start_run(
        self,
        scenario: str,
        generations: int,
        run_id: str | None = None,
        *,
        require_playbook_approval: bool = False,
        client_run_id: str | None = None,
    ) -> str:
        if not 1 <= generations <= MAX_START_RUN_GENERATIONS:
            raise ValueError(f"generations must be between 1 and {MAX_START_RUN_GENERATIONS}")
        if scenario not in SCENARIO_REGISTRY:
            supported = ", ".join(sorted(SCENARIO_REGISTRY.keys()))
            raise ValueError(f"Unknown scenario '{scenario}'. Available: {supported}")
        if not _sigchld_disposition_is_safe():
            raise RuntimeError("interactive runs require the default SIGCHLD disposition")
        if not _run_process_ownership_primitives_available():
            raise RuntimeError("interactive runs require non-reaping child ownership primitives")

        actual_run_id = run_id or f"tui_{uuid.uuid4().hex[:8]}"
        # Preserve the synchronous migration/error behavior of the old thread
        # runner without constructing provider clients in the server process.
        SQLiteStore(self.settings.db_path).migrate(self._migrations_dir)

        with self._lock:
            if self._active:
                raise RuntimeError("A run is already active. Wait for it to finish or stop it.")
            if self._shutdown_requested:
                raise RuntimeError("RunManager has been shut down")
            self.controller.begin_run_session()
            self._run_session += 1
            run_session = self._run_session
            # StopCmd always carries a non-empty client_run_id, but StartRunCmd may
            # omit it. Fall back to the server run id (returned in run_accepted) so an
            # unscoped run is still addressable for stop instead of always mismatching.
            self._active_client_run_id = client_run_id or actual_run_id
            self._active_run_id = actual_run_id
            self._processed_stop_command_ids = set()
            self._thread = None
            self._active = True
            self._commands_open = False

        parent_control: Connection | None = None
        child_control: Connection | None = None
        parent_events: Connection | None = None
        child_events: Connection | None = None
        process: Any | None = None
        process_started = False
        process_registered = False
        try:
            context = multiprocessing.get_context("spawn")
            parent_control, child_control = context.Pipe(duplex=True)
            parent_events, child_events = context.Pipe(duplex=False)
            process = context.Process(
                target=_run_generation_process,
                name=f"autocontext-run-{actual_run_id}",
                args=(
                    self.settings.model_dump(mode="json"),
                    scenario,
                    generations,
                    actual_run_id,
                    require_playbook_approval,
                    child_control,
                    child_events,
                ),
            )
            with self._lock:
                if self._shutdown_requested:
                    raise RuntimeError("RunManager was shut down during startup")
                if not _sigchld_disposition_is_safe():
                    raise RuntimeError(
                        "SIGCHLD disposition changed before interactive run startup"
                    )
                if not _run_process_ownership_primitives_available():
                    raise RuntimeError(
                        "child ownership primitives changed before interactive run startup"
                    )
                _start_owned_run_process(process)
                process_started = True
                self._process = process
                process_registered = True
                with _ACTIVE_RUN_PROCESSES_LOCK:
                    _ACTIVE_RUN_PROCESSES.add(process)
                child_control.close()
                child_events.close()
                monitor_thread = threading.Thread(
                    target=self._monitor_run_process,
                    args=(
                        process,
                        parent_control,
                        parent_events,
                        actual_run_id,
                        run_session,
                    ),
                    daemon=True,
                )
                self._commands_open = True
                try:
                    monitor_thread.start()
                except BaseException:
                    self._commands_open = False
                    raise
                self._thread = monitor_thread
        except BaseException:
            with self._lock:
                self._commands_open = False
                self.controller.abort_pending_chats(
                    "interactive run ended before the chat request completed"
                )
            for connection in (
                parent_control,
                child_control,
                parent_events,
                child_events,
            ):
                if connection is not None:
                    _close_connection(connection)
            with _RUN_PROCESS_CLEANUP_LOCK:
                try:
                    process_pid = None if process is None else process.pid
                except ValueError:
                    process_pid = None
                process_reaped = process is None or process_pid is None or _terminate_run_process(process)
                if process_reaped:
                    if process_started:
                        self._repair_interrupted_run_state(
                            actual_run_id,
                            run_session,
                        )
                    if process_registered and process is not None:
                        self._finalize_reaped_process(
                            process,
                            clear_monitor_thread=True,
                            run_session=run_session,
                        )
                    else:
                        if process is not None:
                            try:
                                process.close()
                            except (OSError, ValueError):
                                logger.debug(
                                    "failed to close unstarted interactive run process",
                                    exc_info=True,
                                )
                        with self._lock:
                            self._active_client_run_id = None
                            self._active_run_id = None
                            self._processed_stop_command_ids.clear()
                            self._thread = None
                            self._process = None
                            self._active = False
                            self._commands_open = False
                else:
                    self._thread = None
                    assert process is not None
                    self._retain_unreaped_process(
                        process,
                        actual_run_id,
                        run_session=run_session,
                    )
            raise
        return actual_run_id
