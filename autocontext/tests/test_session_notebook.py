"""Tests for session notebook: types, storage, API, and prompt injection."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    db = SQLiteStore(tmp_path / "test.db")
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    db.migrate(migrations)
    return db


# ---------------------------------------------------------------------------
# SessionNotebook type
# ---------------------------------------------------------------------------


class TestSessionNotebookType:
    def test_default_fields(self) -> None:
        from autocontext.notebook.types import SessionNotebook

        nb = SessionNotebook(session_id="session-1", scenario_name="grid_ctf")
        assert nb.session_id == "session-1"
        assert nb.scenario_name == "grid_ctf"
        assert nb.current_objective == ""
        assert nb.current_hypotheses == []
        assert nb.best_run_id is None
        assert nb.best_generation is None
        assert nb.best_score is None
        assert nb.unresolved_questions == []
        assert nb.operator_observations == []
        assert nb.follow_ups == []
        assert nb.updated_at == ""
        assert nb.created_at == ""

    def test_custom_fields(self) -> None:
        from autocontext.notebook.types import SessionNotebook

        nb = SessionNotebook(
            session_id="session-1",
            scenario_name="othello",
            current_objective="maximize corner control",
            current_hypotheses=["corners are key", "edges matter"],
            best_run_id="run_123",
            best_generation=5,
            best_score=0.85,
            unresolved_questions=["why does mobility drop?"],
            operator_observations=["model prefers center"],
            follow_ups=["try edge-first strategy"],
        )
        assert nb.scenario_name == "othello"
        assert len(nb.current_hypotheses) == 2
        assert nb.best_score == 0.85
        assert nb.follow_ups == ["try edge-first strategy"]


# ---------------------------------------------------------------------------
# SQLiteStore notebook methods
# ---------------------------------------------------------------------------


class TestNotebookStore:
    def test_upsert_and_get(self, store: SQLiteStore) -> None:
        store.upsert_notebook(
            session_id="session-1",
            scenario_name="grid_ctf",
            current_objective="test objective",
            current_hypotheses=["h1", "h2"],
            best_score=0.75,
        )
        nb = store.get_notebook("session-1")
        assert nb is not None
        assert nb["session_id"] == "session-1"
        assert nb["scenario_name"] == "grid_ctf"
        assert nb["current_objective"] == "test objective"
        assert nb["current_hypotheses"] == ["h1", "h2"]
        assert nb["best_score"] == 0.75

    def test_get_nonexistent(self, store: SQLiteStore) -> None:
        nb = store.get_notebook("nonexistent")
        assert nb is None

    def test_upsert_updates_existing(self, store: SQLiteStore) -> None:
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf", current_objective="first")
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf", current_objective="second")
        nb = store.get_notebook("session-1")
        assert nb is not None
        assert nb["current_objective"] == "second"

    def test_upsert_partial_update(self, store: SQLiteStore) -> None:
        store.upsert_notebook(
            session_id="session-1",
            scenario_name="grid_ctf",
            current_objective="obj1",
            best_score=0.5,
        )
        store.upsert_notebook(
            session_id="session-1",
            scenario_name="grid_ctf",
            best_score=0.9,
        )
        nb = store.get_notebook("session-1")
        assert nb is not None
        assert nb["current_objective"] == "obj1"
        assert nb["best_score"] == 0.9

    def test_list_notebooks(self, store: SQLiteStore) -> None:
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf", current_objective="obj1")
        store.upsert_notebook(session_id="session-2", scenario_name="othello", current_objective="obj2")
        notebooks = store.list_notebooks()
        assert len(notebooks) == 2
        session_ids = {nb["session_id"] for nb in notebooks}
        names = {nb["scenario_name"] for nb in notebooks}
        assert session_ids == {"session-1", "session-2"}
        assert names == {"grid_ctf", "othello"}

    def test_multiple_sessions_can_share_scenario(self, store: SQLiteStore) -> None:
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf", current_objective="obj1")
        store.upsert_notebook(session_id="session-2", scenario_name="grid_ctf", current_objective="obj2")
        notebooks = store.list_notebooks()
        assert len(notebooks) == 2
        assert {nb["session_id"] for nb in notebooks} == {"session-1", "session-2"}
        assert {nb["scenario_name"] for nb in notebooks} == {"grid_ctf"}

    def test_list_notebooks_empty(self, store: SQLiteStore) -> None:
        notebooks = store.list_notebooks()
        assert notebooks == []

    def test_delete_notebook(self, store: SQLiteStore) -> None:
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf", current_objective="obj")
        deleted = store.delete_notebook("session-1")
        assert deleted is True
        assert store.get_notebook("session-1") is None

    def test_delete_nonexistent(self, store: SQLiteStore) -> None:
        deleted = store.delete_notebook("nonexistent")
        assert deleted is False

    def test_json_list_fields_roundtrip(self, store: SQLiteStore) -> None:
        store.upsert_notebook(
            session_id="session-1",
            scenario_name="grid_ctf",
            current_hypotheses=["h1", "h2"],
            unresolved_questions=["q1"],
            operator_observations=["obs1", "obs2"],
            follow_ups=["f1"],
        )
        nb = store.get_notebook("session-1")
        assert nb is not None
        assert nb["current_hypotheses"] == ["h1", "h2"]
        assert nb["unresolved_questions"] == ["q1"]
        assert nb["operator_observations"] == ["obs1", "obs2"]
        assert nb["follow_ups"] == ["f1"]

    def test_timestamps_are_set(self, store: SQLiteStore) -> None:
        store.upsert_notebook(session_id="session-1", scenario_name="grid_ctf")
        nb = store.get_notebook("session-1")
        assert nb is not None
        assert nb["created_at"] != ""
        assert nb["updated_at"] != ""


# ---------------------------------------------------------------------------
# ArtifactStore notebook methods
# ---------------------------------------------------------------------------


class TestNotebookArtifacts:
    @pytest.fixture()
    def artifacts(self, tmp_path: Path) -> object:
        from autocontext.storage.artifacts import ArtifactStore

        return ArtifactStore(
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            claude_skills_path=tmp_path / ".claude" / "skills",
        )

    def test_write_and_read(self, artifacts: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        art = artifacts
        assert isinstance(art, ArtifactStore)
        data = {
            "session_id": "session-1",
            "scenario_name": "grid_ctf",
            "current_objective": "test",
            "current_hypotheses": ["h1"],
        }
        art.write_notebook("session-1", data)
        result = art.read_notebook("session-1")
        assert result is not None
        assert result["current_objective"] == "test"
        assert result["current_hypotheses"] == ["h1"]

    def test_read_nonexistent(self, artifacts: object) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        art = artifacts
        assert isinstance(art, ArtifactStore)
        result = art.read_notebook("nonexistent")
        assert result is None

    def test_write_creates_file(self, artifacts: object, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        art = artifacts
        assert isinstance(art, ArtifactStore)
        art.write_notebook("session-1", {"session_id": "session-1", "scenario_name": "grid_ctf"})
        path = tmp_path / "runs" / "sessions" / "session-1" / "notebook.json"
        assert path.exists()
        content = json.loads(path.read_text(encoding="utf-8"))
        assert content["scenario_name"] == "grid_ctf"

    def test_delete_removes_file(self, artifacts: object, tmp_path: Path) -> None:
        from autocontext.storage.artifacts import ArtifactStore

        art = artifacts
        assert isinstance(art, ArtifactStore)
        art.write_notebook("session-1", {"session_id": "session-1", "scenario_name": "grid_ctf"})
        path = tmp_path / "runs" / "sessions" / "session-1" / "notebook.json"
        assert path.exists()
        art.delete_notebook("session-1")
        assert not path.exists()


# ---------------------------------------------------------------------------
# REST API endpoints
# ---------------------------------------------------------------------------


class TestNotebookAPI:
    @pytest.fixture()
    def client(self, tmp_path: Path) -> TestClient:
        import os

        from autocontext.server.app import create_app

        # Use an isolated temp DB for API tests
        os.environ["AUTOCONTEXT_DB_PATH"] = str(tmp_path / "test.db")
        os.environ["AUTOCONTEXT_RUNS_ROOT"] = str(tmp_path / "runs")
        os.environ["AUTOCONTEXT_KNOWLEDGE_ROOT"] = str(tmp_path / "knowledge")
        os.environ["AUTOCONTEXT_SKILLS_ROOT"] = str(tmp_path / "skills")
        os.environ["AUTOCONTEXT_CLAUDE_SKILLS_PATH"] = str(tmp_path / ".claude" / "skills")
        os.environ["AUTOCONTEXT_EVENT_STREAM_PATH"] = str(tmp_path / "events.ndjson")
        try:
            app = create_app()
            yield TestClient(app)  # type: ignore[misc]
        finally:
            for key in [
                "AUTOCONTEXT_DB_PATH",
                "AUTOCONTEXT_RUNS_ROOT",
                "AUTOCONTEXT_KNOWLEDGE_ROOT",
                "AUTOCONTEXT_SKILLS_ROOT",
                "AUTOCONTEXT_CLAUDE_SKILLS_PATH",
                "AUTOCONTEXT_EVENT_STREAM_PATH",
            ]:
                os.environ.pop(key, None)

    def test_list_empty(self, client: TestClient) -> None:
        resp = client.get("/api/notebooks/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_put_and_get(self, client: TestClient) -> None:
        body = {
            "scenario_name": "grid_ctf",
            "current_objective": "maximize score",
            "current_hypotheses": ["h1"],
            "best_score": 0.8,
        }
        resp = client.put("/api/notebooks/session-1", json=body)
        assert resp.status_code == 200
        data = resp.json()
        assert data["session_id"] == "session-1"
        assert data["scenario_name"] == "grid_ctf"
        assert data["current_objective"] == "maximize score"

        resp = client.get("/api/notebooks/session-1")
        assert resp.status_code == 200
        assert resp.json()["current_objective"] == "maximize score"

    def test_get_nonexistent(self, client: TestClient) -> None:
        resp = client.get("/api/notebooks/nonexistent")
        assert resp.status_code == 404

    def test_delete(self, client: TestClient) -> None:
        client.put("/api/notebooks/session-1", json={"scenario_name": "grid_ctf", "current_objective": "obj"})
        resp = client.delete("/api/notebooks/session-1")
        assert resp.status_code == 200

        resp = client.get("/api/notebooks/session-1")
        assert resp.status_code == 404

    def test_create_requires_scenario_name(self, client: TestClient) -> None:
        resp = client.put("/api/notebooks/session-1", json={"current_objective": "obj"})
        assert resp.status_code == 400

    def test_delete_nonexistent(self, client: TestClient) -> None:
        resp = client.delete("/api/notebooks/nonexistent")
        assert resp.status_code == 404

    def test_put_partial_update(self, client: TestClient) -> None:
        client.put(
            "/api/notebooks/session-1",
            json={"scenario_name": "grid_ctf", "current_objective": "first", "best_score": 0.5},
        )
        client.put("/api/notebooks/session-1", json={"best_score": 0.9})
        resp = client.get("/api/notebooks/session-1")
        assert resp.status_code == 200
        assert resp.json()["current_objective"] == "first"
        assert resp.json()["best_score"] == 0.9

    def test_list_multiple(self, client: TestClient) -> None:
        client.put("/api/notebooks/session-1", json={"scenario_name": "grid_ctf", "current_objective": "obj1"})
        client.put("/api/notebooks/session-2", json={"scenario_name": "othello", "current_objective": "obj2"})
        resp = client.get("/api/notebooks/")
        assert resp.status_code == 200
        names = {nb["scenario_name"] for nb in resp.json()}
        assert names == {"grid_ctf", "othello"}
        session_ids = {nb["session_id"] for nb in resp.json()}
        assert session_ids == {"session-1", "session-2"}


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------


class TestNotebookInjection:
    def test_format_notebook_context(self) -> None:
        from autocontext.notebook.injection import format_notebook_context
        from autocontext.notebook.types import SessionNotebook

        nb = SessionNotebook(
            session_id="session-1",
            scenario_name="grid_ctf",
            current_objective="maximize flag captures",
            current_hypotheses=["corners matter", "speed is key"],
            best_run_id="run_42",
            best_generation=7,
            best_score=0.85,
            unresolved_questions=["why does defense drop?"],
            operator_observations=["model avoids edges"],
            follow_ups=["try aggressive opening"],
        )
        result = format_notebook_context(nb)
        assert "maximize flag captures" in result
        assert "corners matter" in result
        assert "speed is key" in result
        assert "run_42" in result
        assert "0.85" in result
        assert "why does defense drop?" in result
        assert "model avoids edges" in result
        assert "try aggressive opening" in result

    def test_format_empty_notebook(self) -> None:
        from autocontext.notebook.injection import format_notebook_context
        from autocontext.notebook.types import SessionNotebook

        nb = SessionNotebook(session_id="session-1", scenario_name="grid_ctf")
        result = format_notebook_context(nb)
        # Should return something, even for empty notebook
        assert "grid_ctf" in result

    def test_format_partial_notebook(self) -> None:
        from autocontext.notebook.injection import format_notebook_context
        from autocontext.notebook.types import SessionNotebook

        nb = SessionNotebook(
            session_id="session-1",
            scenario_name="othello",
            current_objective="improve corner control",
        )
        result = format_notebook_context(nb)
        assert "improve corner control" in result
        # Should not have best-known state section content if no best score
        assert "othello" in result


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestNotebookSettings:
    def test_notebook_enabled_default(self) -> None:
        from autocontext.config.settings import AppSettings

        settings = AppSettings()
        assert settings.notebook_enabled is True

    def test_notebook_enabled_false(self) -> None:
        import os

        old = os.environ.get("AUTOCONTEXT_NOTEBOOK_ENABLED")
        try:
            os.environ["AUTOCONTEXT_NOTEBOOK_ENABLED"] = "false"
            from autocontext.config.settings import load_settings

            settings = load_settings()
            assert settings.notebook_enabled is False
        finally:
            if old is None:
                os.environ.pop("AUTOCONTEXT_NOTEBOOK_ENABLED", None)
            else:
                os.environ["AUTOCONTEXT_NOTEBOOK_ENABLED"] = old
