"""Tests for AC-219: Wire notebook editing into cockpit.

Strict TDD — these tests were written before the production code.
They exercise the cockpit notebook CRUD endpoints and verify:
  - Create, read, list, update, delete
  - 404 / 400 error handling
  - Session isolation
  - Filesystem sync (notebook.json created/deleted)
  - Existing read-only cockpit endpoints still work
"""

from __future__ import annotations

import json
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.server.cockpit_api import cockpit_router
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)
    return store


def _make_artifacts(tmp_path: Path) -> ArtifactStore:
    return ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )


def _seed_run(store: SQLiteStore, run_id: str = "run1", scenario: str = "grid_ctf", gens: int = 3) -> None:
    """Create a run with completed generations for testing."""
    store.create_run(run_id, scenario, gens, "local")
    store.upsert_generation(run_id, 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)
    store.upsert_generation(run_id, 2, 0.55, 0.65, 1050.0, 3, 0, "advance", "completed", 45.0)
    store.upsert_generation(run_id, 3, 0.70, 0.80, 1100.0, 4, 1, "advance", "completed", 60.0)
    store.mark_run_completed(run_id)


@pytest.fixture()
def cockpit_env(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Build an app with explicit state-backed store/settings for cockpit API."""
    store = _make_store(tmp_path)
    artifacts = _make_artifacts(tmp_path)
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

    yield {"store": store, "artifacts": artifacts, "client": client, "tmp_path": tmp_path, "settings": settings}


# ---------------------------------------------------------------------------
# Create notebook via PUT
# ---------------------------------------------------------------------------


class TestCockpitCreateNotebook:
    def test_create_notebook(self, cockpit_env: dict[str, Any]) -> None:
        """PUT with scenario_name creates a new notebook."""
        resp = cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-1",
            json={"scenario_name": "grid_ctf", "current_objective": "Maximize flag captures"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-1"
        assert data["scenario_name"] == "grid_ctf"
        assert data["current_objective"] == "Maximize flag captures"

    def test_create_without_scenario_name_400(self, cockpit_env: dict[str, Any]) -> None:
        """PUT without scenario_name on new notebook returns 400."""
        resp = cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-new",
            json={"current_objective": "Something"},
        )
        assert resp.status_code == 400
        assert "scenario_name" in resp.json()["detail"].lower()

    def test_create_with_all_fields(self, cockpit_env: dict[str, Any]) -> None:
        """PUT with all fields populates them correctly."""
        resp = cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-full",
            json={
                "scenario_name": "othello",
                "current_objective": "Win more games",
                "current_hypotheses": ["flanking works", "corners matter"],
                "best_run_id": "run-42",
                "best_generation": 5,
                "best_score": 0.85,
                "unresolved_questions": ["Why does edge strategy fail?"],
                "operator_observations": ["Model struggles with endgame"],
                "follow_ups": ["Try deeper search"],
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenario_name"] == "othello"
        assert data["current_hypotheses"] == ["flanking works", "corners matter"]
        assert data["best_run_id"] == "run-42"
        assert data["best_generation"] == 5
        assert data["best_score"] == 0.85
        assert data["unresolved_questions"] == ["Why does edge strategy fail?"]
        assert data["operator_observations"] == ["Model struggles with endgame"]
        assert data["follow_ups"] == ["Try deeper search"]


# ---------------------------------------------------------------------------
# Read notebook via GET
# ---------------------------------------------------------------------------


class TestCockpitGetNotebook:
    def test_get_existing_notebook(self, cockpit_env: dict[str, Any]) -> None:
        """GET returns notebook created via PUT."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-r",
            json={"scenario_name": "grid_ctf", "current_objective": "Test read"},
        )
        resp = cockpit_env["client"].get("/api/cockpit/notebooks/sess-r")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-r"
        assert data["current_objective"] == "Test read"

    def test_get_nonexistent_notebook_404(self, cockpit_env: dict[str, Any]) -> None:
        """GET for unknown session_id returns 404."""
        resp = cockpit_env["client"].get("/api/cockpit/notebooks/does-not-exist")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()

    def test_get_effective_context_preview(self, cockpit_env: dict[str, Any]) -> None:
        """GET effective-context returns the role-specific preview and warnings."""
        _seed_run(cockpit_env["store"], run_id="sess-preview")
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-preview",
            json={
                "scenario_name": "grid_ctf",
                "current_objective": "Stabilize defense",
                "current_hypotheses": ["Lower aggression should reduce variance"],
                "best_score": 0.50,
                "operator_observations": ["Analyst keeps recommending risky offense"],
            },
        )

        resp = cockpit_env["client"].get("/api/cockpit/notebooks/sess-preview/effective-context")
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "sess-preview"
        assert "competitor" in data["role_contexts"]
        assert "Stabilize defense" in data["role_contexts"]["competitor"]
        assert any(w["warning_type"] == "stale_score" for w in data["warnings"])


