from __future__ import annotations

from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_quarantined_roundtrips(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)
    store.create_run("run-1", "grid_ctf", 1, "local", agent_provider="anthropic")
    store.upsert_generation(
        "run-1",
        1,
        mean_score=0.9,
        best_score=0.9,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="completed",
        status="completed",
        quarantined=True,
    )
    rows = store.get_generation_metrics("run-1")
    assert rows[0]["quarantined"] in (1, True)


def test_quarantined_defaults_null(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "t2.sqlite3")
    store.migrate(MIGRATIONS)
    store.create_run("run-2", "grid_ctf", 1, "local", agent_provider="anthropic")
    store.upsert_generation(
        "run-2",
        1,
        mean_score=0.5,
        best_score=0.5,
        elo=1000.0,
        wins=1,
        losses=0,
        gate_decision="advance",
        status="completed",
    )
    assert store.get_generation_metrics("run-2")[0]["quarantined"] is None
