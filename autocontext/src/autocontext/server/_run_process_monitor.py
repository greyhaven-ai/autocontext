"""Parent-side lifecycle monitor for an interactive run process."""

from __future__ import annotations

import logging
import queue
import secrets
import threading
import time
from collections import deque
from collections.abc import Callable
from multiprocessing.connection import Connection
from typing import Any, Literal, Protocol

from autocontext.server._run_process_ipc import (
    _MAX_EVENT_IPC_BYTES,
    _IncrementalConnectionReader,
    _RunProcessProtocolError,
)

_RunProcessState = Literal["running", "exited", "ownership_lost"]
_MAX_PENDING_EVENTS = 4
_EVENT_RELAY_CAPACITY = 4
_EVENT_CALLBACK_TIMEOUT_SECONDS = 1.0
_EVENT_RELAY_STOP_TIMEOUT_SECONDS = 0.1
_RELAY_STOP = object()


class _WaitForConnections(Protocol):
    def __call__(
        self,
        object_list: list[Any],
        timeout: float | None = None,
    ) -> list[Any]: ...


class _EventRelay:
    """Run trusted event callbacks off the ownership-critical monitor thread."""

    def __init__(self, emit: Callable[..., None]) -> None:
        self._emit = emit
        self._queue: queue.Queue[dict[str, Any] | object] = queue.Queue(
            maxsize=_EVENT_RELAY_CAPACITY
        )
        self._lock = threading.Lock()
        self._outstanding = 0
        self._inflight_since: float | None = None
        self._failure: BaseException | None = None
        self._started = False
        self._stop_requested = False
        self.thread = threading.Thread(
            target=self._run,
            name="autocontext-run-event-relay",
            daemon=True,
        )

    def start(self) -> None:
        self.thread.start()
        self._started = True

    def submit(self, message: dict[str, Any]) -> bool:
        with self._lock:
            if self._failure is not None or self._stop_requested:
                return False
            try:
                self._queue.put_nowait(message)
            except queue.Full:
                return False
            self._outstanding += 1
            return True

    @property
    def is_idle(self) -> bool:
        with self._lock:
            return self._failure is None and self._outstanding == 0

    @property
    def can_accept(self) -> bool:
        return not self._queue.full()

    def check_health(self) -> None:
        with self._lock:
            failure = self._failure
            inflight_since = self._inflight_since
        if failure is not None:
            raise _RunProcessProtocolError(
                "interactive run event relay failed"
            ) from failure
        if (
            inflight_since is not None
            and time.monotonic() - inflight_since >= _EVENT_CALLBACK_TIMEOUT_SECONDS
        ):
            raise _RunProcessProtocolError(
                "interactive run event subscriber exceeded its time limit"
            )

    def stop(self, *, drop_pending: bool) -> bool:
        if not self._started:
            return True
        if drop_pending:
            dropped = 0
            while True:
                try:
                    message = self._queue.get_nowait()
                except queue.Empty:
                    break
                if message is not _RELAY_STOP:
                    dropped += 1
            if dropped:
                with self._lock:
                    self._outstanding -= dropped
        if not self._stop_requested:
            self._stop_requested = True
            try:
                self._queue.put_nowait(_RELAY_STOP)
            except queue.Full:
                return False
        self.thread.join(timeout=_EVENT_RELAY_STOP_TIMEOUT_SECONDS)
        if self.thread.is_alive():
            return False
        self.check_health()
        return True

    def _run(self) -> None:
        while True:
            message = self._queue.get()
            if message is _RELAY_STOP:
                return
            assert isinstance(message, dict)
            try:
                with self._lock:
                    self._inflight_since = time.monotonic()
                self._emit(
                    message["event"],
                    message["payload"],
                    channel=message["channel"],
                )
            except BaseException as exc:
                with self._lock:
                    self._failure = exc
                return
            finally:
                with self._lock:
                    self._inflight_since = None
                    self._outstanding -= 1


