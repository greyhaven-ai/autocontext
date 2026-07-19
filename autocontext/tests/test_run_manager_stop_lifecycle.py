from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.harness.core.events import EventStreamEmitter
from autocontext.loop.controller import LoopController
from autocontext.server.run_manager import RunManager
from autocontext.storage import SQLiteStore


def _make_settings(tmp_path: Path) -> AppSettings:
    return AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        claude_skills_path=tmp_path / ".claude" / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        monitor_enabled=False,
    )


def test_start_run_clears_a_leaked_stop_from_a_prior_run(tmp_path: Path) -> None:
    # The controller is reused across runs. A stop that terminated an earlier run
    # leaves _stop_requested set; without a reset, the next run would die at its
    # first boundary. start_run must clear it so a fresh run completes normally.
    settings = _make_settings(tmp_path)
    controller = LoopController()
    events = EventStreamEmitter(settings.event_stream_path)
    recorded: list[tuple[str, dict]] = []
    events.subscribe(lambda event, payload: recorded.append((event, payload)))

    # Simulate a stop that terminated a previous run and was never cleared.
    controller.request_stop("stale-command", "prior run")
    assert controller.stop_requested()

    manager = RunManager(controller, events, settings)
    run_id = manager.start_run("grid_ctf", generations=1, client_run_id="fresh-run")
    assert manager._thread is not None
    manager._thread.join(timeout=30.0)
    assert manager._thread is not None and not manager._thread.is_alive()

    store = SQLiteStore(settings.db_path)
    run = store.get_run(run_id)
    assert run is not None
    # The fresh run must complete, not inherit the stale stop.
    assert run["status"] == "completed"
    # No stop receipt should have fired for the fresh run.
    assert not any(event == "run_stopped" for event, _ in recorded)
    # And the controller's stop flag was reset at start.
    assert not controller.stop_requested()
