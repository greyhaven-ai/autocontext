"""Tests for SQLiteStore.persist_rescore_revision (AC-885 Slice D2b).

The promote-and-archive write method reads the current generation row, archives the
original score/epoch/quarantined into ``generation_score_revisions``, and promotes the
new score/epoch onto the generation while clearing quarantine. It is per-(run_id,
generation_index) and atomic, and leaves ``mean_score``/``elo`` untouched.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.storage.sqlite_store import SQLiteStore

MIGRATIONS = Path(__file__).resolve().parent.parent / "migrations"


def _make_store(tmp_path: Path) -> SQLiteStore:
    store = SQLiteStore(tmp_path / "t.sqlite3")
    store.migrate(MIGRATIONS)
    return store


def _seed_generation(store: SQLiteStore) -> None:
    store.create_run("run-a", "grid_ctf", 1, "local", agent_provider="anthropic")
    store.upsert_generation(
        "run-a",
        1,
        mean_score=0.42,
        best_score=0.6,
        elo=1200.0,
        wins=3,
        losses=1,
        gate_decision="completed",
        status="completed",
        evaluator_epoch="e1",
        quarantined=True,
    )


def _read_revisions(store: SQLiteStore, run_id: str, generation_index: int) -> list[dict[str, object]]:
    with store.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM generation_score_revisions WHERE run_id = ? AND generation_index = ? ORDER BY id",
            (run_id, generation_index),
        ).fetchall()
        return [dict(r) for r in rows]


def test_persist_rescore_revision_promotes_and_archives(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)

    assert store.persist_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True

    gen = store.get_generation("run-a", 1)
    assert gen is not None
    assert gen["best_score"] == 0.55
    assert gen["evaluator_epoch"] == "e2"
    assert gen["quarantined"] is None
    # untouched columns
    assert gen["mean_score"] == 0.42
    assert gen["elo"] == 1200.0

    revisions = _read_revisions(store, "run-a", 1)
    assert len(revisions) == 1
    rev = revisions[0]
    assert rev["revision_epoch"] == "e2"
    assert rev["revision_score"] == 0.55
    assert rev["previous_epoch"] == "e1"
    assert rev["previous_score"] == 0.6
    assert rev["previous_quarantined"] == 1
    assert rev["created_by"] == "jay"


def test_persist_rescore_revision_missing_run_returns_false(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)

    assert store.persist_rescore_revision("missing-run", 1, 0.55, "e2") is False
    assert _read_revisions(store, "missing-run", 1) == []


def test_persist_rescore_revision_reapply_appends_history(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)

    assert store.persist_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True
    assert store.persist_rescore_revision("run-a", 1, 0.7, "e3", created_by="sam") is True

    revisions = _read_revisions(store, "run-a", 1)
    assert len(revisions) == 2
    # second revision archives the first promotion's values
    assert revisions[1]["revision_epoch"] == "e3"
    assert revisions[1]["revision_score"] == 0.7
    assert revisions[1]["previous_epoch"] == "e2"
    assert revisions[1]["previous_score"] == 0.55
    assert revisions[1]["previous_quarantined"] is None
    assert revisions[1]["created_by"] == "sam"

    gen = store.get_generation("run-a", 1)
    assert gen is not None
    assert gen["best_score"] == 0.7
    assert gen["evaluator_epoch"] == "e3"
    assert gen["quarantined"] is None
