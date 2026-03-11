"""Tests for training data export iterator (AC-170)."""
from __future__ import annotations

from pathlib import Path

from mts.storage.artifacts import ArtifactStore
from mts.storage.sqlite_store import SQLiteStore
from mts.training.export import export_training_data
from mts.training.types import MatchRecord, TrainingRecord


def _make_stores(tmp_path: Path) -> tuple[SQLiteStore, ArtifactStore]:
    """Create a SQLiteStore + ArtifactStore pair for testing."""
    db = SQLiteStore(tmp_path / "test.sqlite3")
    db.migrate(Path("migrations"))
    artifacts = ArtifactStore(
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
    )
    return db, artifacts


def _seed_run(
    db: SQLiteStore,
    artifacts: ArtifactStore,
    run_id: str = "run-1",
    scenario: str = "grid_ctf",
    generations: list[dict] | None = None,
) -> None:
    """Seed a run with generations, agent outputs, and optionally matches."""
    db.create_run(run_id, scenario, len(generations or []), "local")
    for gen in generations or []:
        idx = gen["index"]
        db.upsert_generation(
            run_id,
            idx,
            mean_score=gen.get("mean_score", gen.get("score", 0.5)),
            best_score=gen.get("score", 0.5),
            elo=gen.get("elo", 1000.0),
            wins=gen.get("wins", 1),
            losses=gen.get("losses", 0),
            gate_decision=gen.get("gate", "advance"),
            status="completed",
        )
        db.append_agent_output(run_id, idx, "competitor", gen.get("strategy", '{"aggression": 0.5}'))
        for m in gen.get("matches", []):
            db.insert_match(
                run_id,
                idx,
                seed=m["seed"],
                score=m["score"],
                passed_validation=m.get("passed", True),
                validation_errors=m.get("errors", ""),
            )


# ---- Tests ----------------------------------------------------------------


def test_empty_run_returns_empty_iterator(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    db.create_run("empty-run", "grid_ctf", 0, "local")
    records = list(export_training_data(db, artifacts, run_id="empty-run"))
    assert records == []


def test_single_generation_exports_one_record(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.75, "gate": "advance", "strategy": '{"aggression": 0.8}'},
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1"))
    assert len(records) == 1
    rec = records[0]
    assert isinstance(rec, TrainingRecord)
    assert rec.run_id == "run-1"
    assert rec.scenario == "grid_ctf"
    assert rec.generation_index == 1
    assert rec.score == 0.75
    assert rec.gate_decision == "advance"


def test_multi_generation_mixed_gate_decisions(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.4, "gate": "advance"},
        {"index": 2, "score": 0.3, "gate": "retry"},
        {"index": 3, "score": 0.2, "gate": "rollback"},
        {"index": 4, "score": 0.6, "gate": "advance"},
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1"))
    assert len(records) == 4
    gates = [r.gate_decision for r in records]
    assert gates == ["advance", "retry", "rollback", "advance"]


def test_kept_only_excludes_non_advance(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.4, "gate": "advance"},
        {"index": 2, "score": 0.3, "gate": "retry"},
        {"index": 3, "score": 0.2, "gate": "rollback"},
        {"index": 4, "score": 0.6, "gate": "advance"},
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1", kept_only=True))
    assert len(records) == 2
    assert all(isinstance(r, TrainingRecord) for r in records)
    assert all(r.gate_decision == "advance" for r in records)
    assert [r.generation_index for r in records] == [1, 4]


def test_strategy_json_preserved_exactly(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    strategy = '{"aggression": 0.9, "defense": 0.1}'
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.5, "gate": "advance", "strategy": strategy},
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1"))
    assert len(records) == 1
    assert records[0].strategy == strategy


def test_latest_competitor_output_wins_for_generation(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.5, "gate": "advance", "strategy": '{"aggression": 0.1}'},
    ])
    db.append_agent_output("run-1", 1, "competitor", '{"aggression": 0.9}')

    records = list(export_training_data(db, artifacts, run_id="run-1"))
    assert len(records) == 1
    assert records[0].strategy == '{"aggression": 0.9}'


def test_context_fields_populated(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    # Write a playbook and hints so they appear in context
    artifacts.write_playbook("grid_ctf", "## Strategy Guide\nBe aggressive.")
    artifacts.write_hints("grid_ctf", "Focus on flag capture.")
    _seed_run(db, artifacts, generations=[
        {"index": 1, "score": 0.5, "gate": "advance"},
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1"))
    assert len(records) == 1
    ctx = records[0].context
    assert "playbook" in ctx
    assert "Be aggressive" in ctx["playbook"]
    assert "hints" in ctx
    assert "flag capture" in ctx["hints"]


def test_include_matches_yields_match_records(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    _seed_run(db, artifacts, generations=[
        {
            "index": 1,
            "score": 0.75,
            "gate": "advance",
            "matches": [
                {"seed": 42, "score": 0.7, "passed": True, "errors": ""},
                {"seed": 43, "score": 0.8, "passed": True, "errors": ""},
            ],
        },
    ])
    records = list(export_training_data(db, artifacts, run_id="run-1", include_matches=True))
    training_recs = [r for r in records if isinstance(r, TrainingRecord)]
    match_recs = [r for r in records if isinstance(r, MatchRecord)]
    assert len(training_recs) == 1
    assert len(match_recs) == 2
    assert match_recs[0].seed == 42
    assert match_recs[1].seed == 43
    assert match_recs[0].score == 0.7
    assert match_recs[1].passed_validation is True


def test_scenario_filter_exports_all_runs_for_scenario(tmp_path: Path) -> None:
    db, artifacts = _make_stores(tmp_path)
    # Create two runs for grid_ctf
    _seed_run(db, artifacts, run_id="run-a", scenario="grid_ctf", generations=[
        {"index": 1, "score": 0.5, "gate": "advance"},
    ])
    _seed_run(db, artifacts, run_id="run-b", scenario="grid_ctf", generations=[
        {"index": 1, "score": 0.6, "gate": "advance"},
    ])
    # Create one run for othello (should be excluded)
    _seed_run(db, artifacts, run_id="run-c", scenario="othello", generations=[
        {"index": 1, "score": 0.7, "gate": "advance"},
    ])
    records = list(export_training_data(db, artifacts, scenario="grid_ctf"))
    assert len(records) == 2
    run_ids = {r.run_id for r in records}
    assert run_ids == {"run-a", "run-b"}
