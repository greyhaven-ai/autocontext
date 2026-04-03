"""Tests for AC-521: SQLite store bootstrap on clean workspace.

The store must create required tables even when migration files are
unavailable (e.g. installed via pip where migrations/ is not packaged).
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest


class TestBootstrapSchema:
    """SQLiteStore should work on a fresh DB without external migration files."""

    def test_create_run_on_fresh_db(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.ensure_core_tables()
        # Should NOT raise sqlite3.OperationalError: no such table: runs
        store.create_run("r1", "test_scenario", 3, "local")

    def test_list_runs_on_fresh_db(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.ensure_core_tables()
        rows = store.list_runs(limit=10)
        assert rows == []

    def test_ensure_core_tables_is_idempotent(self, tmp_path: Path) -> None:
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "fresh.db")
        store.ensure_core_tables()
        store.ensure_core_tables()  # second call should not error
        store.create_run("r1", "test", 1, "local")

    def test_migrate_then_ensure_does_not_conflict(self, tmp_path: Path) -> None:
        """If migrations ran first, ensure_core_tables should still be safe."""
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "migrated.db")
        # First ensure creates tables
        store.ensure_core_tables()
        # Second ensure should be idempotent
        store.ensure_core_tables()
        store.create_run("r1", "test", 1, "local")
        rows = store.list_runs(limit=1)
        assert len(rows) == 1

    def test_generation_runner_calls_ensure(self, tmp_path: Path) -> None:
        """ensure_core_tables allows create_run + list_runs on fresh DB."""
        from autocontext.storage.sqlite_store import SQLiteStore

        store = SQLiteStore(tmp_path / "runner.db")
        store.ensure_core_tables()
        store.create_run("test-run", "scenario", 1, "local")
        rows = store.list_runs(limit=10)
        assert len(rows) == 1
        assert rows[0]["scenario"] == "scenario"
