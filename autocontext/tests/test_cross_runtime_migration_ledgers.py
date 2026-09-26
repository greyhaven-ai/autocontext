from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path

import pytest

from autocontext.storage.migration_ledgers import TYPESCRIPT_BASELINE_MIGRATIONS
from autocontext.storage.sqlite_store import SQLiteStore

PACKAGE_ROOT = Path(__file__).resolve().parents[1]
REPO_ROOT = PACKAGE_ROOT.parent
PYTHON_MIGRATIONS_DIR = PACKAGE_ROOT / "migrations"
TYPESCRIPT_MIGRATIONS_DIR = REPO_ROOT / "ts" / "migrations"


def _apply_typescript_migrations(db_path: Path) -> None:
    with closing(sqlite3.connect(db_path)) as conn, conn:
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
    with closing(sqlite3.connect(db_path)) as conn, conn:
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


def _drop_minimum_generations_as_unfixed_typescript_013_did(db_path: Path) -> None:
    """Leave the state the TypeScript runner produced before 013 kept the column."""
    with closing(sqlite3.connect(db_path)) as conn, conn:
        conn.executescript((TYPESCRIPT_MIGRATIONS_DIR / "013_runs_status_default_parity.sql").read_text(encoding="utf-8"))
        conn.executemany(
            "INSERT OR IGNORE INTO schema_version(filename) VALUES (?)",
            [("013_runs_status_default_parity.sql",), ("019_run_minimum_generations.sql",)],
        )


def _minimum_generations_columns(db_path: Path) -> list[tuple[str, str]]:
    with closing(sqlite3.connect(db_path)) as conn, conn:
        return [(row[1], row[4]) for row in conn.execute("PRAGMA table_info(runs)").fetchall() if row[1] == "minimum_generations"]


@pytest.mark.parametrize("migrations_dir", [PYTHON_MIGRATIONS_DIR, None], ids=["migration-files", "bootstrap"])
def test_migrate_restores_minimum_generations_dropped_by_typescript_013(tmp_path: Path, migrations_dir: Path | None) -> None:
    db_path = tmp_path / "dropped-minimum-generations.db"
    store = SQLiteStore(db_path)
    store.migrate(PYTHON_MIGRATIONS_DIR)
    store.create_run("run-1", "grid_ctf", 5, "local", minimum_generations=3)
    _drop_minimum_generations_as_unfixed_typescript_013_did(db_path)
    with pytest.raises(sqlite3.OperationalError, match="no column named minimum_generations"):
        store.create_run("run-2", "grid_ctf", 5, "local", minimum_generations=2)

    store.migrate(migrations_dir or tmp_path / "missing-migrations")
    store.migrate(migrations_dir or tmp_path / "missing-migrations")

    assert _minimum_generations_columns(db_path) == [("minimum_generations", "1")]
    store.create_run("run-2", "grid_ctf", 5, "local", minimum_generations=2)
    with closing(sqlite3.connect(db_path)) as conn, conn:
        assert dict(conn.execute("SELECT run_id, minimum_generations FROM runs").fetchall()) == {
            "run-1": 1,
            "run-2": 2,
        }
        with pytest.raises(sqlite3.IntegrityError, match="CHECK constraint failed"):
            conn.execute("UPDATE runs SET minimum_generations = 0 WHERE run_id = 'run-2'")


def test_migrate_leaves_minimum_generations_to_its_migration_until_a_ledger_records_it(tmp_path: Path) -> None:
    db_path = tmp_path / "pre-minimum-generations.db"
    pre_020_migrations_dir = tmp_path / "pre-020-migrations"
    pre_020_migrations_dir.mkdir()
    for migration in PYTHON_MIGRATIONS_DIR.glob("*.sql"):
        if migration.name < "020_":
            (pre_020_migrations_dir / migration.name).write_text(migration.read_text(encoding="utf-8"), encoding="utf-8")

    SQLiteStore(db_path).migrate(pre_020_migrations_dir)

    assert _minimum_generations_columns(db_path) == []
