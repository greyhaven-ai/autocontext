from __future__ import annotations

import os
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Protocol

import pytest

from autocontext.ambient.queue import AmbientQueue
from autocontext.ambient.trace_store import TraceStore
from autocontext.ambient.usage import UsageLedger
from autocontext.session import runtime_events as runtime_events_module
from autocontext.session import store as session_store_module
from autocontext.session.runtime_events import RuntimeSessionEventStore
from autocontext.session.store import SessionStore


class _Connectable(Protocol):
    def _connect(self) -> sqlite3.Connection: ...


def _open_file_descriptor_count() -> int:
    count = 0
    for raw_fd in os.listdir("/dev/fd"):
        try:
            os.fstat(int(raw_fd))
        except (OSError, ValueError):
            continue
        count += 1
    return count


def _retain_connections(
    connect: Callable[[], sqlite3.Connection],
    retained: list[sqlite3.Connection],
) -> Callable[[], sqlite3.Connection]:
    def retaining_connect() -> sqlite3.Connection:
        conn = connect()
        retained.append(conn)
        return conn

    return retaining_connect


def _operation_cases(tmp_path: Path) -> list[tuple[_Connectable, Callable[[], object]]]:
    traces = TraceStore(tmp_path / "traces.sqlite3")
    usage = UsageLedger(tmp_path / "usage.sqlite3")
    queue = AmbientQueue(tmp_path / "queue.sqlite3")
    sessions = SessionStore(tmp_path / "sessions.sqlite3")
    return [
        (traces, traces.count),
        (usage, lambda: usage.total("target")),
        (queue, lambda: queue.depth("stage")),
        (sessions, lambda: sessions.load("missing")),
    ]


def test_operation_scoped_stores_close_every_connection(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    for store, operation in _operation_cases(tmp_path):
        retained_connections: list[sqlite3.Connection] = []
        original_connect = store._connect  # noqa: SLF001
        monkeypatch.setattr(store, "_connect", _retain_connections(original_connect, retained_connections))

        for _ in range(64):
            operation()

        assert len(retained_connections) == 64
        for conn in retained_connections:
            with pytest.raises(sqlite3.ProgrammingError, match="closed"):
                conn.execute("SELECT 1")


@pytest.mark.skipif(os.name != "posix" or not Path("/dev/fd").is_dir(), reason="requires /dev/fd")
def test_operation_scoped_stores_do_not_accumulate_file_descriptors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    baseline = _open_file_descriptor_count()
    cases = _operation_cases(tmp_path)
    assert _open_file_descriptor_count() == baseline

    for store, operation in cases:
        retained_connections: list[sqlite3.Connection] = []
        original_connect = store._connect  # noqa: SLF001
        monkeypatch.setattr(store, "_connect", _retain_connections(original_connect, retained_connections))
        operation_baseline = _open_file_descriptor_count()

        for _ in range(64):
            operation()

        assert len(retained_connections) == 64
        assert _open_file_descriptor_count() == operation_baseline


@pytest.mark.parametrize("store_kind", ["session", "runtime_events"])
@pytest.mark.parametrize("failure_stage", ["row_factory", "journal_mode"])
def test_session_store_connect_closes_connection_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    store_kind: str,
    failure_stage: str,
) -> None:
    class SetupFailureConnection:
        closed = False
        _row_factory: object | None = None

        @property
        def row_factory(self) -> object | None:
            return self._row_factory

        @row_factory.setter
        def row_factory(self, value: object | None) -> None:
            if failure_stage == "row_factory":
                raise sqlite3.OperationalError("setup failed")
            self._row_factory = value

        def execute(self, statement: str) -> None:
            if failure_stage == "journal_mode" and "journal_mode" in statement:
                raise sqlite3.OperationalError("setup failed")

        def close(self) -> None:
            self.closed = True

    connection = SetupFailureConnection()
    module = session_store_module if store_kind == "session" else runtime_events_module
    monkeypatch.setattr(module.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="setup failed"):
        if store_kind == "session":
            SessionStore(tmp_path / "session-setup-failure.sqlite3")
        else:
            RuntimeSessionEventStore(tmp_path / "runtime-events-setup-failure.sqlite3")

    assert connection.closed
