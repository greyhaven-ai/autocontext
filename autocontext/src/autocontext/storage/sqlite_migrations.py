from __future__ import annotations

import re
import sqlite3
from collections.abc import Sequence
from pathlib import Path

from autocontext.storage.migration_ledgers import typescript_baselines_for_python_migrations

_ALTER_ADD_COLUMN_RE = re.compile(r"^\s*(?:--[^\n]*\n\s*)*ALTER\s+TABLE\s+\S+\s+ADD\s+COLUMN\s+", re.IGNORECASE)


def _iter_sql_statements(script: str) -> Sequence[str]:
    statements: list[str] = []
    pending: list[str] = []
    for line in script.splitlines(keepends=True):
        pending.append(line)
        statement = "".join(pending).strip()
        if statement and sqlite3.complete_statement(statement):
            statements.append(statement)
            pending = []
    trailing = "".join(pending).strip()
    if trailing:
        statements.append(trailing)
    return statements


def _execute_migration_script(conn: sqlite3.Connection, script: str) -> None:
    for statement in _iter_sql_statements(script):
        try:
            conn.execute(statement)
        except sqlite3.OperationalError as exc:
            if "duplicate column name" in str(exc).lower() and _ALTER_ADD_COLUMN_RE.match(statement):
                continue
            raise


def _applied_python_migrations(conn: sqlite3.Connection) -> set[str]:
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations").fetchall()}


def apply_python_migration_files(conn: sqlite3.Connection, migrations_dir: Path) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_version (
            filename TEXT PRIMARY KEY,
            applied_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    applied_python = _applied_python_migrations(conn)
    for migration in sorted(migrations_dir.glob("*.sql")):
        if migration.name in applied_python:
            continue
        # Another process may be migrating the same database. Take the write
        # lock first, then re-read the ledger so each version applies once.
        conn.execute("BEGIN IMMEDIATE")
        try:
            applied_python = _applied_python_migrations(conn)
            if migration.name not in applied_python:
                _execute_migration_script(conn, migration.read_text(encoding="utf-8"))
                conn.execute("INSERT INTO schema_migrations(version) VALUES (?)", (migration.name,))
                applied_python.add(migration.name)
                for typescript_migration in typescript_baselines_for_python_migrations(applied_python):
                    conn.execute(
                        "INSERT OR IGNORE INTO schema_version(filename) VALUES (?)",
                        (typescript_migration,),
                    )
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
