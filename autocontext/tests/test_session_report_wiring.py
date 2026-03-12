"""Tests for wiring session report generation into GenerationRunner.run() completion flow.

Issue #178: Trigger report generation on run completion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

from autocontext.config.settings import AppSettings


def _make_settings(tmp_path: Path, **overrides: Any) -> AppSettings:
    """Create AppSettings pointing at temp dirs."""
    defaults: dict[str, Any] = {
        "db_path": tmp_path / "runs" / "autocontext.sqlite3",
        "runs_root": tmp_path / "runs",
        "knowledge_root": tmp_path / "knowledge",
        "skills_root": tmp_path / "skills",
        "claude_skills_path": tmp_path / ".claude" / "skills",
        "event_stream_path": tmp_path / "runs" / "events.ndjson",
        "agent_provider": "deterministic",
        "cross_run_inheritance": False,
        "session_reports_enabled": True,
        "exploration_mode": "linear",
    }
    defaults.update(overrides)
    return AppSettings(**defaults)


def _make_runner_with_mocks(settings: AppSettings) -> tuple[Any, dict[str, Any]]:
    """Create a GenerationRunner with all heavy dependencies mocked out.

    Returns (runner, mocks_dict) where mocks_dict has keys:
    sqlite, artifacts, agents, executor, events, gate, trajectory_builder, scenario.
    """
    from autocontext.loop.generation_runner import GenerationRunner

    with patch.object(GenerationRunner, "__init__", lambda self, s: None):
        runner = GenerationRunner.__new__(GenerationRunner)

    runner.settings = settings
    runner.sqlite = MagicMock()
    runner.artifacts = MagicMock()
    runner.agents = MagicMock()
    runner.executor = MagicMock()
    runner.events = MagicMock()
    runner.gate = MagicMock()
    runner.trajectory_builder = MagicMock()
    runner.controller = None
    runner.remote = None
    runner._meta_optimizer = MagicMock()

    # Default: no existing generations (not skipped for idempotency)
    runner.sqlite.generation_exists.return_value = False

    # Default: scenario lookup succeeds
    scenario_mock = MagicMock()
    scenario_mock.seed_tools.return_value = {}

    mocks = {
        "sqlite": runner.sqlite,
        "artifacts": runner.artifacts,
        "agents": runner.agents,
        "executor": runner.executor,
        "events": runner.events,
        "gate": runner.gate,
        "trajectory_builder": runner.trajectory_builder,
        "scenario": scenario_mock,
    }

    return runner, mocks


def _run_with_pipeline_mock(
    runner: Any,
    mocks: dict[str, Any],
    scenario_name: str,
    generations: int,
    run_id: str,
) -> Any:
    """Run the runner with GenerationPipeline and _scenario mocked."""
    with patch("autocontext.loop.generation_pipeline.GenerationPipeline") as mock_pipeline_cls:
        mock_pipeline = MagicMock()
        mock_pipeline_cls.return_value = mock_pipeline

        def fake_run_gen(ctx: Any) -> Any:
            return ctx
        mock_pipeline.run_generation.side_effect = fake_run_gen

        with patch.object(runner, "_scenario") as mock_scenario:
            mock_scenario.return_value = mocks["scenario"]
            return runner.run(scenario_name, generations=generations, run_id=run_id)


class TestSessionReportOnRunCompletion:
    """Report is generated when session_reports_enabled=True and run completes."""

    def test_report_generated_when_enabled(self, tmp_path: Path) -> None:
        """When session_reports_enabled=True and run completes, a report should be
        generated and written to the artifact store."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        trajectory_rows = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.5, "elo": 1050, "delta": 0.2, "gate_decision": "advance"},
        ]
        mocks["sqlite"].get_generation_trajectory.return_value = trajectory_rows
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 2, "test_run_001")

        # Verify write_session_report was called
        mocks["artifacts"].write_session_report.assert_called_once()
        call_args = mocks["artifacts"].write_session_report.call_args
        assert call_args[0][0] == "grid_ctf"  # scenario_name
        assert call_args[0][1] == "test_run_001"  # run_id
        markdown_content = call_args[0][2]
        assert "# Session Report: test_run_001" in markdown_content

    def test_report_not_generated_when_disabled(self, tmp_path: Path) -> None:
        """When session_reports_enabled=False, no report should be generated."""
        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_run_disabled")

        # write_session_report should NOT have been called
        mocks["artifacts"].write_session_report.assert_not_called()


