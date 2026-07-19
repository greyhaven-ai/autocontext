from __future__ import annotations

from unittest.mock import MagicMock

from autocontext.harness.core.controller import LoopController
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.server.protocol import SERVER_CAPABILITIES
from autocontext.server.run_manager import RunManager


def _make_manager(tmp_path) -> tuple[RunManager, MagicMock]:
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    manager = RunManager.__new__(RunManager)
    manager.controller = controller
    manager.events = events
    manager._active = False
    manager._active_client_run_id = None
    manager._processed_stop_command_ids = set()
    return manager, controller


def test_stop_run_not_active_returns_not_active(tmp_path) -> None:
    rm, controller = _make_manager(tmp_path)
    rm._active = False

    assert rm.stop_run("r1", "c1", None) == "not_active"
    controller.request_stop.assert_not_called()


def test_stop_run_scope_mismatch_does_not_touch_controller(tmp_path) -> None:
    rm, controller = _make_manager(tmp_path)
    rm._active = True
    rm._active_client_run_id = "r1"
    rm._processed_stop_command_ids = set()

    assert rm.stop_run("r2", "c1", None) == "scope_mismatch"
    controller.request_stop.assert_not_called()


def test_stop_run_accepts_then_dedups_repeat_command(tmp_path) -> None:
    rm, controller = _make_manager(tmp_path)
    rm._active = True
    rm._active_client_run_id = "r1"
    rm._processed_stop_command_ids = set()

    assert rm.stop_run("r1", "c1", None) == "accepted"
    assert controller.request_stop.call_count == 1

    assert rm.stop_run("r1", "c1", None) == "duplicate"
    # A duplicate command must not re-trigger the controller.
    assert controller.request_stop.call_count == 1


def test_stop_run_none_client_run_id_targets_active_run(tmp_path) -> None:
    rm, controller = _make_manager(tmp_path)
    rm._active = True
    rm._active_client_run_id = "r1"
    rm._processed_stop_command_ids = set()

    assert rm.stop_run(None, "c2", None) == "accepted"
    controller.request_stop.assert_called_once()


def test_server_advertises_safe_run_stop_capability() -> None:
    assert "safe_run_stop_v1" in SERVER_CAPABILITIES
