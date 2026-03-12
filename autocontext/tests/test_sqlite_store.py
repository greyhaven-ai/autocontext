from __future__ import annotations

from pathlib import Path

from autocontext.storage.sqlite_store import SQLITE_BUSY_TIMEOUT_MS, SQLiteStore


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "test.sqlite3")
    store.migrate(Path("migrations"))
    return store


def test_connect_applies_sqlite_tuning(tmp_path: Path) -> None:
    store = _make_store(tmp_path)

    with store.connect() as conn:
        journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
        busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]

    assert str(journal_mode).lower() == "wal"
    assert busy_timeout == SQLITE_BUSY_TIMEOUT_MS


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

    with store.connect() as conn:
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