class TestSessionReportTrajectoryData:
    """Report includes correct trajectory data from SQLite."""

    def test_report_uses_trajectory_from_sqlite(self, tmp_path: Path) -> None:
        """The report should be built from get_generation_trajectory() data."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        trajectory_rows = [
            {"generation_index": 1, "best_score": 0.2, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.2, "elo": 1000, "delta": 0.0, "gate_decision": "retry"},
            {"generation_index": 3, "best_score": 0.6, "elo": 1100, "delta": 0.4, "gate_decision": "advance"},
        ]
        mocks["sqlite"].get_generation_trajectory.return_value = trajectory_rows
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 3, "test_traj")

        # Verify get_generation_trajectory was called with the right run_id
        mocks["sqlite"].get_generation_trajectory.assert_called_with("test_traj")

        # Verify the markdown contains expected trajectory information
        call_args = mocks["artifacts"].write_session_report.call_args
        markdown = call_args[0][2]
        assert "0.2000" in markdown  # start score
        assert "0.6000" in markdown  # end score
        assert "2 advances" in markdown
        assert "1 retries" in markdown


class TestSessionReportPersistence:
    """Report is persisted to artifact store."""

    def test_report_written_via_artifacts(self, tmp_path: Path) -> None:
        """write_session_report should be called with scenario, run_id, and markdown."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "othello", 1, "test_persist")

        mocks["artifacts"].write_session_report.assert_called_once()
        call_args = mocks["artifacts"].write_session_report.call_args
        assert call_args[0][0] == "othello"
        assert call_args[0][1] == "test_persist"
        assert isinstance(call_args[0][2], str)
        assert len(call_args[0][2]) > 0


class TestSessionReportEmptyTrajectory:
    """Handles empty trajectory (0 completed generations) gracefully."""

    def test_report_generated_with_empty_trajectory(self, tmp_path: Path) -> None:
        """Even if no generations completed, a report should still be generated."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_empty")

        mocks["artifacts"].write_session_report.assert_called_once()
        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        assert "# Session Report: test_empty" in markdown
        assert "Generations: 0" in markdown
        assert "No significant improvements recorded." in markdown


class TestSessionReportDuration:
    """Duration is calculated correctly from run start to report generation time."""

    def test_duration_is_positive(self, tmp_path: Path) -> None:
        """The report duration should be a positive number reflecting wall-clock time."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_duration")

        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        # Duration should appear in the report -- it will be very small (< 1s) in test
        assert "Duration:" in markdown


class TestSessionReportDeadEnds:
    """Dead ends count is read from the dead_ends.md file."""

    def test_dead_ends_counted_from_file(self, tmp_path: Path) -> None:
        """When dead_ends.md has entries, the count is passed to the report."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
        ]
        # dead_ends.md with 3 entries
        mocks["artifacts"].read_dead_ends.return_value = (
            "### Dead End\n\nEntry one\n\n"
            "### Dead End\n\nEntry two\n\n"
            "### Dead End\n\nEntry three\n"
        )
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_dead_ends")

        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        assert "3 dead ends identified" in markdown

    def test_no_dead_ends_file(self, tmp_path: Path) -> None:
        """When dead_ends.md is empty, 0 dead ends are reported."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_no_dead_ends")

        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        assert "0 dead ends identified" in markdown


class TestSessionReportExplorationMode:
    """Exploration mode from settings is included in the report."""

    def test_exploration_mode_passed_to_report(self, tmp_path: Path) -> None:
        """The exploration_mode from settings should be included in the report."""
        settings = _make_settings(tmp_path, session_reports_enabled=True, exploration_mode="rapid")
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_rapid")

        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        assert "rapid" in markdown


class TestSessionReportPlacement:
    """Report generation happens after mark_run_completed but before run_completed event."""

    def test_report_generated_after_mark_completed(self, tmp_path: Path) -> None:
        """Verify the ordering: mark_run_completed -> report generation -> run_completed event."""
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = []
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        call_order: list[str] = []
        mocks["sqlite"].mark_run_completed.side_effect = lambda *a: call_order.append("mark_completed")
        mocks["artifacts"].write_session_report.side_effect = lambda *a: call_order.append("write_report")

        def track_event(name: str, data: Any) -> None:
            if name == "run_completed":
                call_order.append("run_completed_event")

        mocks["events"].emit.side_effect = track_event

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_order")

        assert "mark_completed" in call_order
        assert "write_report" in call_order
        assert "run_completed_event" in call_order

        mark_idx = call_order.index("mark_completed")
        report_idx = call_order.index("write_report")
        event_idx = call_order.index("run_completed_event")

        assert mark_idx < report_idx, "Report should be written after mark_run_completed"
        assert report_idx < event_idx, "Report should be written before run_completed event"
