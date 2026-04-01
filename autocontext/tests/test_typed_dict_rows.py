"""Tests for TypedDict row types in SQLite store (AC-485).

Verifies that core query methods return properly typed dicts
instead of untyped dict[str, Any].
"""

from __future__ import annotations

from pathlib import Path
from typing import get_type_hints

import pytest

from autocontext.storage.sqlite_store import SQLiteStore


@pytest.fixture()
def store(tmp_path: Path) -> SQLiteStore:
    db = SQLiteStore(tmp_path / "test.sqlite3")
    migrations = Path(__file__).resolve().parent.parent / "migrations"
    if migrations.exists():
        db.migrate(migrations)
    return db


class TestRunRowTypedDict:
    """list_runs and get_run should return RunRow typed dicts."""

    def test_list_runs_returns_typed_rows(self, store: SQLiteStore) -> None:
        from autocontext.storage.row_types import RunRow

        store.create_run("r1", "grid_ctf", 5, "local")
        runs = store.list_runs()
        assert len(runs) == 1
        row = runs[0]
        # Verify all RunRow keys are present
        for key in RunRow.__annotations__:
            assert key in row, f"Missing key '{key}' in list_runs result"

    def test_get_run_returns_typed_row(self, store: SQLiteStore) -> None:
        from autocontext.storage.row_types import RunRow

        store.create_run("r1", "grid_ctf", 5, "local")
        row = store.get_run("r1")
        assert row is not None
        for key in RunRow.__annotations__:
            assert key in row, f"Missing key '{key}' in get_run result"


class TestGenerationRowTypedDict:
    """Generation query methods should return typed rows."""

    def test_get_generation_metrics_returns_typed_rows(self, store: SQLiteStore) -> None:
        from autocontext.storage.row_types import GenerationMetricsRow

        store.create_run("r1", "grid_ctf", 3, "local")
        store.upsert_generation(
            run_id="r1", generation_index=0, mean_score=0.5,
            best_score=0.6, elo=1500.0, wins=3, losses=2,
            gate_decision="advance", status="completed",
        )
        rows = store.get_generation_metrics("r1")
        assert len(rows) == 1
        for key in GenerationMetricsRow.__annotations__:
            assert key in rows[0], f"Missing key '{key}' in generation metrics"


class TestRowTypesModuleExists:
    """The row_types module should define TypedDicts for all core tables."""

    def test_row_types_importable(self) -> None:
        from autocontext.storage import row_types

        assert hasattr(row_types, "RunRow")
        assert hasattr(row_types, "GenerationMetricsRow")
        assert hasattr(row_types, "MatchRow")
        assert hasattr(row_types, "KnowledgeSnapshotRow")
