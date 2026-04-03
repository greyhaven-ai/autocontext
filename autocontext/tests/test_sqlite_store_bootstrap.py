"""Tests for AC-521: SQLite store bootstrap on clean workspace.

The store must create required tables even when migration files are
unavailable (e.g. installed via pip where migrations/ is not packaged).
"""

from __future__ import annotations

from pathlib import Path


class TestBootstrapSchema:
    """SQLiteStore should work on a fresh DB without external migration files."""

    def test_migrate_falls_back_when_migrations_are_missing(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.migrate(tmp_path / "missing-migrations")
        store.create_run("r1", "test_scenario", 3, "local")
        store.upsert_generation(
            "r1",
            0,
            0.25,
            0.5,
            1000.0,
            1,
            0,
            "accept",
            "completed",
            duration_seconds=1.5,
            dimension_summary_json='{"quality": 0.5}',
            scoring_backend="elo",
            rating_uncertainty=0.2,
        )
        rows = store.get_generation_metrics("r1")
        assert len(rows) == 1
        assert rows[0]["duration_seconds"] == 1.5
        assert rows[0]["scoring_backend"] == "elo"

    def test_bootstrapped_db_can_later_run_real_migrations(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.migrate(tmp_path / "missing-migrations")
        store.migrate(Path(__file__).resolve().parents[1] / "migrations")
        store.create_run("r1", "test_scenario", 3, "local")
        rows = store.list_runs(limit=10)
        assert len(rows) == 1

    def test_ensure_core_tables_is_idempotent(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.ensure_core_tables()
        store.ensure_core_tables()  # second call should not error
        store.create_run("r1", "test", 1, "local")
        rows = store.list_runs(limit=10)
        assert len(rows) == 1

    def test_migrate_then_ensure_does_not_conflict(self, tmp_path: Path) -> None:
        """If migrations ran first, ensure_core_tables should still be safe."""
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "migrated.db")
        store.migrate(Path(__file__).resolve().parents[1] / "migrations")
        store.ensure_core_tables()
        store.create_run("r1", "test", 1, "local")
        rows = store.list_runs(limit=1)
        assert len(rows) == 1

    def test_list_runs_on_fresh_db(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "runner.db")
        store.ensure_core_tables()
        rows = store.list_runs(limit=10)
        assert rows == []
