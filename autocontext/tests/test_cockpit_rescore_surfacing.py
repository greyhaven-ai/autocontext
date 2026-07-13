"""Recorded re-score surfacing in the HTTP cockpit run_status (AC-885 Slice D2c)."""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient

from autocontext.config.settings import AppSettings
from autocontext.execution.evaluator_epoch import compute_evaluator_epoch
from autocontext.execution.evaluator_epoch_registry import EvaluatorEpochRegistry
from autocontext.server.cockpit_api import cockpit_router
from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _client(tmp_path: Path, store: SQLiteStore) -> TestClient:
    settings = AppSettings(
        db_path=tmp_path / "test.db",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    app = FastAPI()
    app.state.store = store
    app.state.app_settings = settings
    app.include_router(cockpit_router)
    return TestClient(app)


def test_run_status_surfaces_active_epoch_revision(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)

    reg = EvaluatorEpochRegistry(tmp_path / "knowledge" / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    e2 = compute_evaluator_epoch("rub2", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.observe("grid_ctf", e2, now_fn=lambda: "t1")
    reg.activate("grid_ctf", e2.epoch_id)

    store.create_run("run1", "grid_ctf", 1, "local")
    store.upsert_generation("run1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0, evaluator_epoch=e1.epoch_id)
    store.mark_run_completed("run1")

    assert store.record_rescore_revision("run1", 1, 0.55, e2.epoch_id, created_by="jay")

    client = _client(tmp_path, store)
    payload = client.get("/api/cockpit/runs/run1/status").json()

    gen = payload["generations"][0]
    assert gen["has_active_revision"] is True
    assert gen["revised_score"] == 0.55
    assert gen["revised_by"] == "jay"
    assert gen["revised_at"] is not None
    assert gen["best_score"] == 0.50


def test_run_status_no_revision(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)

    reg = EvaluatorEpochRegistry(tmp_path / "knowledge" / "_evaluator_epochs")
    e1 = compute_evaluator_epoch("rub1", "anthropic", "m")
    reg.observe("grid_ctf", e1, now_fn=lambda: "t0")
    reg.activate("grid_ctf", e1.epoch_id)

    store.create_run("run1", "grid_ctf", 1, "local")
    store.upsert_generation("run1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0, evaluator_epoch=e1.epoch_id)
    store.mark_run_completed("run1")

    client = _client(tmp_path, store)
    gen = client.get("/api/cockpit/runs/run1/status").json()["generations"][0]

    assert gen["has_active_revision"] is False
    assert gen["revised_score"] is None
    assert gen["revised_by"] is None
    assert gen["revised_at"] is None
