"""Tests for AC-521: SQLite store bootstrap on clean workspace.

The store must create required tables even when migration files are
unavailable (e.g. installed via pip where migrations/ is not packaged).
"""

from __future__ import annotations

from pathlib import Path


def _schema(path: Path) -> dict[str, object]:
    import sqlite3

    conn = sqlite3.connect(path)
    try:
        tables = [
            row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table' AND name NOT LIKE 'sqlite_%'")
        ]
        columns = {
            table: {row[1]: (row[2], row[3], row[4], row[5]) for row in conn.execute(f"PRAGMA table_info({table})")}
            for table in tables
        }
        indexes = sorted(
            conn.execute("SELECT tbl_name, name FROM sqlite_master WHERE type = 'index' AND name NOT LIKE 'sqlite_%'").fetchall()
        )
    finally:
        conn.close()
    return {"columns": columns, "indexes": indexes}


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

    def test_bootstrapped_runs_reject_minimum_generations_below_one(self, tmp_path: Path) -> None:
        import sqlite3

        import pytest

        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.migrate(tmp_path / "missing-migrations")
        store.create_run("r1", "test_scenario", 3, "local")
        # create_run uses INSERT OR IGNORE, which skips CHECK failures silently.
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"), store.connection() as conn:
            conn.execute("UPDATE runs SET minimum_generations = 0 WHERE run_id = 'r1'")

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

    def test_bootstrap_upgrades_a_database_bootstrapped_by_the_previous_release(self, tmp_path: Path) -> None:
        """A pip upgrade from 0.18.0 gains every column and index a fresh bootstrap creates."""
        import sqlite3

        from autocontext.storage.sqlite_store import SQLiteStore

        upgraded = tmp_path / "upgraded.db"
        conn = sqlite3.connect(upgraded)
        conn.executescript((Path(__file__).parent / "fixtures" / "bootstrap_schema_py_v0_18_0.sql").read_text(encoding="utf-8"))
        conn.close()

        SQLiteStore(upgraded).migrate(tmp_path / "missing-migrations")
        SQLiteStore(tmp_path / "fresh.db").migrate(tmp_path / "missing-migrations")

        assert _schema(upgraded) == _schema(tmp_path / "fresh.db")
