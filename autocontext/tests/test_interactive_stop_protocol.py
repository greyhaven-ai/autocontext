from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autocontext.config import AppSettings
from autocontext.harness.core.controller import LoopController
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.server import app as app_module
from autocontext.server.run_manager import RunManager


def _settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        monitor_enabled=False,
    )


def test_safe_stop_advertised_and_reports_no_active_run_when_manager_present(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # With a RunManager wired (the component that honors stop), the server
    # advertises safe_run_stop_v1 and a stop with no active run is a clean error.
    settings = _settings(tmp_path)
    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(settings.event_stream_path)
    run_manager = RunManager(controller, events, settings)

    app = app_module.create_app(controller=controller, events=events, run_manager=run_manager)
    with TestClient(app) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            assert websocket.receive_json() == {
                "type": "hello",
                "protocol_version": 1,
                "capabilities": ["safe_run_stop_v1"],
            }
            # With a run_manager, the server also sends environment info on connect.
            assert websocket.receive_json()["type"] == "environments"
            websocket.send_json(
                {
                    "type": "stop",
                    "client_run_id": "client-run-1",
                    "command_id": "command-stop-1",
                }
            )
            assert websocket.receive_json() == {
                "type": "error",
                "client_run_id": "client-run-1",
                "command_id": "command-stop-1",
                "message": "no active run to stop",
            }

    # The not_active path must never mutate the controller.
    controller.request_stop.assert_not_called()


def test_safe_stop_not_advertised_without_a_run_manager(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # `autoctx run --serve` builds the app without a RunManager. The server must
    # not advertise a capability it cannot service, and a stop is a clean error.
    settings = _settings(tmp_path)
    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(settings.event_stream_path)

    with TestClient(app_module.create_app(controller=controller, events=events)) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            # No manager -> no safe_run_stop_v1 in the hello frame.
            assert websocket.receive_json() == {
                "type": "hello",
                "protocol_version": 1,
            }
            websocket.send_json(
                {
                    "type": "stop",
                    "client_run_id": "client-run-1",
                    "command_id": "command-stop-1",
                }
            )
            assert websocket.receive_json() == {
                "type": "error",
                "client_run_id": "client-run-1",
                "command_id": "command-stop-1",
                "message": "no active run to stop",
            }

    controller.request_stop.assert_not_called()
    assert controller.mock_calls == []
