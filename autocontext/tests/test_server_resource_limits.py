from __future__ import annotations

import asyncio
import json
import os
import threading
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from starlette.websockets import WebSocketDisconnect

from autocontext.config import AppSettings
from autocontext.loop.controller import LoopController
from autocontext.loop.events import EventStreamEmitter
from autocontext.server import app as app_module
from autocontext.server.protocol import parse_client_message
from autocontext.server.resource_limits import (
    MAX_EVENT_STREAM_LINE_BYTES,
    MAX_HTTP_REQUEST_BODY_BYTES,
    MAX_INTERACTIVE_FRAME_BYTES,
    MAX_INTERACTIVE_ID_CHARS,
    MAX_INTERACTIVE_ROLE_CHARS,
    MAX_INTERACTIVE_TEXT_CHARS,
    MAX_START_RUN_GENERATIONS,
    EventStreamTailState,
    InteractiveWorkLimiter,
    InteractiveWorkLimitExceeded,
    read_event_stream_lines,
    read_limited_json_object,
)
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


def _create_app(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    controller: LoopController | MagicMock | None = None,
    events: EventStreamEmitter | None = None,
    run_manager: RunManager | MagicMock | None = None,
):
    settings = _settings(tmp_path)
    monkeypatch.setattr(app_module, "load_settings", lambda: settings)
    monkeypatch.setattr(app_module, "_build_scenario_creator", lambda _settings: None)
    return app_module.create_app(controller=controller, events=events, run_manager=run_manager), settings


