"""Tests for AC-210: Operator cockpit — read-only review over existing artifacts."""

from __future__ import annotations

import os
from collections.abc import Generator
from pathlib import Path
from typing import Any

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.server.changelog import build_changelog
from autocontext.server.cockpit_api import cockpit_router
from autocontext.server.writeup import generate_writeup
from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"

ENV_KEYS = [
    "AUTOCONTEXT_DB_PATH",
    "AUTOCONTEXT_RUNS_ROOT",
    "AUTOCONTEXT_KNOWLEDGE_ROOT",
    "AUTOCONTEXT_SKILLS_ROOT",
    "AUTOCONTEXT_CLAUDE_SKILLS_PATH",
]


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


def _seed_agent_outputs(store: SQLiteStore, run_id: str = "run1") -> None:
    """Add agent outputs including architect and competitor."""
    store.append_agent_output(run_id, 1, "competitor", '{"aggression": 0.5}')
    store.append_agent_output(run_id, 1, "analyst", "Gen 1 analysis: baseline strategy.")
    store.append_agent_output(run_id, 2, "competitor", '{"aggression": 0.7}')
    store.append_agent_output(run_id, 2, "architect", '[{"name": "tool_a", "code": "pass"}]')
    store.append_agent_output(run_id, 3, "competitor", '{"aggression": 0.9}')


@pytest.fixture()
def cockpit_env(tmp_path: Path) -> Generator[dict[str, Any], None, None]:
    """Set env vars for cockpit API and yield store, artifacts, client."""
    os.environ["AUTOCONTEXT_DB_PATH"] = str(tmp_path / "test.db")
    os.environ["AUTOCONTEXT_RUNS_ROOT"] = str(tmp_path / "runs")
    os.environ["AUTOCONTEXT_KNOWLEDGE_ROOT"] = str(tmp_path / "knowledge")
    os.environ["AUTOCONTEXT_SKILLS_ROOT"] = str(tmp_path / "skills")
    os.environ["AUTOCONTEXT_CLAUDE_SKILLS_PATH"] = str(tmp_path / ".claude" / "skills")

    store = _make_store(tmp_path)
    artifacts = _make_artifacts(tmp_path)

    app = FastAPI()
    app.include_router(cockpit_router)
    client = TestClient(app)

    yield {"store": store, "artifacts": artifacts, "client": client, "tmp_path": tmp_path}

    for key in ENV_KEYS:
        os.environ.pop(key, None)


# ---------------------------------------------------------------------------
# Writeup generation
# ---------------------------------------------------------------------------


