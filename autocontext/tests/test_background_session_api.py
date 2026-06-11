from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.server.cockpit_api import cockpit_router
from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore, RuntimeSessionEventType
from autocontext.storage.sqlite_store import SQLiteStore  # type: ignore[import-untyped]

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)
    return store


def _persist_background_sessions(db_path: Path) -> None:
    app_store = SQLiteStore(db_path)
    app_store.create_run("test-run-1", "grid_ctf", 1, "local")
    app_store.enqueue_task("task-1", "autoctx run grid_ctf")
    app_store.complete_task("task-1", 1.0, "ok", 1, True)
    app_store.enqueue_task("queued-1", "autoctx run queued")

    log = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "run:test-run-1:runtime",
            "parentSessionId": "",
            "taskId": "task-1",
            "workerId": "worker-1",
            "metadata": {"goal": "autoctx run grid_ctf", "runId": "test-run-1"},
            "createdAt": "2026-06-01T00:00:00.000Z",
            "updatedAt": "2026-06-01T00:00:02.000Z",
            "events": [
                {
                    "eventId": "event-1",
                    "sessionId": "run:test-run-1:runtime",
                    "sequence": 0,
                    "eventType": RuntimeSessionEventType.PROMPT_SUBMITTED.value,
                    "timestamp": "2026-06-01T00:00:01.000Z",
                    "payload": {
                        "requestId": "req-1",
                        "role": "competitor",
                        "prompt": "SECRET_VALUE",
                    },
                    "parentSessionId": "",
                    "taskId": "task-1",
                    "workerId": "worker-1",
                }
            ],
        }
    )
    child = RuntimeSessionEventLog.from_dict(
        {
            "sessionId": "task:run:test-run-1:runtime:child-1",
            "parentSessionId": "run:test-run-1:runtime",
            "taskId": "child-1",
            "workerId": "worker-child",
            "metadata": {"goal": "Inspect failing test", "runId": "test-run-1", "status": "completed"},
            "createdAt": "2026-06-01T00:00:03.000Z",
            "updatedAt": "2026-06-01T00:00:04.000Z",
            "events": [],
        }
    )
    store = RuntimeSessionEventStore(db_path)
    try:
        store.save(log)
        store.save(child)
    finally:
        store.close()


@pytest.fixture()
def cockpit_env(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    store = _make_store(tmp_path)
    settings = AppSettings(
        db_path=tmp_path / "test.db",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
    )

    app = FastAPI()
    app.state.store = store
    app.state.app_settings = settings
    app.include_router(cockpit_router)
    yield {"client": TestClient(app), "settings": settings}


def test_cockpit_lists_background_sessions(cockpit_env: dict[str, Any]) -> None:
    _persist_background_sessions(cockpit_env["settings"].db_path)

    response = cockpit_env["client"].get("/api/cockpit/background-sessions?limit=5")

    assert response.status_code == 200
    sessions = {session["session_id"]: session for session in response.json()["sessions"]}
    assert sessions["task:run:test-run-1:runtime:child-1"] == {
        "session_id": "task:run:test-run-1:runtime:child-1",
        "runtime_session_id": "task:run:test-run-1:runtime:child-1",
        "run_id": "test-run-1",
        "task_id": "child-1",
        "parent_session_id": "run:test-run-1:runtime",
        "status": "completed",
        "goal": "Inspect failing test",
        "event_count": 0,
        "artifact_count": 0,
        "child_session_count": 0,
        "created_at": "2026-06-01T00:00:03.000Z",
        "updated_at": "2026-06-01T00:00:04.000Z",
        "result_url": "/api/cockpit/background-sessions/task%3Arun%3Atest-run-1%3Aruntime%3Achild-1",
        "runtime_session_url": "/api/cockpit/runtime-sessions/task%3Arun%3Atest-run-1%3Aruntime%3Achild-1",
    }
    assert sessions["run:test-run-1:runtime"] == {
        "session_id": "run:test-run-1:runtime",
        "runtime_session_id": "run:test-run-1:runtime",
        "run_id": "test-run-1",
        "task_id": "task-1",
        "parent_session_id": "",
        "status": "completed",
        "goal": "autoctx run grid_ctf",
        "event_count": 1,
        "artifact_count": 0,
        "child_session_count": 1,
        "created_at": "2026-06-01T00:00:00.000Z",
        "updated_at": "2026-06-01T00:00:02.000Z",
        "result_url": "/api/cockpit/background-sessions/run%3Atest-run-1%3Aruntime",
        "runtime_session_url": "/api/cockpit/runtime-sessions/run%3Atest-run-1%3Aruntime",
    }
    assert sessions["task:queued-1"] | {"created_at": "", "updated_at": ""} == {
        "session_id": "task:queued-1",
        "runtime_session_id": "",
        "run_id": "",
        "task_id": "queued-1",
        "parent_session_id": "",
        "status": "queued",
        "goal": "autoctx run queued",
        "event_count": 0,
        "artifact_count": 0,
        "child_session_count": 0,
        "created_at": "",
        "updated_at": "",
        "result_url": "/api/cockpit/background-sessions/task%3Aqueued-1",
        "runtime_session_url": "",
    }


def test_cockpit_reads_background_session_detail(cockpit_env: dict[str, Any]) -> None:
    _persist_background_sessions(cockpit_env["settings"].db_path)
    session_id = quote("run:test-run-1:runtime", safe="")

    response = cockpit_env["client"].get(f"/api/cockpit/background-sessions/{session_id}")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["summary"]["session_id"] == "run:test-run-1:runtime"
    assert body["summary"]["status"] == "completed"
    assert body["child_sessions"][0]["session_id"] == "task:run:test-run-1:runtime:child-1"
    assert body["normalized_events"] == [
        {
            "event_id": body["normalized_events"][0]["event_id"],
            "session_id": "run:test-run-1:runtime",
            "sequence": 0,
            "ts": body["normalized_events"][0]["ts"],
            "event": "prompt_started",
            "source_event_type": "prompt_submitted",
            "status": "running",
            "title": "Prompt started",
            "payload_summary": {"request_id": "req-1", "role": "competitor"},
        }
    ]
    assert "SECRET_VALUE" not in str(body)


def test_cockpit_reads_queued_task_background_session(cockpit_env: dict[str, Any]) -> None:
    _persist_background_sessions(cockpit_env["settings"].db_path)
    session_id = quote("task:queued-1", safe="")

    response = cockpit_env["client"].get(f"/api/cockpit/background-sessions/{session_id}")

    assert response.status_code == 200
    body: dict[str, Any] = response.json()
    assert body["summary"]["session_id"] == "task:queued-1"
    assert body["summary"]["status"] == "queued"
    assert body["normalized_events"] == []


def test_cockpit_background_session_validation_and_not_found(cockpit_env: dict[str, Any]) -> None:
    invalid_limit = cockpit_env["client"].get("/api/cockpit/background-sessions?limit=0")
    assert invalid_limit.status_code == 422
    assert invalid_limit.json()["detail"] == "limit must be a positive integer"

    missing = cockpit_env["client"].get("/api/cockpit/background-sessions/missing")
    assert missing.status_code == 404
    assert missing.json()["detail"] == {
        "detail": "Background session 'missing' not found",
        "session_id": "missing",
    }
