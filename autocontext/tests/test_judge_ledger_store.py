# autocontext/tests/test_judge_ledger_store.py
from __future__ import annotations

from pathlib import Path

import pytest

from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "loop_id": "loop-1",
        "run_id": "run-1",
        "scenario_name": "s",
        "scenario_family": "agent_task",
        "round_number": 1,
        "max_rounds": 5,
        "quality_threshold": 0.9,
        "evaluator_epoch": "epoch-a",
        "rubric_hash": "r",
        "task_prompt_hash": "p",
        "task_prompt": "prompt",
        "output_hash": "o",
        "output": "out",
        "score": 0.4,
        "passed": 0,
    }
    base.update(overrides)
    return base


@pytest.mark.parametrize("use_migrations", [True, False])
def test_ledger_round_trips_on_migrated_and_bootstrapped_databases(tmp_path: Path, use_migrations: bool) -> None:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(MIGRATIONS if use_migrations else tmp_path / "missing-migrations")
    first = store.insert_judge_ledger_row(_row())
    second = store.insert_judge_ledger_row(_row(round_number=2, score=0.95, passed=1, evaluator_epoch="epoch-b"))
    assert second > first
    rows = store.list_judge_ledger_rows()
    assert [r["round_number"] for r in rows] == [1, 2]
    assert rows[0]["required_concepts_json"] == "[]"
    assert rows[0]["prescreen_json"] is None
    assert rows[0]["created_at"]
    assert [r["round_number"] for r in store.list_judge_ledger_rows(evaluator_epoch="epoch-b")] == [2]
    assert store.list_judge_ledger_rows(scenario_family="other") == []


def test_ledger_rejects_unknown_columns(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(MIGRATIONS)
    with pytest.raises(ValueError, match="unknown judge_ledger columns"):
        store.insert_judge_ledger_row(_row(bogus=1))


def test_bootstrap_records_the_ledger_migration(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "ledger.db")
    store.migrate(tmp_path / "missing-migrations")
    with store.connection() as conn:
        versions = {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}
    assert "022_judge_ledger.sql" in versions