# ---------------------------------------------------------------------------
# List notebooks via GET
# ---------------------------------------------------------------------------


class TestCockpitListNotebooks:
    def test_list_empty(self, cockpit_env: dict[str, Any]) -> None:
        """GET /notebooks with no data returns empty list."""
        resp = cockpit_env["client"].get("/api/cockpit/notebooks")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_multiple(self, cockpit_env: dict[str, Any]) -> None:
        """GET /notebooks returns all created notebooks."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-a",
            json={"scenario_name": "grid_ctf"},
        )
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-b",
            json={"scenario_name": "othello"},
        )
        resp = cockpit_env["client"].get("/api/cockpit/notebooks")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 2
        ids = {nb["session_id"] for nb in data}
        assert ids == {"sess-a", "sess-b"}


# ---------------------------------------------------------------------------
# Update (partial) via PUT
# ---------------------------------------------------------------------------


class TestCockpitUpdateNotebook:
    def test_update_existing_notebook(self, cockpit_env: dict[str, Any]) -> None:
        """PUT on existing notebook updates fields; scenario_name inherited."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-u",
            json={"scenario_name": "grid_ctf", "current_objective": "Original"},
        )
        resp = cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-u",
            json={"current_objective": "Updated objective"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["scenario_name"] == "grid_ctf"  # inherited
        assert data["current_objective"] == "Updated objective"

    def test_update_score_fields(self, cockpit_env: dict[str, Any]) -> None:
        """PUT can update best_run_id, best_generation, best_score."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-s",
            json={"scenario_name": "grid_ctf"},
        )
        resp = cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-s",
            json={"best_run_id": "run-99", "best_generation": 10, "best_score": 0.95},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["best_run_id"] == "run-99"
        assert data["best_generation"] == 10
        assert data["best_score"] == 0.95


# ---------------------------------------------------------------------------
# Delete notebook via DELETE
# ---------------------------------------------------------------------------


class TestCockpitDeleteNotebook:
    def test_delete_existing_notebook(self, cockpit_env: dict[str, Any]) -> None:
        """DELETE removes a notebook and returns confirmation."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-d",
            json={"scenario_name": "grid_ctf"},
        )
        resp = cockpit_env["client"].delete("/api/cockpit/notebooks/sess-d")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "deleted"
        assert data["session_id"] == "sess-d"

        # Confirm it's gone
        resp2 = cockpit_env["client"].get("/api/cockpit/notebooks/sess-d")
        assert resp2.status_code == 404

    def test_delete_nonexistent_notebook_404(self, cockpit_env: dict[str, Any]) -> None:
        """DELETE for unknown session_id returns 404."""
        resp = cockpit_env["client"].delete("/api/cockpit/notebooks/ghost")
        assert resp.status_code == 404
        assert "not found" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Session isolation
# ---------------------------------------------------------------------------


class TestCockpitNotebookIsolation:
    def test_session_isolation(self, cockpit_env: dict[str, Any]) -> None:
        """Two different session_ids maintain independent data."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-x",
            json={"scenario_name": "grid_ctf", "current_objective": "Objective X"},
        )
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-y",
            json={"scenario_name": "othello", "current_objective": "Objective Y"},
        )

        resp_x = cockpit_env["client"].get("/api/cockpit/notebooks/sess-x")
        resp_y = cockpit_env["client"].get("/api/cockpit/notebooks/sess-y")

        assert resp_x.json()["scenario_name"] == "grid_ctf"
        assert resp_x.json()["current_objective"] == "Objective X"
        assert resp_y.json()["scenario_name"] == "othello"
        assert resp_y.json()["current_objective"] == "Objective Y"

    def test_delete_does_not_affect_other_sessions(self, cockpit_env: dict[str, Any]) -> None:
        """Deleting one notebook does not affect another."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-keep",
            json={"scenario_name": "grid_ctf"},
        )
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-remove",
            json={"scenario_name": "othello"},
        )

        cockpit_env["client"].delete("/api/cockpit/notebooks/sess-remove")

        resp_keep = cockpit_env["client"].get("/api/cockpit/notebooks/sess-keep")
        resp_removed = cockpit_env["client"].get("/api/cockpit/notebooks/sess-remove")

        assert resp_keep.status_code == 200
        assert resp_removed.status_code == 404


# ---------------------------------------------------------------------------
# Filesystem sync
# ---------------------------------------------------------------------------


