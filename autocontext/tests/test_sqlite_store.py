from __future__ import annotations

import os
import sqlite3
from pathlib import Path

import pytest

from autocontext.storage import sqlite_store as sqlite_store_module
from autocontext.storage.sqlite_store import SQLITE_BUSY_TIMEOUT_MS, SQLiteStore


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    store.migrate(Path("migrations"))
    return store


def test_connect_applies_sqlite_tuning(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    conn = store.connect()
    try:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    finally:
        conn.close()

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS


def test_connect_remains_reusable_across_transactions_until_explicit_close(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    conn = store.connect()
    try:
        with conn:
            conn.execute(
                "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
                "VALUES ('first', 'grid_ctf', 1, 'local', 'running')"
            )
        assert conn.execute("SELECT run_id FROM runs WHERE run_id = 'first'").fetchone() is not None

        with conn:
            conn.execute(
                "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
                "VALUES ('second', 'grid_ctf', 1, 'local', 'running')"
            )
        assert conn.execute("SELECT run_id FROM runs WHERE run_id = 'second'").fetchone() is not None
    finally:
        conn.close()

    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")


def test_connection_context_commits_and_closes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with store.connection() as conn:
        conn.execute(
            "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
            "VALUES ('committed', 'grid_ctf', 1, 'local', 'running')"
        )
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")

    with store.connection() as verify_conn:
        row = verify_conn.execute("SELECT run_id FROM runs WHERE run_id = 'committed'").fetchone()
    assert row is not None


def test_connection_context_rolls_back_and_closes(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with pytest.raises(RuntimeError, match="rollback"):
        with store.connection() as conn:
            conn.execute(
                "INSERT INTO runs(run_id, scenario, target_generations, executor_mode, status) "
                "VALUES ('rolled-back', 'grid_ctf', 1, 'local', 'running')"
            )
            raise RuntimeError("rollback")
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        conn.execute("SELECT 1")

    with store.connection() as verify_conn:
        row = verify_conn.execute("SELECT run_id FROM runs WHERE run_id = 'rolled-back'").fetchone()
    assert row is None


@pytest.mark.parametrize("failure_stage", ["row_factory", "foreign_keys", "journal_mode", "busy_timeout"])
def test_connect_closes_new_connection_when_setup_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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
            if failure_stage in statement:
                raise sqlite3.OperationalError("setup failed")

        def close(self) -> None:
            self.closed = True

    connection = SetupFailureConnection()
    monkeypatch.setattr(sqlite_store_module.sqlite3, "connect", lambda *_args, **_kwargs: connection)

    with pytest.raises(sqlite3.OperationalError, match="setup failed"):
        SQLiteStore(tmp_path / "setup-failure.sqlite3").connect()

    assert connection.closed


def _open_file_descriptor_count() -> int:
    count = 0
    for raw_fd in os.listdir("/dev/fd"):
        try:
            os.fstat(int(raw_fd))
        except (OSError, ValueError):
            continue
        count += 1
    return count


@pytest.mark.skipif(os.name != "posix" or not Path("/dev/fd").is_dir(), reason="requires /dev/fd")
def test_store_calls_do_not_accumulate_file_descriptors(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    baseline = _open_file_descriptor_count()
    exited_connections: list[sqlite3.Connection] = []

    for _ in range(64):
        with store.connection() as conn:
            assert conn.execute("SELECT COUNT(*) FROM task_queue").fetchone()[0] == 0
        exited_connections.append(conn)

    assert _open_file_descriptor_count() == baseline
    for conn in exited_connections:
        with pytest.raises(sqlite3.ProgrammingError, match="closed"):
            conn.execute("SELECT 1")


def test_append_generation_agent_activity_batches_outputs_and_metrics(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run-1", "grid_ctf", 1, "local")
    store.upsert_generation("run-1", 1, 0.0, 0.0, 1000.0, 0, 0, "running", "running")

    store.append_generation_agent_activity(
        "run-1",
        1,
        outputs=[
            ("competitor", '{"aggression": 0.7}'),
            ("analyst", "analysis"),
        ],
        role_metrics=[
            ("competitor", "model-a", 10, 20, 30, "sub-1", "completed"),
            ("analyst", "model-b", 11, 21, 31, "sub-2", "completed"),
        ],
    )

    competitor_rows = store.get_agent_outputs_by_role("run-1", "competitor")
    analyst_rows = store.get_agent_outputs_by_role("run-1", "analyst")
    assert competitor_rows == [{"generation_index": 1, "role": "competitor", "content": '{"aggression": 0.7}'}]
    assert analyst_rows == [{"generation_index": 1, "role": "analyst", "content": "analysis"}]

    with store.connection() as conn:
        role_metric_rows = conn.execute(
            """
            SELECT role, model, input_tokens, output_tokens, latency_ms, subagent_id, status
            FROM agent_role_metrics
            WHERE run_id = ? AND generation_index = ?
            ORDER BY role
            """,
            ("run-1", 1),
        ).fetchall()

    assert [dict(row) for row in role_metric_rows] == [
        {
            "role": "analyst",
            "model": "model-b",
            "input_tokens": 11,
            "output_tokens": 21,
            "latency_ms": 31,
            "subagent_id": "sub-2",
            "status": "completed",
        },
        {
            "role": "competitor",
            "model": "model-a",
            "input_tokens": 10,
            "output_tokens": 20,
            "latency_ms": 30,
            "subagent_id": "sub-1",
            "status": "completed",
        },
    ]


def test_latest_competitor_output_is_canonical_for_generation_queries(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run-1", "grid_ctf", 1, "local")
    store.upsert_generation("run-1", 1, 0.4, 0.5, 1000.0, 1, 0, "advance", "completed")
    store.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.2}')
    store.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.9}')

    history = store.get_strategy_score_history("run-1")
    assert history == [
        {
            "generation_index": 1,
            "content": '{"aggression": 0.9}',
            "best_score": 0.5,
            "gate_decision": "advance",
        },
    ]
    assert store.get_best_competitor_output("grid_ctf") == '{"aggression": 0.9}'


def test_self_play_strategy_history_includes_elo(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run-1", "grid_ctf", 2, "local")
    store.upsert_generation("run-1", 1, 0.4, 0.5, 1012.5, 1, 0, "advance", "completed")
    store.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.9}')

    history = store.get_self_play_strategy_history("run-1")

    assert history == [
        {
            "generation_index": 1,
            "content": '{"aggression": 0.9}',
            "best_score": 0.5,
            "gate_decision": "advance",
            "elo": 1012.5,
        },
    ]


def test_generation_and_snapshot_store_scoring_backend_metadata(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    store.create_run("run-1", "grid_ctf", 1, "local")
    store.upsert_generation(
        "run-1",
        1,
        0.4,
        0.5,
        1512.5,
        1,
        0,
        "advance",
        "completed",
        scoring_backend="glicko",
        rating_uncertainty=312.4,
    )
    trajectory = store.get_generation_trajectory("run-1")
    assert trajectory[0]["scoring_backend"] == "glicko"
    assert trajectory[0]["rating_uncertainty"] == 312.4

    store.save_knowledge_snapshot(
        "grid_ctf",
        "run-1",
        0.5,
        1512.5,
        "hash1",
        scoring_backend="glicko",
        rating_uncertainty=312.4,
    )
    snapshot = store.get_best_knowledge_snapshot("grid_ctf")
    assert snapshot is not None
    assert snapshot["scoring_backend"] == "glicko"
    assert snapshot["rating_uncertainty"] == 312.4
