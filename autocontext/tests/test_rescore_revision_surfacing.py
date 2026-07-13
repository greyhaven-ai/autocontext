"""Tests for surfacing recorded re-scores (AC-885 Slice D2c).

Covers the read-only store method ``SQLiteStore.latest_active_revisions`` (per generation, the LATEST
revision whose ``revision_epoch`` equals the active epoch) and the pure ``revision_fields`` helper that
shapes the four additive surfacing fields. No writes in either surface; tests seed via
``record_rescore_revision`` / a raw INSERT.
"""

from __future__ import annotations

from pathlib import Path

from autocontext.execution.epoch_lineage import revision_fields
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
        best_score=0.9,
        elo=1200.0,
        wins=3,
        losses=1,
        gate_decision="completed",
        status="completed",
        evaluator_epoch="e1",
        quarantined=True,
    )


def test_latest_active_revisions_returns_latest_active_epoch_revision(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)

    # Two active-epoch (e2) revisions; the later (highest id) one wins.
    assert store.record_rescore_revision("run-a", 1, 0.5, "e2") is True
    assert store.record_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True
    # A non-active-epoch revision (e_other) must be excluded from the active-epoch view.
    with store.connect() as conn:
        conn.execute(
            "INSERT INTO generation_score_revisions "
            "(run_id, generation_index, revision_epoch, revision_score) VALUES (?, ?, ?, ?)",
            ("run-a", 1, "e_other", 0.99),
        )

    latest = store.latest_active_revisions("run-a", "e2")
    assert set(latest) == {1}
    rev = latest[1]
    assert rev["revision_epoch"] == "e2"
    assert rev["revision_score"] == 0.55
    assert rev["created_by"] == "jay"


def test_latest_active_revisions_none_epoch_returns_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)
    assert store.record_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True

    assert store.latest_active_revisions("run-a", None) == {}


def test_latest_active_revisions_no_matching_epoch_returns_empty(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)
    assert store.record_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True

    assert store.latest_active_revisions("run-a", "e_nomatch") == {}


def test_revision_fields_none_is_all_null_absent() -> None:
    assert revision_fields(None) == {
        "has_active_revision": False,
        "revised_score": None,
        "revised_by": None,
        "revised_at": None,
    }


def test_revision_fields_maps_revision_row(tmp_path: Path) -> None:
    store = _make_store(tmp_path)
    _seed_generation(store)
    assert store.record_rescore_revision("run-a", 1, 0.55, "e2", created_by="jay") is True
    rev = store.latest_active_revisions("run-a", "e2")[1]

    fields = revision_fields(rev)
    assert fields["has_active_revision"] is True
    assert fields["revised_score"] == 0.55
    assert fields["revised_by"] == "jay"
    assert fields["revised_at"] == rev["created_at"]
