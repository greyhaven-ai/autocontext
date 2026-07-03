from __future__ import annotations

import sqlite3
from pathlib import Path

from autocontext.ambient.sources.native import NativeRunsSource

_SCHEMA = """
CREATE TABLE runs (
    run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL, target_generations INTEGER NOT NULL,
    executor_mode TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE TABLE generations (
    run_id TEXT NOT NULL, generation_index INTEGER NOT NULL, mean_score REAL NOT NULL,
    best_score REAL NOT NULL, gate_decision TEXT NOT NULL, status TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')), updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (run_id, generation_index)
);
"""


def _seed(db: Path, statuses: list[str]) -> None:
    conn = sqlite3.connect(db)
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT INTO runs (run_id, scenario, target_generations, executor_mode, status) "
        "VALUES ('r1', 'grid_ctf', 3, 'local', 'completed')"
    )
    for index, status in enumerate(statuses):
        conn.execute(
            "INSERT INTO generations (run_id, generation_index, mean_score, best_score, gate_decision, status) "
            "VALUES ('r1', ?, 1.0, 2.0, 'advance', ?)",
            (index, status),
        )
    conn.commit()
    conn.close()


def test_poll_reads_generations_with_run_context(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["completed", "completed"])
    source = NativeRunsSource(name="native", runs_db_path=db)
    result = source.poll(None)
    assert len(result.records) == 2
    first = result.records[0]
    assert first.kind == "generation"
    assert first.payload["run_id"] == "r1"
    assert first.payload["scenario"] == "grid_ctf"
    assert first.payload["generation_index"] == 0
    assert first.payload["best_score"] == 2.0
    assert result.next_cursor == "2"


def test_poll_is_incremental(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["completed", "completed", "completed"])
    source = NativeRunsSource(name="native", runs_db_path=db)
    first = source.poll(None)
    second = source.poll(first.next_cursor)
    assert second.records == []
    assert second.next_cursor is None


def test_batch_size_limits_poll(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["completed"] * 5)
    source = NativeRunsSource(name="native", runs_db_path=db, batch_size=2)
    result = source.poll(None)
    assert len(result.records) == 2
    rest = source.poll(result.next_cursor)
    assert len(rest.records) == 2


def test_missing_db_returns_empty_poll(tmp_path: Path) -> None:
    source = NativeRunsSource(name="native", runs_db_path=tmp_path / "absent.sqlite3")
    result = source.poll(None)
    assert result.records == []
    assert result.next_cursor is None


def test_failed_status_is_terminal_and_ingested(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["completed", "failed"])
    source = NativeRunsSource(name="native", runs_db_path=db)
    result = source.poll(None)
    assert [r.payload["status"] for r in result.records] == ["completed", "failed"]
    assert result.next_cursor == "2"


def test_in_flight_row_blocks_cursor_then_advances_when_terminal(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["completed", "running", "completed"])
    source = NativeRunsSource(name="native", runs_db_path=db)

    # the in-flight ("running") row at rowid 2 truncates the batch: only the
    # first row is returned and the cursor stops before the in-flight row
    first = source.poll(None)
    assert len(first.records) == 1
    assert first.records[0].payload["generation_index"] == 0
    assert first.next_cursor == "1"

    # the placeholder completes in place (rowid-preserving UPDATE, same rowid 2)
    conn = sqlite3.connect(db)
    conn.execute("UPDATE generations SET status = 'completed' WHERE generation_index = 1")
    conn.commit()
    conn.close()

    # a later poll from the held cursor now picks up rows 2 and 3 with finals
    second = source.poll(first.next_cursor)
    assert [r.payload["generation_index"] for r in second.records] == [1, 2]
    assert second.next_cursor == "3"


def test_all_running_seed_returns_empty_poll(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _seed(db, ["running", "running"])
    source = NativeRunsSource(name="native", runs_db_path=db)
    result = source.poll(None)
    assert result.records == []
    assert result.next_cursor is None
