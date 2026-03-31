"""Tests for shared service layer methods on SQLiteStore (AC-480).

Verifies that common query operations are available as methods on SQLiteStore,
so CLI/HTTP/MCP surfaces don't duplicate raw SQL.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    db = SQLiteStore(tmp_path / "test.sqlite3")
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    if migrations.exists():
        db.migrate(migrations)
    return db


class TestListRuns:
    def test_list_runs_empty(self, store: SQLiteStore) -> None:
        result = store.list_runs()
        assert result == []

    def test_list_runs_returns_recent(self, store: SQLiteStore) -> None:
        store.create_run("run-1", "grid_ctf", 5, "local")
        store.create_run("run-2", "othello", 3, "local")
        runs = store.list_runs()
        assert len(runs) == 2
        assert all("run_id" in r for r in runs)
        assert all("scenario" in r for r in runs)

    def test_list_runs_respects_limit(self, store: SQLiteStore) -> None:
        for i in range(5):
            store.create_run(f"run-{i}", "grid_ctf", 1, "local")
        runs = store.list_runs(limit=3)
        assert len(runs) == 3


class TestRunStatus:
    def test_run_status_missing(self, store: SQLiteStore) -> None:
        result = store.run_status("nonexistent")
        assert result == []

    def test_run_status_preserves_generation_status_fields(self, store: SQLiteStore) -> None:
        store.create_run("run-1", "grid_ctf", 3, "local")
        store.upsert_generation("run-1", 1, 0.40, 0.50, 1000.0, 2, 1, "advance", "completed")
        store.upsert_generation("run-1", 2, 0.45, 0.55, 1010.0, 3, 2, "retry", "running")
        result = store.run_status("run-1")
        assert result == [
            {
                "generation_index": 1,
                "mean_score": 0.40,
                "best_score": 0.50,
                "elo": 1000.0,
                "wins": 2,
                "losses": 1,
                "gate_decision": "advance",
                "status": "completed",
            },
            {
                "generation_index": 2,
                "mean_score": 0.45,
                "best_score": 0.55,
                "elo": 1010.0,
                "wins": 3,
                "losses": 2,
                "gate_decision": "retry",
                "status": "running",
            },
        ]


class TestListSolved:
    def test_list_solved_empty(self, store: SQLiteStore) -> None:
        result = store.list_solved()
        assert result == []

    def test_list_solved_returns_best_snapshots(self, store: SQLiteStore) -> None:
        store.create_run("run-1", "grid_ctf", 1, "local")
        store.save_knowledge_snapshot(
            scenario="grid_ctf",
            run_id="run-1",
            best_score=0.9,
            best_elo=1500.0,
            playbook_hash="abc123",
        )
        result = store.list_solved()
        assert len(result) >= 1
        assert result[0]["scenario"] == "grid_ctf"
