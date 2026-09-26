from __future__ import annotations

import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore

PYTHON_MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"
CONCURRENT_MIGRATORS = 8
FRESH_DATABASES = 5


def test_connect_waits_for_writer_before_enabling_wal_on_fresh_database(tmp_path: Path) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    with closing(sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE held_by_writer (value TEXT)")
        release = threading.Timer(0.2, writer.execute, ("COMMIT",))
        release.start()
        try:
            with closing(SQLiteStore(db_path).connect()) as conn:
                journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
        finally:
            release.join()

    assert journal_mode == "wal"


def _migrate_when_released(barrier: threading.Barrier, db_path: Path) -> None:
    store = SQLiteStore(db_path)
    barrier.wait()
    store.migrate(PYTHON_MIGRATIONS_DIR)


def test_concurrent_migrations_on_fresh_database_apply_each_version_once(tmp_path: Path) -> None:
    expected = sorted(migration.name for migration in PYTHON_MIGRATIONS_DIR.glob("*.sql"))

    for attempt in range(FRESH_DATABASES):
        db_path = tmp_path / f"fresh-{attempt}.sqlite3"
        barrier = threading.Barrier(CONCURRENT_MIGRATORS)
        with ThreadPoolExecutor(max_workers=CONCURRENT_MIGRATORS) as pool:
            futures = [pool.submit(_migrate_when_released, barrier, db_path) for _ in range(CONCURRENT_MIGRATORS)]
            for future in futures:
                future.result()

        with closing(sqlite3.connect(db_path)) as conn:
            applied = [row[0] for row in conn.execute("SELECT version FROM schema_migrations ORDER BY version")]
        assert applied == expected
