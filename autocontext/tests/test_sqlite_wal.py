from __future__ import annotations

import sqlite3
import subprocess
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import closing, contextmanager
from pathlib import Path

import pytest

from autocontext.mission.store import MissionStore
from autocontext.session.runtime_events import RuntimeSessionEventStore
from autocontext.session.store import SessionStore
from autocontext.storage.sqlite_wal import enable_wal


@contextmanager
def _writer_holding_fresh_database(db_path: Path) -> Iterator[None]:
    with closing(sqlite3.connect(db_path, isolation_level=None, check_same_thread=False)) as writer:
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE held_by_writer (value TEXT)")
        release = threading.Timer(0.2, writer.execute, ("COMMIT",))
        release.start()
        try:
            yield
        finally:
            release.join()


def _run_in_fresh_interpreter(script: str, *, cwd: Path | None = None) -> str:
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=False, cwd=cwd)
    assert result.returncode == 0, result.stderr
    return result.stdout.strip()


def _journal_mode(db_path: Path) -> str:
    with closing(sqlite3.connect(db_path)) as conn:
        return str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()


def _open_mission_store(db_path: Path) -> None:
    MissionStore(str(db_path)).close()


def _open_session_store(db_path: Path) -> None:
    SessionStore(db_path)


def _open_runtime_session_event_store(db_path: Path) -> None:
    RuntimeSessionEventStore(db_path).close()


@pytest.mark.parametrize(
    "open_store",
    [_open_mission_store, _open_session_store, _open_runtime_session_event_store],
    ids=["mission-store", "session-store", "runtime-session-events"],
)
def test_store_waits_for_writer_before_enabling_wal_on_fresh_database(
    tmp_path: Path,
    open_store: Callable[[Path], None],
) -> None:
    db_path = tmp_path / "fresh.sqlite3"
    with _writer_holding_fresh_database(db_path):
        open_store(db_path)

    assert _journal_mode(db_path) == "wal"


def test_enable_wal_accepts_in_memory_database_reporting_memory_mode() -> None:
    with closing(sqlite3.connect(":memory:")) as conn:
        enable_wal(conn, timeout_seconds=0.1)

        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "memory"


def test_enable_wal_gives_up_when_writer_outlasts_timeout(tmp_path: Path) -> None:
    db_path = tmp_path / "held.sqlite3"
    with (
        closing(sqlite3.connect(db_path, isolation_level=None)) as writer,
        closing(sqlite3.connect(db_path)) as conn,
    ):
        writer.execute("BEGIN IMMEDIATE")
        writer.execute("CREATE TABLE held_by_writer (value TEXT)")

        with pytest.raises(sqlite3.OperationalError, match="database is locked"):
            enable_wal(conn, timeout_seconds=0.1)


@pytest.mark.parametrize(
    "module",
    [
        "autocontext.mission.store",
        "autocontext.session.store",
        "autocontext.session.runtime_events",
        "autocontext.execution._external_eval_outbox_store",
    ],
)
def test_wal_call_site_imports_in_fresh_interpreter(module: str) -> None:
    _run_in_fresh_interpreter(f"import {module}")


def test_importing_enable_wal_does_not_load_storage_implementations() -> None:
    loaded = _run_in_fresh_interpreter(
        "import sys\n"
        "import autocontext.storage.sqlite_wal\n"
        "print(sorted(name for name in ('autocontext.agents', 'autocontext.storage.artifacts') if name in sys.modules))"
    )

    assert loaded == "[]"


def test_storage_submodule_imports_in_fresh_interpreter_beside_knowledge_directory(tmp_path: Path) -> None:
    # The package no longer imports the agent stack up front, so importing a
    # storage submodule first must survive scenario discovery under ./knowledge.
    (tmp_path / "knowledge").mkdir()

    _run_in_fresh_interpreter("import autocontext.storage.context_selection_store", cwd=tmp_path)
