from __future__ import annotations

from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.server.cockpit_api import cockpit_router
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)
    return store


def _seed_run(store: SQLiteStore, run_id: str = "test-run-1") -> None:
    store.create_run(run_id, "grid_ctf", 3, "local")
    store.upsert_generation(run_id, 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)


def _persist_runtime_session(db_path: Path) -> None:
    from autocontext.session.runtime_events import RuntimeSessionEventLog, RuntimeSessionEventStore, RuntimeSessionEventType

    log = RuntimeSessionEventLog.create(
        session_id="run:test-run-1:runtime",
        metadata={"goal": "autoctx run grid_ctf", "runId": "test-run-1"},
    )
    prompt = log.append(
        RuntimeSessionEventType.PROMPT_SUBMITTED,
        {"requestId": "req-1", "role": "competitor", "prompt": "Improve grid strategy", "cwd": "/workspace"},
    )
    log.append(
        RuntimeSessionEventType.ASSISTANT_MESSAGE,
        {
            "requestId": "req-1",
            "promptEventId": prompt.event_id,
            "role": "competitor",
            "text": "Try a safer path bias",
            "cwd": "/workspace",
        },
    )
    store = RuntimeSessionEventStore(db_path)
    try:
        store.save(log)
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
    client = TestClient(app)

    yield {"store": store, "client": client, "settings": settings}


def test_cockpit_lists_and_reads_runtime_sessions(cockpit_env: dict[str, Any]) -> None:
    _persist_runtime_session(cockpit_env["settings"].db_path)
    client: TestClient = cockpit_env["client"]

    listed = client.get("/api/cockpit/runtime-sessions?limit=5")
    assert listed.status_code == 200
    assert listed.json()["sessions"] == [
        {
            "session_id": "run:test-run-1:runtime",
            "parent_session_id": "",
            "task_id": "",
            "worker_id": "",
            "goal": "autoctx run grid_ctf",
            "event_count": 2,
            "created_at": listed.json()["sessions"][0]["created_at"],
            "updated_at": listed.json()["sessions"][0]["updated_at"],
        }
    ]

    by_session = client.get("/api/cockpit/runtime-sessions/run%3Atest-run-1%3Aruntime")
    assert by_session.status_code == 200
    assert by_session.json()["sessionId"] == "run:test-run-1:runtime"
    assert by_session.json()["events"][0]["payload"]["requestId"] == "req-1"

    by_run = client.get("/api/cockpit/runs/test-run-1/runtime-session")
    assert by_run.status_code == 200
    assert by_run.json()["sessionId"] == "run:test-run-1:runtime"


def test_cockpit_returns_runtime_session_timeline(cockpit_env: dict[str, Any]) -> None:
    _persist_runtime_session(cockpit_env["settings"].db_path)

    response = cockpit_env["client"].get("/api/cockpit/runs/test-run-1/runtime-session/timeline")

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["session_id"] == "run:test-run-1:runtime"
    assert body["items"][0]["kind"] == "prompt"
    assert body["items"][0]["request_id"] == "req-1"
    assert body["items"][0]["response_preview"] == "Try a safer path bias"


def test_cockpit_run_views_include_runtime_session_discovery(cockpit_env: dict[str, Any]) -> None:
    _seed_run(cockpit_env["store"])
    _persist_runtime_session(cockpit_env["settings"].db_path)
    client: TestClient = cockpit_env["client"]

    runs = client.get("/api/cockpit/runs")
    assert runs.status_code == 200
    run = runs.json()[0]
    assert run["runtime_session"]["session_id"] == "run:test-run-1:runtime"
    assert run["runtime_session_url"] == "/api/cockpit/runs/test-run-1/runtime-session"

    status = client.get("/api/cockpit/runs/test-run-1/status")
    assert status.status_code == 200
    assert status.json()["runtime_session"]["event_count"] == 2
    assert status.json()["runtime_session_url"] == "/api/cockpit/runs/test-run-1/runtime-session"


def test_cockpit_runtime_session_missing_run_returns_404(cockpit_env: dict[str, Any]) -> None:
    response = cockpit_env["client"].get("/api/cockpit/runs/missing/runtime-session")

    assert response.status_code == 404
    assert response.json()["detail"]["session_id"] == "run:missing:runtime"