@pytest.mark.parametrize(
    "payload",
    [
        {"type": "start_run", "scenario": "grid_ctf", "generations": MAX_START_RUN_GENERATIONS + 1},
        {"type": "inject_hint", "text": "x" * (MAX_INTERACTIVE_TEXT_CHARS + 1)},
        {"type": "chat_agent", "role": "x" * (MAX_INTERACTIVE_ROLE_CHARS + 1), "message": "hello"},
        {"type": "chat_agent", "role": "analyst", "message": "x" * (MAX_INTERACTIVE_TEXT_CHARS + 1)},
        {"type": "create_scenario", "description": "x" * (MAX_INTERACTIVE_TEXT_CHARS + 1)},
        {"type": "revise_scenario", "feedback": "x" * (MAX_INTERACTIVE_TEXT_CHARS + 1)},
        {"type": "pause", "command_id": "x" * (MAX_INTERACTIVE_ID_CHARS + 1)},
    ],
)
def test_interactive_protocol_rejects_expensive_or_oversized_fields(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        parse_client_message(payload)


def test_run_manager_enforces_generation_limit_for_non_websocket_callers(tmp_path: Path) -> None:
    settings = _settings(tmp_path)
    manager = RunManager(MagicMock(spec=LoopController), MagicMock(spec=EventStreamEmitter), settings)

    with pytest.raises(ValueError, match="generations must be between"):
        manager.start_run("grid_ctf", MAX_START_RUN_GENERATIONS + 1)


def test_oversized_interactive_frame_closes_with_1009_before_json_parsing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    app, _settings_value = _create_app(monkeypatch, tmp_path, controller=controller, events=events)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_text("{" + "x" * MAX_INTERACTIVE_FRAME_BYTES)
            with pytest.raises(WebSocketDisconnect) as disconnected:
                websocket.receive_json()

    assert disconnected.value.code == 1009
    controller.assert_not_called()


def test_invalid_and_field_limited_messages_return_stable_errors_without_closing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    app, _settings_value = _create_app(monkeypatch, tmp_path, controller=controller, events=events)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_text("{not-json")
            assert websocket.receive_json() == {"type": "error", "message": "Invalid interactive message."}

            websocket.send_json(
                {"type": "start_run", "scenario": "grid_ctf", "generations": MAX_START_RUN_GENERATIONS + 1}
            )
            assert websocket.receive_json() == {
                "type": "error",
                "message": "Unknown or invalid interactive command.",
            }

            websocket.send_json({"type": "pause"})
            assert websocket.receive_json() == {"type": "state", "paused": True, "generation": 0, "phase": ""}


def test_interactive_handler_exception_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = MagicMock(spec=LoopController)
    controller.submit_chat.side_effect = RuntimeError("provider-secret-should-not-cross-boundary")
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    app, _settings_value = _create_app(monkeypatch, tmp_path, controller=controller, events=events)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            websocket.send_json({"type": "chat_agent", "role": "analyst", "message": "hello"})
            response = websocket.receive_json()

    assert response == {"type": "error", "message": "Chat request failed."}
    assert "provider-secret" not in json.dumps(response)


def test_run_start_exception_is_redacted(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    controller = MagicMock(spec=LoopController)
    events = EventStreamEmitter(tmp_path / "events.ndjson")
    run_manager = MagicMock(spec=RunManager)
    run_manager.is_active = False
    run_manager.get_environment_info.return_value = {
        "scenarios": [],
        "executors": [],
        "current_executor": "local",
        "agent_provider": "deterministic",
    }
    run_manager.start_run.side_effect = RuntimeError("database-secret-should-not-cross-boundary")
    app, _settings_value = _create_app(
        monkeypatch,
        tmp_path,
        controller=controller,
        events=events,
        run_manager=run_manager,
    )

    with TestClient(app) as client:
        with client.websocket_connect("/ws/interactive") as websocket:
            assert websocket.receive_json()["type"] == "hello"
            assert websocket.receive_json()["type"] == "environments"
            websocket.send_json({"type": "start_run", "scenario": "grid_ctf", "generations": 1})
            response = websocket.receive_json()

    assert response == {"type": "error", "message": "Unable to start run."}
    assert "database-secret" not in json.dumps(response)


def test_replay_size_is_checked_before_json_load_and_errors_are_stable(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    app, settings = _create_app(monkeypatch, tmp_path)
    replay_dir = settings.runs_root / "run-1" / "generations" / "gen_1" / "replays"
    replay_dir.mkdir(parents=True)
    replay_path = replay_dir / "replay.json"
    monkeypatch.setattr(app_module, "MAX_REPLAY_FILE_BYTES", 32)

    replay_path.write_text('{"secret":"this-is-valid-but-too-large"}', encoding="utf-8")
    with TestClient(app, raise_server_exceptions=False) as client:
        too_large = client.get("/api/runs/run-1/replay/1")
        assert too_large.status_code == 413
        assert too_large.json() == {"detail": "replay exceeds the file size limit"}

        replay_path.write_text('{"secret":', encoding="utf-8")
        malformed = client.get("/api/runs/run-1/replay/1")
        assert malformed.status_code == 500
        assert malformed.json() == {"detail": "replay is unavailable"}
        assert "secret" not in malformed.text

        replay_path.write_text('{"ok":true}', encoding="utf-8")
        assert client.get("/api/runs/run-1/replay/1").json() == {"ok": True}


def test_replay_reader_rejects_final_symlinks(tmp_path: Path) -> None:
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("platform does not expose no-follow file opens")
    target = tmp_path / "outside.json"
    target.write_text('{"secret":true}', encoding="utf-8")
    replay = tmp_path / "replay.json"
    replay.symlink_to(target)

    with pytest.raises(OSError):
        read_limited_json_object(replay)


def test_http_body_limit_rejects_declared_and_streamed_oversize_requests(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_module, "MAX_HTTP_REQUEST_BODY_BYTES", 64)
    app, _settings_value = _create_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        declared = client.put(
            "/api/knowledge/grid_ctf",
            content=b"x" * 65,
            headers={"content-type": "application/json"},
        )
        assert declared.status_code == 413

        def chunks():
            yield b'{"playbook":"'
            yield b"x" * 64
            yield b'"}'

        streamed = client.put(
            "/api/knowledge/grid_ctf",
            content=chunks(),
            headers={"content-type": "application/json", "transfer-encoding": "chunked"},
        )
        assert streamed.status_code == 413
        assert streamed.json() == {"detail": "Request body exceeds the size limit"}


def test_event_stream_tail_is_incremental_and_drops_oversized_lines(tmp_path: Path) -> None:
    event_path = tmp_path / "events.ndjson"
    event_path.write_bytes(b'{"first":1}\npartial')
    state = EventStreamTailState()

    assert read_event_stream_lines(event_path, state) == ['{"first":1}']
    with event_path.open("ab") as handle:
        handle.write(b'-line\n')
        handle.write(b"x" * (MAX_EVENT_STREAM_LINE_BYTES + 1) + b"\n")
        handle.write(b'{"last":2}\n')

    assert read_event_stream_lines(event_path, state) == ["partial-line", '{"last":2}']


def test_global_websocket_cap_includes_event_stream_connections(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(app_module, "MAX_WEBSOCKET_CONNECTIONS", 1)
    app, _settings_value = _create_app(monkeypatch, tmp_path)

    with TestClient(app) as client:
        with client.websocket_connect("/ws/events"):
            with pytest.raises(WebSocketDisconnect) as rejected:
                with client.websocket_connect("/ws/interactive"):
                    pass
            assert rejected.value.code == 1013


def test_http_request_body_default_limit_is_large_enough_for_knowledge_payloads() -> None:
    assert MAX_HTTP_REQUEST_BODY_BYTES > 3 * 1024 * 1024


@pytest.mark.asyncio
async def test_interactive_work_limiter_caps_pending_work_and_holds_slot_after_cancellation() -> None:
    limiter = InteractiveWorkLimiter(max_concurrent=1, max_pending=1)
    started = threading.Event()
    release = threading.Event()

    def blocking_work() -> str:
        started.set()
        assert release.wait(timeout=5)
        return "done"

    first = asyncio.create_task(limiter.run(blocking_work))
    assert await asyncio.to_thread(started.wait, 2)
    second = asyncio.create_task(limiter.run(lambda: "queued"))
    await asyncio.sleep(0)

    with pytest.raises(InteractiveWorkLimitExceeded):
        await limiter.run(lambda: "rejected")

    first.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first
    with pytest.raises(InteractiveWorkLimitExceeded):
        await limiter.run(lambda: "still-rejected")

    release.set()
    assert await second == "queued"
