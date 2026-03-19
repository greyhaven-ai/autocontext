"""Tests for wiring session report generation into GenerationRunner.run() completion flow.

Issue #178: Trigger report generation on run completion.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

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
    runner.sqlite.get_generation_metrics.return_value = []
    runner.sqlite.get_agent_role_metrics.return_value = []
    runner.sqlite.get_staged_validation_results_for_run.return_value = []
    runner.sqlite.get_consultations_for_run.return_value = []
    runner.sqlite.get_recovery_markers_for_run.return_value = []
    runner.sqlite.get_total_consultation_cost.return_value = 0.0

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


class TestWeaknessReportWiring:
    """Weakness reports are generated from the live run completion path."""

    def test_weakness_report_generated_on_run_completion(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)

        trajectory_rows = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.0, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.1, "elo": 980, "delta": -0.2, "gate_decision": "rollback"},
            {"generation_index": 3, "best_score": 0.2, "elo": 990, "delta": 0.1, "gate_decision": "advance"},
            {"generation_index": 4, "best_score": 0.05, "elo": 960, "delta": -0.15, "gate_decision": "rollback"},
            {"generation_index": 5, "best_score": 0.04, "elo": 950, "delta": -0.01, "gate_decision": "rollback"},
        ]
        match_rows = [
            {"generation_index": 2, "score": 0.1, "passed_validation": False, "validation_errors": '["bad action"]'},
            {"generation_index": 4, "score": 0.05, "passed_validation": False, "validation_errors": '["bad action"]'},
        ]
        mocks["sqlite"].get_generation_metrics.return_value = [
            {
                "generation_index": 1,
                "mean_score": 0.3,
                "best_score": 0.3,
                "elo": 1000.0,
                "wins": 1,
                "losses": 0,
                "gate_decision": "advance",
                "status": "completed",
                "duration_seconds": 12.0,
                "created_at": "2026-03-15T11:00:00Z",
                "updated_at": "2026-03-15T11:00:12Z",
            },
            {
                "generation_index": 2,
                "mean_score": 0.1,
                "best_score": 0.1,
                "elo": 980.0,
                "wins": 0,
                "losses": 1,
                "gate_decision": "rollback",
                "status": "completed",
                "duration_seconds": 14.0,
                "created_at": "2026-03-15T11:01:00Z",
                "updated_at": "2026-03-15T11:01:14Z",
            },
        ]
        mocks["sqlite"].get_staged_validation_results_for_run.return_value = [
            {
                "generation_index": 2,
                "stage_order": 1,
                "stage_name": "syntax",
                "status": "failed",
                "duration_ms": 15,
                "error": "bad action",
                "error_code": "parse",
                "created_at": "2026-03-15T11:01:02Z",
            }
        ]
        mocks["sqlite"].get_recovery_markers_for_run.return_value = [
            {
                "generation_index": 2,
                "decision": "retry",
                "reason": "validator failed",
                "retry_count": 1,
                "created_at": "2026-03-15T11:01:04Z",
            }
        ]
        mocks["sqlite"].get_generation_trajectory.return_value = trajectory_rows
        mocks["sqlite"].get_matches_for_run.return_value = match_rows
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 5, "test_weakness")

        mocks["artifacts"].write_weakness_report.assert_called_once()
        call_args = mocks["artifacts"].write_weakness_report.call_args
        assert call_args[0][0] == "grid_ctf"
        assert call_args[0][1] == "test_weakness"
        report = call_args[0][2]
        assert report.run_id == "test_weakness"
        assert report.metadata["scenario"] == "grid_ctf"
        assert report.metadata["report_source"] == "trace_grounded"
        assert report.weaknesses


class TestProgressReportWiring:
    """Normalized progress reports are generated from the live run completion path."""

    def test_progress_report_generated_on_run_completion(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)

        trajectory_rows = [
            {"generation_index": 1, "best_score": 0.3, "elo": 1000, "delta": 0.3, "gate_decision": "advance"},
            {"generation_index": 2, "best_score": 0.5, "elo": 1050, "delta": 0.2, "gate_decision": "advance"},
        ]
        role_metrics = [
            {
                "generation_index": 1,
                "role": "competitor",
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 1000,
                "output_tokens": 500,
                "latency_ms": 100,
                "subagent_id": "competitor",
                "status": "success",
            }
        ]
        mocks["sqlite"].get_generation_trajectory.return_value = trajectory_rows
        mocks["sqlite"].get_agent_role_metrics.return_value = role_metrics
        mocks["sqlite"].get_total_consultation_cost.return_value = 0.01
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 2, "test_progress")

        mocks["artifacts"].write_progress_report.assert_called_once()
        call_args = mocks["artifacts"].write_progress_report.call_args
        assert call_args[0][0] == "grid_ctf"
        assert call_args[0][1] == "test_progress"
        report = call_args[0][2]
        assert report.run_id == "test_progress"
        assert report.cost.total_tokens == 1500
        assert report.cost.total_cost_usd > 0


class TestAggregateAnalyticsWiring:
    """Aggregate facets and clustering are generated from the live run completion path."""

    def test_aggregate_analytics_persisted_on_run_completion(self, tmp_path: Path) -> None:
        from autocontext.analytics.facets import FrictionSignal, RunFacet
        from autocontext.analytics.store import FacetStore

        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)

        facet_store = FacetStore(tmp_path / "knowledge")
        for idx, score in ((1, 0.35), (2, 0.45)):
            facet_store.persist(
                RunFacet(
                    run_id=f"seed-run-{idx}",
                    scenario="grid_ctf",
                    scenario_family="game",
                    agent_provider="deterministic",
                    executor_mode="local",
                    total_generations=2,
                    advances=1,
                    retries=1,
                    rollbacks=0,
                    best_score=score,
                    best_elo=1000.0,
                    total_duration_seconds=10.0,
                    total_tokens=1000,
                    total_cost_usd=0.01,
                    tool_invocations=1,
                    validation_failures=1,
                    consultation_count=0,
                    consultation_cost_usd=0.0,
                    friction_signals=[
                        FrictionSignal(
                            signal_type="validation_failure",
                            severity="medium",
                            generation_index=1,
                            description="parse error",
                            evidence=["seed"],
                        )
                    ],
                    delight_signals=[],
                    events=[],
                    metadata={"release": "v1.1.0"},
                    created_at=f"2026-03-14T0{idx}:00:00Z",
                )
            )

        mocks["sqlite"].get_generation_metrics.return_value = [
            {
                "generation_index": 1,
                "mean_score": 0.3,
                "best_score": 0.5,
                "elo": 1020.0,
                "gate_decision": "advance",
                "status": "completed",
                "duration_seconds": 12.0,
            },
            {
                "generation_index": 2,
                "mean_score": 0.95,
                "best_score": 0.98,
                "elo": 1180.0,
                "gate_decision": "advance",
                "status": "completed",
                "duration_seconds": 14.0,
            },
        ]
        mocks["sqlite"].get_agent_role_metrics.return_value = [
            {
                "generation_index": 1,
                "role": "competitor",
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 1000,
                "output_tokens": 500,
                "latency_ms": 100,
                "subagent_id": "competitor",
                "status": "success",
            }
        ]
        mocks["sqlite"].get_staged_validation_results_for_run.return_value = [
            {
                "generation_index": 2,
                "stage_order": 1,
                "stage_name": "syntax",
                "status": "failed",
                "duration_ms": 10,
                "error": "parse error",
                "error_code": "parse",
            }
        ]
        mocks["sqlite"].get_consultations_for_run.return_value = []
        mocks["sqlite"].get_recovery_markers_for_run.return_value = [
            {
                "generation_index": 2,
                "decision": "retry",
                "reason": "validator failed",
                "retry_count": 1,
            }
        ]
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        with patch("autocontext.loop.generation_runner._current_release_version", return_value="v1.1.0"):
            _run_with_pipeline_mock(runner, mocks, "grid_ctf", 2, "test_aggregate")

        assert (tmp_path / "knowledge" / "facets" / "test_aggregate.json").exists()
        assert (tmp_path / "knowledge" / "analytics" / "friction_clusters.json").exists()
        assert (tmp_path / "knowledge" / "analytics" / "delight_clusters.json").exists()
        assert (tmp_path / "knowledge" / "analytics" / "taxonomy.json").exists()
        assert list((tmp_path / "knowledge" / "analytics" / "correlations").glob("*.json"))
        assert list((tmp_path / "knowledge" / "analytics" / "issues").glob("*.json"))
        assert list((tmp_path / "knowledge" / "analytics" / "probes").glob("*.json"))
        assert list((tmp_path / "knowledge" / "analytics" / "drift_snapshots").glob("*.json"))
        assert list((tmp_path / "knowledge" / "analytics" / "drift_warnings").glob("*.json"))
        assert list((tmp_path / "knowledge" / "analytics" / "calibration_rounds").glob("*.json"))


class TestRunTraceWiring:
    """Canonical traces and inspection artifacts are generated from the live run path."""

    def test_run_trace_and_inspection_persisted_on_run_completion(self, tmp_path: Path) -> None:
        import json

        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_metrics.return_value = [
            {
                "generation_index": 1,
                "mean_score": 0.4,
                "best_score": 0.55,
                "elo": 1040.0,
                "wins": 3,
                "losses": 1,
                "gate_decision": "advance",
                "status": "completed",
                "duration_seconds": 12.0,
                "created_at": "2026-03-15T10:00:00Z",
                "updated_at": "2026-03-15T10:00:12Z",
            },
            {
                "generation_index": 2,
                "mean_score": 0.35,
                "best_score": 0.55,
                "elo": 1035.0,
                "wins": 2,
                "losses": 2,
                "gate_decision": "retry",
                "status": "completed",
                "duration_seconds": 10.0,
                "created_at": "2026-03-15T10:01:00Z",
                "updated_at": "2026-03-15T10:01:10Z",
            },
        ]
        mocks["sqlite"].get_agent_role_metrics.return_value = [
            {
                "generation_index": 1,
                "role": "competitor",
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 900,
                "output_tokens": 450,
                "latency_ms": 120,
                "subagent_id": "competitor",
                "status": "success",
                "created_at": "2026-03-15T10:00:01Z",
            },
            {
                "generation_index": 2,
                "role": "analyst",
                "model": "claude-sonnet-4-5-20250929",
                "input_tokens": 400,
                "output_tokens": 200,
                "latency_ms": 90,
                "subagent_id": "analyst",
                "status": "success",
                "created_at": "2026-03-15T10:01:01Z",
            },
        ]
        mocks["sqlite"].get_staged_validation_results_for_run.return_value = [
            {
                "generation_index": 2,
                "stage_order": 1,
                "stage_name": "syntax",
                "status": "failed",
                "duration_ms": 15,
                "error": "parse error",
                "error_code": "parse",
                "created_at": "2026-03-15T10:01:02Z",
            }
        ]
        mocks["sqlite"].get_consultations_for_run.return_value = [
            {
                "id": 1,
                "run_id": "test_trace",
                "generation_index": 2,
                "trigger": "judge_uncertainty",
                "context_summary": "low confidence",
                "critique": "strategy inconsistent",
                "alternative_hypothesis": "reduce risk",
                "tiebreak_recommendation": "retry",
                "suggested_next_action": "simplify",
                "raw_response": "consultation",
                "model_used": "gpt-5.4",
                "cost_usd": 0.02,
                "created_at": "2026-03-15T10:01:03Z",
            }
        ]
        mocks["sqlite"].get_recovery_markers_for_run.return_value = [
            {
                "generation_index": 2,
                "decision": "retry",
                "reason": "validator failed",
                "retry_count": 1,
                "created_at": "2026-03-15T10:01:04Z",
            }
        ]
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 2, "test_trace")

        trace_path = tmp_path / "knowledge" / "analytics" / "traces" / "trace-test_trace.json"
        inspection_path = tmp_path / "knowledge" / "analytics" / "inspections" / "trace-test_trace.json"
        assert trace_path.exists()
        assert inspection_path.exists()

        trace_payload = json.loads(trace_path.read_text(encoding="utf-8"))
        event_types = {event["event_type"] for event in trace_payload["events"]}
        categories = {event["category"] for event in trace_payload["events"]}
        assert "generation_summary" in event_types
        assert "consultation" in event_types
        assert "recovery" in categories
        assert trace_payload["causal_edges"]

        inspection_payload = json.loads(inspection_path.read_text(encoding="utf-8"))
        assert inspection_payload["run_inspection"]["total_events"] == len(trace_payload["events"])
        assert inspection_payload["timeline_summary"]
        assert inspection_payload["failure_paths"] or inspection_payload["recovery_paths"]


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


class TestMutationLogWiring:
    """Mutation log is appended and checkpointed from the live runner path."""

    def test_run_appends_outcome_and_creates_checkpoint(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)
        mocks["artifacts"].mutation_log = MagicMock()
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        with patch("autocontext.loop.generation_pipeline.GenerationPipeline") as mock_pipeline_cls:
            mock_pipeline = MagicMock()
            mock_pipeline_cls.return_value = mock_pipeline

            def fake_run_gen(ctx: Any) -> Any:
                ctx.gate_decision = "advance"
                ctx.previous_best = 0.42
                ctx.challenger_elo = 1042.0
                return ctx

            mock_pipeline.run_generation.side_effect = fake_run_gen

            with patch.object(runner, "_scenario") as mock_scenario:
                mock_scenario.return_value = mocks["scenario"]
                runner.run("grid_ctf", generations=1, run_id="test_mutation_success")

        mocks["artifacts"].mutation_log.append.assert_called_once()
        append_args = mocks["artifacts"].mutation_log.append.call_args
        assert append_args[0][0] == "grid_ctf"
        entry = append_args[0][1]
        assert entry.mutation_type == "run_outcome"
        assert entry.generation == 1
        assert entry.run_id == "test_mutation_success"
        assert entry.payload == {
            "gate_decision": "advance",
            "best_score": 0.42,
            "elo": 1042.0,
            "scoring_backend": "elo",
            "rating_uncertainty": None,
        }

        mocks["artifacts"].mutation_log.create_checkpoint.assert_called_once_with(
            "grid_ctf",
            generation=1,
            run_id="test_mutation_success",
        )

    def test_run_logs_failed_generation_outcome(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, session_reports_enabled=False)
        runner, mocks = _make_runner_with_mocks(settings)
        mocks["artifacts"].mutation_log = MagicMock()
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        with patch("autocontext.loop.generation_pipeline.GenerationPipeline") as mock_pipeline_cls:
            mock_pipeline = MagicMock()
            mock_pipeline_cls.return_value = mock_pipeline
            mock_pipeline.run_generation.side_effect = RuntimeError("boom")

            with patch.object(runner, "_scenario") as mock_scenario:
                mock_scenario.return_value = mocks["scenario"]
                with pytest.raises(RuntimeError, match="boom"):
                    runner.run("grid_ctf", generations=1, run_id="test_mutation_failure")

        mocks["artifacts"].mutation_log.append.assert_called_once()
        append_args = mocks["artifacts"].mutation_log.append.call_args
        assert append_args[0][0] == "grid_ctf"
        entry = append_args[0][1]
        assert entry.mutation_type == "run_outcome"
        assert entry.generation == 1
        assert entry.run_id == "test_mutation_failure"
        assert entry.payload == {"status": "failed", "error": "boom"}
        mocks["artifacts"].mutation_log.create_checkpoint.assert_not_called()


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


class TestSessionReportLessonHealth:
    """Structured lesson health is included when lessons exist."""

    def test_report_includes_structured_lesson_health(self, tmp_path: Path) -> None:
        settings = _make_settings(tmp_path, session_reports_enabled=True)
        runner, mocks = _make_runner_with_mocks(settings)

        mocks["sqlite"].get_generation_trajectory.return_value = [
            {"generation_index": 12, "best_score": 0.7, "elo": 1100, "delta": 0.1, "gate_decision": "advance"},
        ]
        mocks["artifacts"].read_dead_ends.return_value = ""
        mocks["artifacts"].tools_dir.return_value = MagicMock(exists=MagicMock(return_value=True))

        stale_lesson = MagicMock()
        stale_lesson.is_superseded.return_value = False
        superseded_lesson = MagicMock()
        superseded_lesson.is_superseded.return_value = True

        mocks["artifacts"].lesson_store.read_lessons.return_value = [stale_lesson, superseded_lesson]
        mocks["artifacts"].lesson_store.get_stale_lessons.return_value = [stale_lesson]

        _run_with_pipeline_mock(runner, mocks, "grid_ctf", 1, "test_lesson_health")

        markdown = mocks["artifacts"].write_session_report.call_args[0][2]
        assert "Lesson Health" in markdown
        assert "Stale lessons: 1" in markdown
        assert "Superseded lessons: 1" in markdown
