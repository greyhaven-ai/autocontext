"""tests for the agent-outputs native source."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from autocontext.ambient.sources.agent_outputs import AgentOutputsSource


def _make_runs_db(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL);
        CREATE TABLE generations (
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            mean_score REAL NOT NULL,
            best_score REAL NOT NULL,
            gate_decision TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, generation_index)
        );
        CREATE TABLE agent_outputs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
        """
    )
    conn.commit()
    conn.close()


def _make_runs_db_without_agent_outputs(path: Path) -> None:
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE runs (run_id TEXT PRIMARY KEY, scenario TEXT NOT NULL);
        CREATE TABLE generations (
            run_id TEXT NOT NULL,
            generation_index INTEGER NOT NULL,
            mean_score REAL NOT NULL,
            best_score REAL NOT NULL,
            gate_decision TEXT NOT NULL,
            status TEXT NOT NULL,
            created_at TEXT NOT NULL DEFAULT (datetime('now')),
            PRIMARY KEY (run_id, generation_index)
        );
        """
    )
    conn.commit()
    conn.close()


def _insert(path: Path, sql: str, params: tuple) -> None:
    conn = sqlite3.connect(path)
    conn.execute(sql, params)
    conn.commit()
    conn.close()


def _add_generation(path: Path, run_id: str, index: int, status: str) -> None:
    _insert(
        path,
        "INSERT OR IGNORE INTO runs (run_id, scenario) VALUES (?, ?)",
        (run_id, "grid_ctf"),
    )
    _insert(
        path,
        "INSERT INTO generations (run_id, generation_index, mean_score, best_score, gate_decision, status) "
        "VALUES (?, ?, 0.5, 0.9, 'advance', ?)",
        (run_id, index, status),
    )


def _add_output(path: Path, run_id: str, index: int, role: str, content: str) -> None:
    _insert(
        path,
        "INSERT INTO agent_outputs (run_id, generation_index, role, content) VALUES (?, ?, ?, ?)",
        (run_id, index, role, content),
    )


def test_missing_db_yields_empty_poll(tmp_path: Path) -> None:
    source = AgentOutputsSource(name="native", runs_db_path=tmp_path / "absent.sqlite3")
    poll = source.poll(None)
    assert poll.records == []
    assert poll.next_cursor is None


def test_runs_db_without_agent_outputs_table_yields_empty_poll(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _make_runs_db_without_agent_outputs(db)
    _add_generation(db, "run_a", 0, "completed")

    poll = AgentOutputsSource(name="native", runs_db_path=db).poll(None)

    assert poll.records == []
    assert poll.next_cursor is None


def test_emits_agent_output_traces_with_generation_labels(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _make_runs_db(db)
    _add_generation(db, "run_a", 0, "completed")
    _add_output(db, "run_a", 0, "competitor", "the strategy text")

    poll = AgentOutputsSource(name="native", runs_db_path=db).poll(None)

    assert len(poll.records) == 1
    record = poll.records[0]
    assert record.kind == "agent_output"
    assert record.payload["run_id"] == "run_a"
    assert record.payload["scenario"] == "grid_ctf"
    assert record.payload["role"] == "competitor"
    assert record.payload["content"] == "the strategy text"
    assert record.payload["status"] == "completed"
    assert record.payload["best_score"] == 0.9
    assert record.payload["gate_decision"] == "advance"
    assert poll.next_cursor == "1"


def test_holds_at_first_output_of_a_running_generation(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _make_runs_db(db)
    _add_generation(db, "run_a", 0, "completed")
    _add_output(db, "run_a", 0, "competitor", "done text")
    _add_generation(db, "run_a", 1, "running")
    _add_output(db, "run_a", 1, "competitor", "in flight text")
    _add_generation(db, "run_a", 2, "completed")
    _add_output(db, "run_a", 2, "competitor", "later text")

    source = AgentOutputsSource(name="native", runs_db_path=db)
    poll = source.poll(None)

    # only the contiguous terminal prefix: the running generation blocks
    # everything at and after its first output row
    assert [r.payload["content"] for r in poll.records] == ["done text"]
    assert poll.next_cursor == "1"

    conn = sqlite3.connect(db)
    conn.execute("UPDATE generations SET status = 'completed' WHERE run_id = 'run_a' AND generation_index = 1")
    conn.commit()
    conn.close()

    resumed = source.poll(poll.next_cursor)
    assert [r.payload["content"] for r in resumed.records] == ["in flight text", "later text"]
    assert resumed.next_cursor == "3"


def test_cursor_advances_and_no_reread(tmp_path: Path) -> None:
    db = tmp_path / "runs.sqlite3"
    _make_runs_db(db)
    _add_generation(db, "run_a", 0, "completed")
    _add_output(db, "run_a", 0, "competitor", "text one")

    source = AgentOutputsSource(name="native", runs_db_path=db)
    first = source.poll(None)
    assert first.next_cursor == "1"
    second = source.poll(first.next_cursor)
    assert second.records == []
