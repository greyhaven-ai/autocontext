from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient

from autocontext.config import AppSettings
from autocontext.harness.core.controller import LoopController
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.server import app as app_module


def test_python_interactive_server_supports_safe_stop_and_reports_no_active_run(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        agent_provider="deterministic",
        monitor_enabled=False,
    )
    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(settings.event_stream_path)

    with TestClient(app_module.create_app(controller=controller, events=events)) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            # The Python server now advertises the safe-stop capability in its hello frame.
            assert websocket.receive_json() == {
                "type": "hello",
                "protocol_version": 1,
                "capabilities": ["safe_run_stop_v1"],
            }
            websocket.send_json(
                {
                    "type": "stop",
                    "client_run_id": "client-run-1",
                    "command_id": "command-stop-1",
                }
            )

            # No run is active (this app is wired without a run_manager), so the
            # server reports "no active run to stop" and echoes the identifiers.
            assert websocket.receive_json() == {
                "type": "error",
                "client_run_id": "client-run-1",
                "command_id": "command-stop-1",
                "message": "no active run to stop",
            }

    # The not_active path must never mutate the controller.
    controller.request_stop.assert_not_called()
    assert controller.mock_calls == []
