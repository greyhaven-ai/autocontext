from __future__ import annotations

from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def test_clear_quarantine_scoped_by_scenario(tmp_path: Path) -> None:
    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)
    store.create_run("run-a", "grid_ctf", 1, "local", agent_provider="anthropic")
    store.create_run("run-b", "othello", 1, "local", agent_provider="anthropic")
    # same content-hash epoch used in two scenarios; only grid_ctf must be cleared
    for rid in ("run-a", "run-b"):
        store.upsert_generation(
            rid,
            1,
            mean_score=0.9,
            best_score=0.9,
            elo=0.0,
            wins=0,
            losses=0,
            gate_decision="completed",
            status="completed",
            evaluator_epoch="epoch-x",
            quarantined=True,
        )
    cleared = store.clear_quarantine_for_epoch("grid_ctf", "epoch-x")
    assert cleared == 1
    assert store.get_generation_metrics("run-a")[0]["quarantined"] is None
    assert store.get_generation_metrics("run-b")[0]["quarantined"] in (1, True)  # untouched