class TestCockpitNotebookFilesystemSync:
    def test_put_creates_notebook_json(self, cockpit_env: dict[str, Any]) -> None:
        """PUT syncs notebook to filesystem as notebook.json."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-fs",
            json={"scenario_name": "grid_ctf", "current_objective": "FS test"},
        )
        nb_path = cockpit_env["tmp_path"] / "runs" / "sessions" / "sess-fs" / "notebook.json"
        assert nb_path.exists(), f"Expected notebook.json at {nb_path}"
        data = json.loads(nb_path.read_text(encoding="utf-8"))
        assert data["session_id"] == "sess-fs"
        assert data["current_objective"] == "FS test"

    def test_delete_removes_notebook_json(self, cockpit_env: dict[str, Any]) -> None:
        """DELETE removes the notebook.json from filesystem."""
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-fsd",
            json={"scenario_name": "grid_ctf"},
        )
        nb_path = cockpit_env["tmp_path"] / "runs" / "sessions" / "sess-fsd" / "notebook.json"
        assert nb_path.exists()

        cockpit_env["client"].delete("/api/cockpit/notebooks/sess-fsd")
        assert not nb_path.exists()


# ---------------------------------------------------------------------------
# Existing read-only cockpit endpoints still work
# ---------------------------------------------------------------------------


class TestCockpitReadOnlyEndpointsUnchanged:
    def test_list_runs_still_works(self, cockpit_env: dict[str, Any]) -> None:
        """GET /runs returns 200 alongside new notebook endpoints."""
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) >= 1
        assert data[0]["run_id"] == "run1"

    def test_run_status_still_works(self, cockpit_env: dict[str, Any]) -> None:
        """GET /runs/{id}/status returns 200."""
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert len(data["generations"]) == 3

    def test_resume_still_works(self, cockpit_env: dict[str, Any]) -> None:
        """GET /runs/{id}/resume returns 200."""
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/resume")
        assert resp.status_code == 200
        assert resp.json()["run_id"] == "run1"

    def test_resume_includes_effective_notebook_context_when_available(self, cockpit_env: dict[str, Any]) -> None:
        """Resume payload exposes the notebook context that will be carried forward."""
        _seed_run(cockpit_env["store"], run_id="run-notebook")
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/run-notebook",
            json={
                "scenario_name": "grid_ctf",
                "current_objective": "Resume from the strongest defensive line",
                "follow_ups": ["Retry with lower aggression before exploring offense"],
            },
        )

        resp = cockpit_env["client"].get("/api/cockpit/runs/run-notebook/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["effective_notebook_context"] is not None
        competitor_ctx = data["effective_notebook_context"]["role_contexts"]["competitor"]
        assert "Resume from the strongest defensive line" in competitor_ctx

    def test_compare_still_works(self, cockpit_env: dict[str, Any]) -> None:
        """GET /runs/{id}/compare/{a}/{b} returns 200."""
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/compare/1/2")
        assert resp.status_code == 200

    def test_writeup_still_works(self, cockpit_env: dict[str, Any]) -> None:
        """GET /writeup/{id} returns 200."""
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/writeup/run1")
        assert resp.status_code == 200
        assert "writeup_markdown" in resp.json()


# ---------------------------------------------------------------------------
# Event emission
# ---------------------------------------------------------------------------


class TestCockpitNotebookEventEmission:
    def test_put_emits_event(self, cockpit_env: dict[str, Any]) -> None:
        """PUT writes a notebook_updated event to the event stream."""
        event_path = cockpit_env["tmp_path"] / "runs" / "events.ndjson"

        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-ev",
            json={"scenario_name": "grid_ctf"},
        )

        assert event_path.exists(), "Event stream file should exist after PUT"
        lines = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        assert len(lines) >= 1
        event = json.loads(lines[-1])
        assert event["event"] == "notebook_updated"
        assert event["payload"]["session_id"] == "sess-ev"
        assert event["payload"]["source"] == "cockpit"

    def test_delete_emits_event(self, cockpit_env: dict[str, Any]) -> None:
        """DELETE writes a notebook_deleted event to the event stream."""
        event_path = cockpit_env["tmp_path"] / "runs" / "events.ndjson"
        cockpit_env["client"].put(
            "/api/cockpit/notebooks/sess-del-ev",
            json={"scenario_name": "grid_ctf"},
        )

        resp = cockpit_env["client"].delete("/api/cockpit/notebooks/sess-del-ev")
        assert resp.status_code == 200
        lines = [line for line in event_path.read_text(encoding="utf-8").splitlines() if line.strip()]
        event = json.loads(lines[-1])
        assert event["event"] == "notebook_deleted"
        assert event["payload"]["session_id"] == "sess-del-ev"
        assert event["payload"]["source"] == "cockpit"
