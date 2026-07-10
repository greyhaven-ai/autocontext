"""Training-export excludes quarantined scores by default (AC-885 Slice C3)."""

from __future__ import annotations

from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.training.export import export_training_data
from autocontext.training.types import MatchRecord, TrainingRecord


def _setup(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore]:
    db = SQLiteStore(tmp_path / "t.sqlite3")
    db.migrate(Path("migrations"))
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    db.create_run("run-1", "grid_ctf", 2, "local")
    db.upsert_generation(
        "run-1",
        1,
        mean_score=0.9,
        best_score=0.9,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="completed",
        status="completed",
        evaluator_epoch="e-2",
        quarantined=True,
    )
    db.upsert_generation(
        "run-1",
        2,
        mean_score=0.8,
        best_score=0.8,
        elo=0.0,
        wins=0,
        losses=0,
        gate_decision="advance",
        status="completed",
        evaluator_epoch="e-1",
    )
    return db, artifacts


def test_quarantined_excluded_by_default(tmp_path: Path) -> None:
    store, artifacts = _setup(tmp_path)
    recs = [r for r in export_training_data(store, artifacts, run_id="run-1") if isinstance(r, TrainingRecord)]
    idxs = {r.generation_index for r in recs}
    assert 2 in idxs and 1 not in idxs  # quarantined gen 1 excluded, trusted gen 2 kept


def test_include_quarantined_flag(tmp_path: Path) -> None:
    store, artifacts = _setup(tmp_path)
    recs = [
        r
        for r in export_training_data(store, artifacts, run_id="run-1", include_quarantined=True)
        if isinstance(r, TrainingRecord)
    ]
    assert {r.generation_index for r in recs} == {1, 2}


def test_quarantined_matches_survive_exclusion(tmp_path: Path) -> None:
    # A quarantined generation's TrainingRecord is excluded by default, but its
    # tournament matches (scored without an evaluator epoch) must still be exported.
    store, artifacts = _setup(tmp_path)
    store.insert_match("run-1", 1, seed=7, score=0.5, passed_validation=True, validation_errors="")

    records = list(export_training_data(store, artifacts, run_id="run-1", include_matches=True))
    training = [r for r in records if isinstance(r, TrainingRecord)]
    matches = [r for r in records if isinstance(r, MatchRecord)]

    assert {r.generation_index for r in training} == {2}  # quarantined gen 1 record excluded
    assert any(m.generation_index == 1 and m.seed == 7 for m in matches)  # its match survives


def _trajectory_indices(record: TrainingRecord) -> set[int]:
    return {entry["generation_index"] for entry in record.context["trajectory"]}


def test_quarantined_score_does_not_leak_into_trusted_trajectory(tmp_path: Path) -> None:
    # Excluding the quarantined generation's own record is not enough: its score must also not
    # ride along inside a later trusted record's trajectory context (the central enforcement leak).
    store, artifacts = _setup(tmp_path)
    recs = [r for r in export_training_data(store, artifacts, run_id="run-1") if isinstance(r, TrainingRecord)]
    gen2 = next(r for r in recs if r.generation_index == 2)
    assert _trajectory_indices(gen2) == {2}  # quarantined gen 1 absent from the trusted trajectory


def test_include_quarantined_restores_trajectory(tmp_path: Path) -> None:
    store, artifacts = _setup(tmp_path)
    recs = [
        r
        for r in export_training_data(store, artifacts, run_id="run-1", include_quarantined=True)
        if isinstance(r, TrainingRecord)
    ]
    gen2 = next(r for r in recs if r.generation_index == 2)
    assert _trajectory_indices(gen2) == {1, 2}  # opt-in keeps the quarantined generation in context
