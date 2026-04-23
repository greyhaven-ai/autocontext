from __future__ import annotations

import sqlite3
from pathlib import Path

from autocontext.storage.migration_ledgers import TYPESCRIPT_BASELINE_MIGRATIONS
from autocontext.storage.sqlite_store import SQLiteStore

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
PYTHON_MIGRATIONS_DIR = PACKAGE_ROOT / "migrations"
TYPESCRIPT_MIGRATIONS_DIR = REPO_ROOT / "ts" / "migrations"


def _apply_typescript_migrations(db_path: Path) -> None:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_version (
                filename TEXT PRIMARY KEY,
                applied_at TEXT NOT NULL DEFAULT (datetime('now'))
            );
            """
        )
        for migration in sorted(TYPESCRIPT_MIGRATIONS_DIR.glob("*.sql")):
            conn.executescript(migration.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_version(filename) VALUES (?)", (migration.name,))


def _ledger_values(db_path: Path, table: str, column: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {row[0] for row in conn.execute(f"SELECT {column} FROM {table}").fetchall()}


def test_python_migrations_can_follow_typescript_migrations(tmp_path: Path) -> None:
    db_path = tmp_path / "cross-runtime.db"
    _apply_typescript_migrations(db_path)

    store = SQLiteStore(db_path)
    store.migrate(PYTHON_MIGRATIONS_DIR)

    applied_python = _ledger_values(db_path, "schema_migrations", "version")
    applied_typescript = _ledger_values(db_path, "schema_version", "filename")

    assert applied_python == {migration.name for migration in PYTHON_MIGRATIONS_DIR.glob("*.sql")}
    assert set(TYPESCRIPT_BASELINE_MIGRATIONS).issubset(applied_typescript)


def test_bootstrap_schema_seeds_typescript_ledger(tmp_path: Path) -> None:
    db_path = tmp_path / "bootstrap.db"

    store = SQLiteStore(db_path)
    store.migrate(tmp_path / "missing-migrations")

    applied_typescript = _ledger_values(db_path, "schema_version", "filename")
    assert set(TYPESCRIPT_BASELINE_MIGRATIONS).issubset(applied_typescript)
