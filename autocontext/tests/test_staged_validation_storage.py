"""Tests for AC-200: Staged validation result persistence in SQLite.

Tests insert_staged_validation_results and get_staged_validation_results
round-trip through a real SQLite database.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def sqlite_store(tmp_path: Path) -> SQLiteStore:
    """Create a SQLiteStore with migrations applied."""
    store = SQLiteStore(tmp_path / "test.db")
    migrations_dir = Path(__file__).parent.parent / "migrations"
    store.migrate(migrations_dir)
    # Seed a run and generation for FK constraints
    store.create_run("run-1", "grid_ctf", 3, "local")
    store.upsert_generation("run-1", 1, 0.5, 0.7, 1000.0, 1, 0, "advance", "completed")
    return store


class TestInsertStagedValidationResults:
    def test_insert_single_result(self, sqlite_store: SQLiteStore) -> None:
        results = [
            {
                "stage_order": 0,
                "stage_name": "syntax",
                "status": "passed",
                "duration_ms": 0.5,
                "error": None,
                "error_code": None,
            },
        ]
        sqlite_store.insert_staged_validation_results("run-1", 1, results)

        rows = sqlite_store.get_staged_validation_results("run-1", 1)
        assert len(rows) == 1
        assert rows[0]["stage_name"] == "syntax"
        assert rows[0]["status"] == "passed"

    def test_insert_multiple_results(self, sqlite_store: SQLiteStore) -> None:
        results = [
            {
                "stage_order": 0,
                "stage_name": "syntax",
                "status": "passed",
                "duration_ms": 0.3,
                "error": None,
                "error_code": None,
            },
            {
                "stage_order": 1,
                "stage_name": "contract",
                "status": "failed",
                "duration_ms": 1.2,
                "error": "missing choose_action",
                "error_code": "missing_entry_point",
            },
        ]
        sqlite_store.insert_staged_validation_results("run-1", 1, results)

        rows = sqlite_store.get_staged_validation_results("run-1", 1)
        assert len(rows) == 2
        assert rows[0]["stage_name"] == "syntax"
        assert rows[1]["stage_name"] == "contract"
        assert rows[1]["error"] == "missing choose_action"
        assert rows[1]["error_code"] == "missing_entry_point"

    def test_insert_empty_results(self, sqlite_store: SQLiteStore) -> None:
        sqlite_store.insert_staged_validation_results("run-1", 1, [])
        rows = sqlite_store.get_staged_validation_results("run-1", 1)
        assert rows == []

    def test_results_ordered_by_stage(self, sqlite_store: SQLiteStore) -> None:
        results = [
            {
                "stage_order": 2,
                "stage_name": "deterministic",
                "status": "skipped",
                "duration_ms": 0.0,
                "error": None,
                "error_code": None,
            },
            {
                "stage_order": 0,
                "stage_name": "syntax",
                "status": "passed",
                "duration_ms": 0.1,
                "error": None,
                "error_code": None,
            },
            {
                "stage_order": 1,
                "stage_name": "contract",
                "status": "passed",
                "duration_ms": 0.2,
                "error": None,
                "error_code": None,
            },
        ]
        sqlite_store.insert_staged_validation_results("run-1", 1, results)

        rows = sqlite_store.get_staged_validation_results("run-1", 1)
        assert [r["stage_order"] for r in rows] == [0, 1, 2]

    def test_duration_ms_preserved(self, sqlite_store: SQLiteStore) -> None:
        results = [
            {
                "stage_order": 0,
                "stage_name": "syntax",
                "status": "passed",
                "duration_ms": 42.75,
                "error": None,
                "error_code": None,
            },
        ]
        sqlite_store.insert_staged_validation_results("run-1", 1, results)
        rows = sqlite_store.get_staged_validation_results("run-1", 1)
        assert rows[0]["duration_ms"] == 42.75

    def test_no_results_for_nonexistent_generation(self, sqlite_store: SQLiteStore) -> None:
        rows = sqlite_store.get_staged_validation_results("run-1", 99)
        assert rows == []