def _validated_event_message(message: dict[str, Any]) -> dict[str, Any]:
    event = message.get("event")
    payload = message.get("payload")
    channel = message.get("channel")
    if not isinstance(event, str) or not isinstance(payload, dict) or not isinstance(
        channel, str
    ):
        raise _RunProcessProtocolError("interactive run sent malformed event fields")
    return message


def _monitor_run_process(
    manager: Any,
    process: Any,
    control_connection: Connection,
    event_connection: Connection,
    actual_run_id: str,
    run_session: int,
    *,
    observe_process: Callable[[Any], _RunProcessState],
    terminate_process: Callable[[Any], bool],
    close_connection: Callable[[Connection], None],
    wait_for_connections: _WaitForConnections,
    ownership_lost_error: type[BaseException],
    cleanup_lock: Any,
    result_exit_grace_seconds: float,
    post_exit_idle_drain_seconds: float,
    post_exit_max_drain_seconds: float,
    logger: logging.Logger,
) -> None:
    """Relay bounded IPC while retaining ownership of the spawned leader."""
    control_open = True
    event_open = True
    pending_events: deque[dict[str, Any]] = deque()
    process_exited = False
    process_reaped = False
    monitor_failed = False
    post_exit_idle_deadline: float | None = None
    post_exit_max_deadline: float | None = None
    result_exit_deadline: float | None = None
    result: dict[str, Any] | None = None
    expected_control_token: str | None = None
    relay: _EventRelay | None = None

    def mark_process_exited(reason: str) -> None:
        nonlocal process_exited, post_exit_idle_deadline, post_exit_max_deadline
        if process_exited:
            return
        process_exited = True
        manager._close_run_commands(
            reason,
            process=process,
            run_session=run_session,
        )
        now = time.monotonic()
        post_exit_idle_deadline = now + post_exit_idle_drain_seconds
        post_exit_max_deadline = now + post_exit_max_drain_seconds

    def observe_leader() -> None:
        state = observe_process(process)
        if state == "ownership_lost":
            raise ownership_lost_error("interactive run process ownership was lost")
        if state == "exited":
            mark_process_exited(
                "interactive run ended before the chat request completed"
            )

    def accept_event_messages(messages: list[dict[str, Any]]) -> None:
        nonlocal result, result_exit_deadline
        for message in messages:
            if result is not None:
                raise _RunProcessProtocolError(
                    "interactive run sent data after its terminal result"
                )
            message_type = message.get("type")
            if message_type == "event":
                pending_events.append(_validated_event_message(message))
                continue
            if message_type != "result" or not isinstance(message.get("ok"), bool):
                raise _RunProcessProtocolError(
                    "interactive run sent an unknown event message"
                )
            if message.get("run_id") != actual_run_id:
                raise _RunProcessProtocolError(
                    "interactive run result used an unexpected run id"
                )
            result = message
            manager._close_run_commands(
                "interactive run reported its terminal result",
                process=process,
                run_session=run_session,
            )
            result_exit_deadline = time.monotonic() + result_exit_grace_seconds

    try:
        control_reader = _IncrementalConnectionReader(control_connection)
        event_reader = _IncrementalConnectionReader(
            event_connection,
            max_frame_bytes=_MAX_EVENT_IPC_BYTES,
        )
        relay = _EventRelay(manager.events.emit)
        relay.start()
        while (
            not process_exited
            or control_open
            or event_open
            or pending_events
            or not relay.is_idle
        ):
            relay.check_health()
            if not process_exited:
                observe_leader()

            now = time.monotonic()
            if (
                result_exit_deadline is not None
                and not process_exited
                and now >= result_exit_deadline
            ):
                raise _RunProcessProtocolError(
                    "interactive run reported a result but did not exit"
                )
            if post_exit_max_deadline is not None and now >= post_exit_max_deadline:
                open_channels = [
                    connection
                    for connection, is_open in (
                        (control_connection, control_open),
                        (event_connection, event_open),
                    )
                    if is_open
                ]
                if (
                    pending_events
                    or not relay.is_idle
                    or control_reader.has_buffered_bytes
                    or event_reader.has_buffered_bytes
                    or bool(
                        wait_for_connections(open_channels, timeout=0)
                        if open_channels
                        else []
                    )
                ):
                    raise _RunProcessProtocolError(
                        "interactive run exceeded its post-exit drain deadline"
                    )
                break

            idle_expired = (
                post_exit_idle_deadline is not None
                and now >= post_exit_idle_deadline
            )
            idle_kernel_ready: list[Any] = []
            if idle_expired:
                open_channels = [
                    connection
                    for connection, is_open in (
                        (control_connection, control_open),
                        (event_connection, event_open),
                    )
                    if is_open
                ]
                idle_kernel_ready = (
                    wait_for_connections(open_channels, timeout=0)
                    if open_channels
                    else []
                )
                complete_buffered = (
                    control_reader.has_buffered_frame
                    or event_reader.has_buffered_frame
                )
                if not complete_buffered:
                    if (
                        control_reader.has_buffered_bytes
                        or event_reader.has_buffered_bytes
                    ):
                        raise _RunProcessProtocolError(
                            "interactive run IPC channel retained a partial frame after exit"
                        )
                    if (
                        not pending_events
                        and relay.is_idle
                        and not idle_kernel_ready
                    ):
                        break

            waitables: list[Any] = []
            if control_open:
                waitables.append(control_connection)
            if event_open and len(pending_events) < _MAX_PENDING_EVENTS:
                waitables.append(event_connection)
            if not process_exited:
                waitables.append(process.sentinel)

            buffered_ready: list[Any] = []
            if control_open and control_reader.has_buffered_frame:
                buffered_ready.append(control_connection)
            if (
                event_open
                and len(pending_events) < _MAX_PENDING_EVENTS
                and event_reader.has_buffered_frame
            ):
                buffered_ready.append(event_connection)
            wait_timeout = (
                0.0
                if buffered_ready or (pending_events and relay.can_accept)
                else 0.1
            )
            deadlines = [
                deadline
                for deadline in (
                    post_exit_idle_deadline,
                    post_exit_max_deadline,
                    result_exit_deadline if not process_exited else None,
                )
                if deadline is not None
            ]
            if deadlines:
                wait_timeout = max(0.0, min(wait_timeout, min(deadlines) - now))
            polled_ready = (
                wait_for_connections(waitables, timeout=wait_timeout)
                if waitables
                else []
            )
            ready = [*buffered_ready, *polled_ready]
            control_ready = any(item is control_connection for item in ready)
            event_ready = any(item is event_connection for item in ready)
            sentinel_ready = any(item is process.sentinel for item in ready)

            # Decode the event channel first so an already-sent terminal result
            # closes command admission before any simultaneously-ready control.
            event_backlog_unscanned = (
                len(pending_events) >= _MAX_PENDING_EVENTS
                and event_open
                and (
                    event_reader.has_buffered_frame
                    or bool(wait_for_connections([event_connection], timeout=0))
                )
            )
            if event_ready and len(pending_events) < _MAX_PENDING_EVENTS:
                messages, event_open = event_reader.receive_available(
                    read_from_fd=(
                        not idle_expired
                        or any(item is event_connection for item in idle_kernel_ready)
                    ),
                    max_messages=_MAX_PENDING_EVENTS - len(pending_events),
                )
                accept_event_messages(messages)
                if messages and process_exited:
                    post_exit_idle_deadline = (
                        time.monotonic() + post_exit_idle_drain_seconds
                    )
                if len(pending_events) >= _MAX_PENDING_EVENTS:
                    event_backlog_unscanned = event_reader.has_buffered_frame or (
                        event_open
                        and bool(wait_for_connections([event_connection], timeout=0))
                    )

            requests: list[dict[str, Any]] = []
            if control_ready:
                requests, control_open = control_reader.receive_available(
                    read_from_fd=(
                        not idle_expired
                        or any(item is control_connection for item in idle_kernel_ready)
                    ),
                    max_messages=2,
                )

            if sentinel_ready or requests:
                observe_leader()

            if requests:
                if event_backlog_unscanned:
                    raise _RunProcessProtocolError(
                        "interactive run event backlog hid controller ordering"
                    )
                if process_exited:
                    raise _RunProcessProtocolError(
                        "interactive run sent controller data after process exit"
                    )
                if result is not None:
                    raise _RunProcessProtocolError(
                        "interactive run sent controller data after its terminal result"
                    )
                if len(requests) != 1:
                    raise _RunProcessProtocolError(
                        "interactive run pipelined controller requests"
                    )
                request = requests[0]
                if (
                    "token" not in request
                    or request["token"] != expected_control_token
                ):
                    raise _RunProcessProtocolError(
                        "interactive run used an invalid controller sequence token"
                    )
                next_token = secrets.token_urlsafe(24)
                manager._dispatch_control_request(
                    control_connection,
                    request=request,
                    next_token=next_token,
                )
                expected_control_token = next_token

            if pending_events and relay.submit(pending_events[0]):
                pending_events.popleft()

            if not process_exited and not control_open and not event_open:
                grace = result_exit_grace_seconds if result is not None else 0.1
                if not wait_for_connections([process.sentinel], timeout=grace):
                    raise _RunProcessProtocolError(
                        "interactive run closed its IPC channels before exiting"
                    )
                observe_leader()
                if not process_exited:
                    raise _RunProcessProtocolError(
                        "interactive run sentinel became ready before process exit"
                    )

        process_reaped = terminate_process(process)
        if not process_reaped:
            raise _RunProcessProtocolError(
                "interactive run process could not be reaped"
            )
        clean_exit = process.exitcode == 0
        if result is not None and result.get("ok") is True and clean_exit:
            best_score = result.get("best_score")
            if isinstance(best_score, int | float):
                logger.info(
                    "Run %s completed: best_score=%.4f",
                    actual_run_id,
                    best_score,
                )
            else:
                logger.info("Run %s completed", actual_run_id)
        elif result is not None and result.get("ok") is not True:
            logger.error(
                "Run %s failed: %s: %s",
                actual_run_id,
                result.get("error_type", "Exception"),
                result.get("message", "generation process failed"),
            )
        elif result is not None:
            raise _RunProcessProtocolError(
                "interactive run reported success but exited abnormally"
            )
        elif process.exitcode not in (0, None):
            logger.error(
                "Run %s process exited with status %s",
                actual_run_id,
                process.exitcode,
            )
        else:
            logger.error(
                "Run %s process exited without a terminal result",
                actual_run_id,
            )
    except BaseException:
        monitor_failed = True
        logger.exception("Run %s monitor failed", actual_run_id)
    finally:
        manager._close_run_commands(
            "interactive run ended before the chat request completed",
            process=process,
            run_session=run_session,
        )
        relay_stopped = True
        if relay is not None:
            try:
                relay_stopped = relay.stop(drop_pending=monitor_failed)
            except _RunProcessProtocolError:
                monitor_failed = True
                relay_stopped = not relay.thread.is_alive()
                logger.exception(
                    "Run %s event relay failed during cleanup",
                    actual_run_id,
                )
        close_connection(control_connection)
        close_connection(event_connection)
        with cleanup_lock:
            if not process_reaped:
                process_reaped = terminate_process(process)
            if process_reaped:
                manager._repair_interrupted_run_state(actual_run_id, run_session)
                if relay_stopped:
                    manager._finalize_reaped_process(
                        process,
                        run_session=run_session,
                    )
                else:
                    assert relay is not None
                    logger.error(
                        "Run %s event subscriber remained blocked after cleanup",
                        actual_run_id,
                    )
                    manager._retain_stalled_event_relay(
                        process,
                        actual_run_id,
                        relay.thread,
                        run_session=run_session,
                    )
            else:
                logger.error(
                    "Run %s process remained unreaped after cleanup",
                    actual_run_id,
                )
                manager._retain_unreaped_process(
                    process,
                    actual_run_id,
                    run_session=run_session,
                )
