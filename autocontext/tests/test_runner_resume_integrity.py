"""Existing-run re-entry keeps a run's scenario and finishes its stored target."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from autocontext.config import AppSettings
from autocontext.extensions import HookEvents, HookResult
from autocontext.loop import GenerationRunner
from autocontext.loop.generation_pipeline import GenerationPipeline
from autocontext.scenarios import SCENARIO_REGISTRY

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


def _block_run_start(runner: GenerationRunner) -> None:
    runner.hook_bus.on(HookEvents.RUN_START, lambda event: HookResult(block=True, reason="policy says no"))


def _record_run_ends(runner: GenerationRunner) -> list[dict]:
    ends: list[dict] = []
    runner.hook_bus.on(HookEvents.RUN_END, lambda event: ends.append(dict(event.payload)))
    return ends


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


def test_blocked_run_start_of_a_new_run_writes_no_row(tmp_path: Path) -> None:
    # A refused start used to land after create_run: a 'running' row with no process and an unmatched run_started.
    runner = _runner(tmp_path)
    _block_run_start(runner)
    events: list[str] = []
    runner.events.subscribe(lambda event, _payload: events.append(event))

    with pytest.raises(RuntimeError, match="blocked run_start: policy says no"):
        runner.run(scenario_name="othello", generations=1, run_id="r")

    assert runner.sqlite.get_run("r") is None
    assert "run_started" not in events


def test_blocked_extension_of_a_completed_run_keeps_it_completed_at_its_old_target(tmp_path: Path) -> None:
    # The refused extension used to reopen the row with the new target, so the next plain resume ran it anyway.
    runner = _runner(tmp_path)
    runner.sqlite.create_run("r", "othello", 2, "local")
    _seed_generation(runner, "r", 1, "completed")
    _seed_generation(runner, "r", 2, "completed")
    runner.sqlite.mark_run_completed("r")
    _block_run_start(runner)

    with pytest.raises(RuntimeError, match="blocked run_start"):
        runner.run(scenario_name="othello", generations=3, run_id="r")

    assert _state(tmp_path, "r") == (("othello", 2, "completed"), [(1, "completed"), (2, "completed")])


def test_blocked_resume_of_an_interrupted_run_leaves_it_failed(tmp_path: Path) -> None:
    # Recovery marked it failed, then mark_run_running flipped it back to 'running' before the hook refused.
    runner = _runner(tmp_path)
    runner.sqlite.create_run("r", "othello", 2, "local")
    _seed_generation(runner, "r", 1, "completed")
    _block_run_start(runner)

    with pytest.raises(RuntimeError, match="blocked run_start"):
        runner.run(scenario_name="othello", generations=2, run_id="r")

    assert _state(tmp_path, "r") == (("othello", 2, "failed"), [(1, "completed")])


@pytest.mark.parametrize("failing_step", ["hydrate", "seed_tools"])
def test_setup_failure_after_the_row_is_written_fails_the_run_and_fires_run_end(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, failing_step: str
) -> None:
    # Setup before the loop (hydration, tool seeding, knowledge restore) ran outside the recovery try.
    runner = _runner(tmp_path)

    def fail(*_args: object) -> None:
        raise RuntimeError("injected setup failure")

    if failing_step == "hydrate":
        monkeypatch.setattr(runner, "_hydrate_run_state", fail)
    else:
        monkeypatch.setattr(SCENARIO_REGISTRY["othello"], "seed_tools", fail)
    ends = _record_run_ends(runner)

    with pytest.raises(RuntimeError, match="injected setup failure"):
        runner.run(scenario_name="othello", generations=1, run_id="r")

    assert _state(tmp_path, "r") == (("othello", 1, "failed"), [])
    assert [(end["status"], end["error"]) for end in ends] == [("failed", "injected setup failure")]
