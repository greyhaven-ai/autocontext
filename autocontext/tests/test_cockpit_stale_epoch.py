"""Stale-epoch surfacing in the HTTP cockpit run_status (AC-885 Slice D1)."""

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


def test_run_status_surfaces_stale_epoch(tmp_path: Path) -> None:
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

    client = _client(tmp_path, store)
    resp = client.get("/api/cockpit/runs/run1/status")
    assert resp.status_code == 200
    payload = resp.json()

    assert payload["active_evaluator_epoch"] == e2.epoch_id
    gen = payload["generations"][0]
    assert gen["evaluator_epoch"] == e1.epoch_id
    assert gen["evaluator_epoch_status"] == "stale"
    assert gen["quarantined"] in (False, True)

    warnings = payload["warnings"]
    stale = [w for w in warnings if w["warning_type"] == "stale_epoch"]
    assert len(stale) == 1
    assert stale[0]["generation"] == 1
    assert stale[0]["evaluator_epoch"] == e1.epoch_id
    assert stale[0]["active_evaluator_epoch"] == e2.epoch_id


def test_run_status_no_active_epoch(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "test.db")
    store.migrate(MIGRATIONS_DIR)

    store.create_run("run1", "grid_ctf", 1, "local")
    store.upsert_generation("run1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed", 30.0)
    store.mark_run_completed("run1")

    client = _client(tmp_path, store)
    payload = client.get("/api/cockpit/runs/run1/status").json()

    assert payload["active_evaluator_epoch"] is None
    assert payload["generations"][0]["evaluator_epoch_status"] == "no_active_epoch"
    assert payload["warnings"] == []