class TestWriteup:
    def test_generates_markdown(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert isinstance(result, str)
        assert "run1" in result
        assert "# Run Summary" in result

    def test_includes_score_trajectory(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert "Score Trajectory" in result
        assert "0.50" in result or "0.5" in result

    def test_includes_gate_decisions(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = generate_writeup("run1", store, artifacts)
        assert "advance" in result

    def test_includes_playbook_excerpt(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        playbook_dir = tmp_path / "knowledge" / "grid_ctf"
        playbook_dir.mkdir(parents=True)
        (playbook_dir / "playbook.md").write_text("# Evolved Playbook\n\nUse flanking.", encoding="utf-8")
        result = generate_writeup("run1", store, artifacts)
        assert "Playbook" in result
        assert "flanking" in result

    def test_empty_run(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        store.create_run("empty", "grid_ctf", 3, "local")
        result = generate_writeup("empty", store, artifacts)
        assert "empty" in result

    def test_includes_best_strategy(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        _seed_agent_outputs(store)
        result = generate_writeup("run1", store, artifacts)
        assert "Best Strategy" in result or "Strategy" in result


# ---------------------------------------------------------------------------
# Changelog builder
# ---------------------------------------------------------------------------


class TestChangelog:
    def test_builds_changelog(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        assert result["run_id"] == "run1"
        assert isinstance(result["generations"], list)
        assert len(result["generations"]) >= 2

    def test_score_deltas(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        gens = result["generations"]
        gen2 = next(g for g in gens if g["generation"] == 2)
        assert abs(gen2["score_delta"] - 0.15) < 0.01

    def test_elo_deltas(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        gens = result["generations"]
        gen2 = next(g for g in gens if g["generation"] == 2)
        assert abs(gen2["elo_delta"] - 50.0) < 0.01

    def test_gate_decision_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        for gen in result["generations"]:
            assert "gate_decision" in gen

    def test_empty_run(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        store.create_run("empty", "grid_ctf", 3, "local")
        result = build_changelog("empty", store, artifacts)
        assert result["run_id"] == "empty"
        assert result["generations"] == []

    def test_new_tools_detected(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        _seed_agent_outputs(store)
        result = build_changelog("run1", store, artifacts)
        gen2 = next(g for g in result["generations"] if g["generation"] == 2)
        assert "new_tools" in gen2

    def test_duration_included(self, tmp_path: Path) -> None:
        store = _make_store(tmp_path)
        artifacts = _make_artifacts(tmp_path)
        _seed_run(store)
        result = build_changelog("run1", store, artifacts)
        for gen in result["generations"]:
            assert "duration_seconds" in gen


# ---------------------------------------------------------------------------
# Cockpit API endpoints
# ---------------------------------------------------------------------------


class TestCockpitRunsEndpoint:
    def test_list_runs(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1
        run = data[0]
        assert run["run_id"] == "run1"
        assert run["scenario_name"] == "grid_ctf"
        assert run["generations_completed"] == 3
        assert run["best_score"] == 0.80
        assert run["status"] == "completed"

    def test_list_runs_empty(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/runs")
        assert resp.status_code == 200
        assert resp.json() == []


class TestCockpitRunStatus:
    def test_run_status(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["scenario_name"] == "grid_ctf"
        assert isinstance(data["generations"], list)
        assert len(data["generations"]) == 3
        gen1 = data["generations"][0]
        assert gen1["generation"] == 1
        assert gen1["mean_score"] == 0.40
        assert gen1["best_score"] == 0.50
        assert gen1["elo"] == 1000.0
        assert gen1["gate_decision"] == "advance"


class TestCockpitChangelog:
    def test_changelog_endpoint(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/changelog")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert isinstance(data["generations"], list)


class TestCockpitCompare:
    def test_compare_generations(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/compare/1/3")
        assert resp.status_code == 200
        data = resp.json()
        assert data["gen_a"]["generation"] == 1
        assert data["gen_b"]["generation"] == 3
        assert abs(data["score_delta"] - 0.30) < 0.01
        assert abs(data["elo_delta"] - 100.0) < 0.01

    def test_compare_nonexistent_generation(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/compare/1/99")
        assert resp.status_code == 404


class TestCockpitResume:
    def test_resume_completed_run(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/runs/run1/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["status"] == "completed"
        assert data["last_generation"] == 3
        assert data["can_resume"] is False

    def test_resume_running_run(self, cockpit_env: dict[str, Any]) -> None:
        store = cockpit_env["store"]
        store.create_run("running1", "grid_ctf", 5, "local")
        store.upsert_generation("running1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)
        store.upsert_generation("running1", 2, 0.55, 0.65, 1050.0, 3, 0, "advance", "completed", 45.0)
        resp = cockpit_env["client"].get("/api/cockpit/runs/running1/resume")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "running1"
        assert data["status"] == "running"
        assert data["last_generation"] == 2
        assert data["can_resume"] is True

    def test_resume_nonexistent_run(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/runs/nonexistent/resume")
        assert resp.status_code == 404


class TestCockpitWriteup:
    def test_writeup_endpoint(self, cockpit_env: dict[str, Any]) -> None:
        _seed_run(cockpit_env["store"])
        resp = cockpit_env["client"].get("/api/cockpit/writeup/run1")
        assert resp.status_code == 200
        data = resp.json()
        assert data["run_id"] == "run1"
        assert data["scenario_name"] == "grid_ctf"
        assert isinstance(data["writeup_markdown"], str)
        assert "# Run Summary" in data["writeup_markdown"]

    def test_writeup_nonexistent_run(self, cockpit_env: dict[str, Any]) -> None:
        resp = cockpit_env["client"].get("/api/cockpit/writeup/nonexistent")
        assert resp.status_code == 404
