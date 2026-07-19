from __future__ import annotations

import threading
from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner
from autocontext.loop.controller import LoopController

MIGRATIONS_DIR = Path(__file__).resolve().parents[1] / "migrations"


def _make_runner(tmp_path: Path) -> GenerationRunner:
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    runner.migrate(MIGRATIONS_DIR)
    return runner


def _record_events(runner: GenerationRunner) -> list[tuple[str, dict]]:
    recorded: list[tuple[str, dict]] = []
    runner.events.subscribe(lambda event, payload: recorded.append((event, payload)))
    return recorded


def test_stop_before_first_boundary_marks_stopped(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    recorded = _record_events(runner)
    controller = LoopController()
    runner.controller = controller
    controller.request_stop("c1", "operator requested")

    run_id = "stop_first_run"
    runner.run(scenario_name="grid_ctf", generations=2, run_id=run_id)

    run = runner.sqlite.get_run(run_id)
    assert run is not None
    assert run["status"] == "stopped"

    stop_events = [payload for event, payload in recorded if event == "run_stopped"]
    assert len(stop_events) == 1
    assert stop_events[0]["command_id"] == "c1"
    assert stop_events[0]["reason"] == "operator requested"


def test_run_completes_when_no_stop_requested(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    recorded = _record_events(runner)
    runner.controller = LoopController()

    run_id = "no_stop_run"
    runner.run(scenario_name="grid_ctf", generations=1, run_id=run_id)

    run = runner.sqlite.get_run(run_id)
    assert run is not None
    assert run["status"] == "completed"
    assert not any(event == "run_stopped" for event, _ in recorded)


def test_stop_from_paused_wakes_loop_and_stops(tmp_path: Path) -> None:
    runner = _make_runner(tmp_path)
    recorded = _record_events(runner)
    controller = LoopController()
    runner.controller = controller

    started = threading.Event()
    runner.events.subscribe(lambda event, _payload: started.set() if event == "run_started" else None)

    controller.pause()

    run_id = "stop_paused_run"

    def _drive() -> None:
        runner.run(scenario_name="grid_ctf", generations=2, run_id=run_id)

    thread = threading.Thread(target=_drive)
    thread.start()

    # Wait for the loop to enter the run (it then parks at wait_if_paused).
    assert started.wait(timeout=10.0)
    # Requesting a stop both flips the flag and wakes the parked thread.
    controller.request_stop("c2", "stop while paused")
    thread.join(timeout=10.0)
    assert not thread.is_alive()

    run = runner.sqlite.get_run(run_id)
    assert run is not None
    assert run["status"] == "stopped"
    stop_events = [payload for event, payload in recorded if event == "run_stopped"]
    assert len(stop_events) == 1
    assert stop_events[0]["command_id"] == "c2"
