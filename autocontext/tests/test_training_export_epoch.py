"""Training-export records carry evaluator_epoch (AC-885 Slice B)."""

from __future__ import annotations

from pathlib import Path

from autocontext.storage.artifacts import ArtifactStore
from autocontext.storage.sqlite_store import SQLiteStore
from autocontext.training.export import export_training_data
from autocontext.training.types import TrainingRecord


def _make_stores(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore]:
    db = SQLiteStore(tmp_path / "t.sqlite3")
    db.migrate(Path("migrations"))
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return db, artifacts


def test_training_record_carries_epoch(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    db.create_run("run-1", "grid_ctf", 1, "local")
    db.upsert_generation(
        "run-1",
        1,
        mean_score=0.9,
        best_score=0.9,
        elo=1000.0,
        wins=1,
        losses=0,
        gate_decision="advance",
        status="completed",
        evaluator_epoch="e-1",
    )
    db.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.5}')

    recs = [r for r in export_training_data(db, artifacts, run_id="run-1") if isinstance(r, TrainingRecord)]
    assert recs
    assert recs[0].evaluator_epoch == "e-1"
