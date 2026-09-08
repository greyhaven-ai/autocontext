from __future__ import annotations

import logging
import multiprocessing
import threading
import time
from multiprocessing.connection import wait
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from autocontext.server._run_process_ipc import _send_json_message
from autocontext.server._run_process_monitor import _monitor_run_process


@pytest.mark.parametrize("event_count", [5, 65])
@pytest.mark.parametrize("control_case", ["valid", "terminal", "missing-token", "stale-token", "exited"])
def test_monitor_scans_event_burst_before_admitting_control(
    event_count: int,
    control_case: str,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A queued burst must drain, while a terminal result still bars control."""
    parent_control, child_control = multiprocessing.Pipe(duplex=True)
    parent_events, child_events = multiprocessing.Pipe(duplex=False)
    sentinel, sentinel_writer = multiprocessing.Pipe(duplex=False)
    process = SimpleNamespace(sentinel=sentinel, exitcode=0)
    exited = threading.Event()
    manager = Mock()
    result = {"type": "result", "run_id": "burst-run", "ok": True, "best_score": 1.0}

    def finish_child(*_args: object, **_kwargs: object) -> None:
        _send_json_message(child_events, result)
        child_control.close()
        child_events.close()
        exited.set()

    manager._dispatch_control_request.side_effect = finish_child
    deadline = time.monotonic() + 5.0
    observations = 0

    def observe_process(_process: object) -> str:
        nonlocal observations
        observations += 1
        if time.monotonic() >= deadline:
            raise TimeoutError("monitor did not finish the preloaded event burst")
        if control_case == "exited" and observations > 1:
            return "exited"
        return "exited" if exited.is_set() else "running"

    try:
        # Both channels are ready before monitoring starts, without depending on
        # child-process scheduling or the host's process-ownership primitives.
        for index in range(event_count):
            _send_json_message(
                child_events,
                {"type": "event", "event": "burst", "payload": {"index": index}, "channel": "generation"},
            )
        if control_case == "terminal":
            _send_json_message(child_events, result)
        request: dict[str, object] = {"type": "control", "operation": "is_paused", "args": [], "token": None}
        if control_case == "missing-token":
            request.pop("token")
        elif control_case == "stale-token":
            request["token"] = "stale"
        _send_json_message(child_control, request)

        _monitor_run_process(
            manager,
            process,
            parent_control,
            parent_events,
            "burst-run",
            1,
            observe_process=observe_process,
            terminate_process=lambda _process: True,
            close_connection=lambda connection: connection.close(),
            wait_for_connections=wait,
            ownership_lost_error=RuntimeError,
            cleanup_lock=threading.Lock(),
            result_exit_grace_seconds=1.0,
            post_exit_idle_drain_seconds=0.2,
            post_exit_max_drain_seconds=2.0,
            logger=logging.getLogger(__name__),
        )
    finally:
        for connection in (parent_control, child_control, parent_events, child_events, sentinel, sentinel_writer):
            connection.close()

    if control_case != "valid":
        manager._dispatch_control_request.assert_not_called()
        expected_error = {
            "terminal": "after its terminal result",
            "missing-token": "invalid controller sequence token",
            "stale-token": "invalid controller sequence token",
            "exited": "after process exit",
        }[control_case]
        assert expected_error in caplog.text
    else:
        manager._dispatch_control_request.assert_called_once()
        assert [call.args[1]["index"] for call in manager.events.emit.call_args_list] == list(range(event_count))
        assert "monitor failed" not in caplog.text
