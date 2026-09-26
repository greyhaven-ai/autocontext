"""Existing-run re-entry keeps a run's scenario and finishes its stored target."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.loop.generation_pipeline import GenerationPipeline

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _runner(tmp_path: Path) -> GenerationRunner:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        audit_log_path=tmp_path / "runs" / "audit.ndjson",
        agent_provider="deterministic",
        matches_per_generation=1,
    )
    runner = GenerationRunner(settings)
    runner.migrate(MIGRATIONS_DIR)
    return runner


def _state(tmp_path: Path, run_id: str) -> tuple[tuple, list[tuple]]:
    conn = sqlite3.connect(tmp_path / "runs" / "autocontext.sqlite3")
    try:
        run = conn.execute("SELECT scenario, target_generations, status FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        gens = conn.execute(
            "SELECT generation_index, status FROM generations WHERE run_id = ? ORDER BY generation_index", (run_id,)
        ).fetchall()
    finally:
        conn.close()
    return run, gens


def _seed_generation(runner: GenerationRunner, run_id: str, index: int, status: str) -> None:
    runner.sqlite.upsert_generation(
        run_id, index, mean_score=0.5, best_score=0.5, elo=1000.0, wins=0, losses=0, gate_decision=status, status=status
    )


def _fail_generation(monkeypatch: pytest.MonkeyPatch, index: int) -> None:
    original = GenerationPipeline.run_generation

    def failing(self, ctx):  # type: ignore[no-untyped-def]
        if ctx.generation == index:
            raise RuntimeError(f"injected generation {index} failure")
        return original(self, ctx)

    monkeypatch.setattr(GenerationPipeline, "run_generation", failing)


def test_reentry_with_another_scenario_is_rejected_before_any_write(tmp_path: Path) -> None:
    # An interrupted run: recovery would rewrite it, so this also pins the guard before recovery.
    runner = _runner(tmp_path)
    runner.sqlite.create_run("r", "othello", 2, "local")
    _seed_generation(runner, "r", 1, "completed")
    _seed_generation(runner, "r", 2, "running")
    before = _state(tmp_path, "r")

    with pytest.raises(ValueError, match="belongs to scenario 'othello', not 'grid_ctf'"):
        runner.run(scenario_name="grid_ctf", generations=2, run_id="r")

    assert before == (("othello", 2, "running"), [(1, "completed"), (2, "running")])
    assert _state(tmp_path, "r") == before
    assert runner.sqlite.get_recovery_markers_for_run("r") == []
    assert not (tmp_path / "knowledge" / "grid_ctf").exists()


@pytest.mark.parametrize("executor_mode", ["agent_task", "artifact_editing", "import"])
def test_reentry_of_a_run_the_generation_loop_did_not_create_is_rejected(tmp_path: Path, executor_mode: str) -> None:
    # Rows written outside the loop can stay 'running', where recovery would rewrite them.
    runner = _runner(tmp_path)
    runner.sqlite.create_run("r", "othello", 1, executor_mode)
    _seed_generation(runner, "r", 1, "completed")

    with pytest.raises(ValueError, match="not created by the generation loop"):
        runner.run(scenario_name="othello", generations=2, run_id="r")

    assert _state(tmp_path, "r") == (("othello", 1, "running"), [(1, "completed")])
    assert runner.sqlite.get_recovery_markers_for_run("r") == []


@pytest.mark.slow
def test_reentry_with_fewer_generations_finishes_the_stored_target(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.run(scenario_name="othello", generations=2, run_id="r")
    runner.sqlite.update_generation_status("r", 2, status="failed", gate_decision="error")
    runner.sqlite.mark_run_failed("r")

    summary = runner.run(scenario_name="othello", generations=1, run_id="r")

    assert summary.generations_executed == 1
    assert _state(tmp_path, "r") == (("othello", 2, "completed"), [(1, "completed"), (2, "completed")])


@pytest.mark.slow
def test_rerun_of_a_finished_run_stays_completed_without_reopening(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner(tmp_path)
    runner.run(scenario_name="othello", generations=2, run_id="r")
    reopened: list[str] = []
    monkeypatch.setattr(runner.sqlite, "mark_run_running", lambda run_id, **_: reopened.append(run_id))

    summary = runner.run(scenario_name="othello", generations=2, run_id="r")

    assert summary.generations_executed == 0
    assert reopened == []
    assert _state(tmp_path, "r")[0] == ("othello", 2, "completed")


@pytest.mark.slow
def test_extending_a_completed_run_persists_the_new_target(tmp_path: Path) -> None:
    runner = _runner(tmp_path)
    runner.run(scenario_name="othello", generations=1, run_id="r")

    summary = runner.run(scenario_name="othello", generations=2, run_id="r")

    assert summary.generations_executed == 1
    assert _state(tmp_path, "r") == (("othello", 2, "completed"), [(1, "completed"), (2, "completed")])


@pytest.mark.slow
def test_failed_extension_of_a_completed_run_marks_it_failed(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    runner = _runner(tmp_path)
    runner.run(scenario_name="othello", generations=1, run_id="r")
    _fail_generation(monkeypatch, 2)

    with pytest.raises(RuntimeError, match="injected generation 2"):
        runner.run(scenario_name="othello", generations=2, run_id="r")

    assert _state(tmp_path, "r") == (("othello", 2, "failed"), [(1, "completed"), (2, "failed")])


@pytest.mark.slow
def test_failed_retry_of_a_completed_run_with_a_failed_generation_marks_it_failed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # A run left 'completed' with a failed generation (the state earlier releases wrote).
    runner = _runner(tmp_path)
    runner.run(scenario_name="othello", generations=2, run_id="r")
    runner.sqlite.update_generation_status("r", 2, status="failed", gate_decision="error")
    _fail_generation(monkeypatch, 2)

    with pytest.raises(RuntimeError, match="injected generation 2"):
        runner.run(scenario_name="othello", generations=2, run_id="r")

    assert _state(tmp_path, "r")[0] == ("othello", 2, "failed")
