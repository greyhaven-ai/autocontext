"""AC-174: Tests for generation time budget feature."""
from __future__ import annotations

import os
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from autocontext.config.settings import AppSettings, load_settings
from autocontext.loop.generation_pipeline import _over_budget, _time_remaining
from autocontext.loop.stage_types import GenerationContext

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_ctx(
    tmp_path: Path,
    *,
    budget: int = 0,
    start_time: float | None = None,
    **overrides: object,
) -> GenerationContext:
    """Build a minimal GenerationContext for testing budget helpers."""
    settings = AppSettings(
        agent_provider="deterministic",
        db_path=tmp_path / "test.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        generation_time_budget_seconds=budget,
    )
    scenario = MagicMock()
    ctx = GenerationContext(
        run_id="test_run",
        scenario_name="grid_ctf",
        scenario=scenario,
        generation=1,
        settings=settings,
        previous_best=0.0,
        challenger_elo=1000.0,
        score_history=[],
        gate_decision_history=[],
        coach_competitor_hints="",
        replay_narrative="",
    )
    if start_time is not None:
        ctx.generation_start_time = start_time
    return ctx


# ---------------------------------------------------------------------------
# Settings
# ---------------------------------------------------------------------------


class TestSettings:
    def test_default_is_zero(self) -> None:
        """generation_time_budget_seconds defaults to 0 (unlimited)."""
        settings = AppSettings()
        assert settings.generation_time_budget_seconds == 0

    def test_env_var_override(self) -> None:
        """AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS loads from env."""
        with patch.dict(os.environ, {"AUTOCONTEXT_GENERATION_TIME_BUDGET_SECONDS": "60"}):
            settings = load_settings()
        assert settings.generation_time_budget_seconds == 60


# ---------------------------------------------------------------------------
# GenerationContext fields
# ---------------------------------------------------------------------------


class TestContextFields:
    def test_timing_fields_exist(self, tmp_path: Path) -> None:
        """GenerationContext has generation_start_time and generation_elapsed_seconds."""
        ctx = _make_ctx(tmp_path)
        assert ctx.generation_start_time == 0.0
        assert ctx.generation_elapsed_seconds == 0.0


# ---------------------------------------------------------------------------
# Budget helpers
# ---------------------------------------------------------------------------


class TestBudgetHelpers:
    def test_over_budget_false_when_unlimited(self, tmp_path: Path) -> None:
        """_over_budget returns False when budget=0 (unlimited)."""
        ctx = _make_ctx(tmp_path, budget=0, start_time=time.monotonic())
        assert _over_budget(ctx) is False

    def test_over_budget_false_when_within_budget(self, tmp_path: Path) -> None:
        """_over_budget returns False when still within budget."""
        ctx = _make_ctx(tmp_path, budget=300, start_time=time.monotonic())
        assert _over_budget(ctx) is False

    def test_over_budget_true_when_exceeded(self, tmp_path: Path) -> None:
        """_over_budget returns True when start time is far in the past."""
        ctx = _make_ctx(tmp_path, budget=1, start_time=time.monotonic() - 100)
        assert _over_budget(ctx) is True

    def test_time_remaining_none_when_unlimited(self, tmp_path: Path) -> None:
        """_time_remaining returns None when budget=0."""
        ctx = _make_ctx(tmp_path, budget=0, start_time=time.monotonic())
        assert _time_remaining(ctx) is None

    def test_time_remaining_positive_within_budget(self, tmp_path: Path) -> None:
        """_time_remaining returns positive float when within budget."""
        ctx = _make_ctx(tmp_path, budget=300, start_time=time.monotonic())
        remaining = _time_remaining(ctx)
        assert remaining is not None
        assert remaining > 0

    def test_time_remaining_zero_when_exceeded(self, tmp_path: Path) -> None:
        """_time_remaining returns 0 when budget is exceeded (clamped)."""
        ctx = _make_ctx(tmp_path, budget=1, start_time=time.monotonic() - 100)
        remaining = _time_remaining(ctx)
        assert remaining == 0.0


# ---------------------------------------------------------------------------
# Pipeline behavior
# ---------------------------------------------------------------------------


class TestPipelineBudget:
    def test_pipeline_skips_optional_stages_when_over_budget(self, tmp_path: Path) -> None:
        """Budget exhaustion before tournament causes a rollback without matches."""
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            generation_time_budget_seconds=1,  # very short budget
            coherence_check_enabled=True,
            probe_matches=3,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))

        # Patch time.monotonic so the pipeline immediately appears over-budget
        # after agent generation (which always runs)
        real_monotonic = time.monotonic
        call_count = 0

        def _fast_forward_monotonic() -> float:
            nonlocal call_count
            call_count += 1
            # First call sets generation_start_time normally
            # Subsequent calls appear 1000s later → over budget
            if call_count <= 1:
                return real_monotonic()
            return real_monotonic() + 1000

        events_emitted: list[tuple[str, dict]] = []
        original_emit = runner.events.emit

        def _capture_emit(event: str, payload: dict) -> None:
            events_emitted.append((event, payload))
            original_emit(event, payload)

        runner.events.emit = _capture_emit  # type: ignore[assignment]

        with patch("autocontext.loop.generation_pipeline.time") as mock_time:
            mock_time.monotonic = _fast_forward_monotonic
            summary = runner.run("grid_ctf", generations=1, run_id="budget_test")

        assert summary.generations_executed == 1

        # Verify budget exhaustion was emitted and tournament work was skipped.
        budget_events = [e for e in events_emitted if e[0] == "generation_budget_exhausted"]
        assert len(budget_events) == 1
        tournament_events = [e for e in events_emitted if e[0] == "tournament_started"]
        assert tournament_events == []

        metrics = runner.sqlite.get_generation_metrics("budget_test")
        assert len(metrics) == 1
        assert metrics[0]["gate_decision"] == "rollback"
        assert metrics[0]["duration_seconds"] is not None
        assert metrics[0]["duration_seconds"] > 0

        # Verify timing event was emitted.
        timing_events = [e for e in events_emitted if e[0] == "generation_timing"]
        assert len(timing_events) == 1
        assert timing_events[0][1]["budget_seconds"] == 1
        assert timing_events[0][1]["over_budget"] is True

    def test_pipeline_runs_all_stages_when_unlimited(self, tmp_path: Path) -> None:
        """All stages run when budget=0 (unlimited)."""
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
            generation_time_budget_seconds=0,
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))
        summary = runner.run("grid_ctf", generations=1, run_id="unlimited_test")
        assert summary.generations_executed == 1
        assert summary.best_score >= 0.0

    def test_pipeline_emits_timing_event(self, tmp_path: Path) -> None:
        """run_generation emits a generation_timing event."""
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))

        events_emitted: list[tuple[str, dict]] = []
        original_emit = runner.events.emit

        def _capture_emit(event: str, payload: dict) -> None:
            events_emitted.append((event, payload))
            original_emit(event, payload)

        runner.events.emit = _capture_emit  # type: ignore[assignment]
        runner.run("grid_ctf", generations=1, run_id="timing_test")

        timing_events = [e for e in events_emitted if e[0] == "generation_timing"]
        assert len(timing_events) == 1

        payload = timing_events[0][1]
        assert payload["run_id"] == "timing_test"
        assert payload["generation"] == 1
        assert payload["elapsed_seconds"] > 0
        assert payload["budget_seconds"] == 0
        assert payload["over_budget"] is False

    def test_pipeline_persists_completed_generation_duration(self, tmp_path: Path) -> None:
        """Completed generations store a non-null elapsed duration."""
        from autocontext.loop.generation_runner import GenerationRunner

        settings = AppSettings(
            agent_provider="deterministic",
            db_path=tmp_path / "test.sqlite3",
            runs_root=tmp_path / "runs",
            knowledge_root=tmp_path / "knowledge",
            skills_root=tmp_path / "skills",
        )
        runner = GenerationRunner(settings)
        runner.migrate(Path("migrations"))

        summary = runner.run("grid_ctf", generations=1, run_id="duration_test")
        assert summary.generations_executed == 1

        metrics = runner.sqlite.get_generation_metrics("duration_test")
        assert len(metrics) == 1
        assert metrics[0]["duration_seconds"] is not None
        assert metrics[0]["duration_seconds"] > 0


# ---------------------------------------------------------------------------
# SQLite store
# ---------------------------------------------------------------------------


class TestSQLiteStore:
    def test_upsert_generation_accepts_duration(self, tmp_path: Path) -> None:
        """upsert_generation stores duration_seconds."""
        from autocontext.storage.sqlite_store import SQLiteStore

        db_path = tmp_path / "test.sqlite3"
        store = SQLiteStore(db_path)
        store.migrate(Path("migrations"))

        store.create_run("r1", "grid_ctf", 1, "local")
        store.upsert_generation(
            "r1", 1,
            mean_score=0.5, best_score=0.5,
            elo=1000.0, wins=1, losses=0,
            gate_decision="advance", status="completed",
            duration_seconds=5.25,
        )

        metrics = store.get_generation_metrics("r1")
        assert len(metrics) == 1
        assert metrics[0]["duration_seconds"] == pytest.approx(5.25)

    def test_upsert_generation_duration_defaults_none(self, tmp_path: Path) -> None:
        """duration_seconds defaults to None when not provided."""
        from autocontext.storage.sqlite_store import SQLiteStore

        db_path = tmp_path / "test.sqlite3"
        store = SQLiteStore(db_path)
        store.migrate(Path("migrations"))

        store.create_run("r2", "grid_ctf", 1, "local")
        store.upsert_generation(
            "r2", 1,
            mean_score=0.5, best_score=0.5,
            elo=1000.0, wins=1, losses=0,
            gate_decision="advance", status="completed",
        )

        metrics = store.get_generation_metrics("r2")
        assert len(metrics) == 1
        assert metrics[0]["duration_seconds"] is None


# ---------------------------------------------------------------------------
# Migration
# ---------------------------------------------------------------------------


class TestMigration:
    def test_migration_adds_duration_column(self, tmp_path: Path) -> None:
        """Migration 009 adds duration_seconds column to generations table."""
        from autocontext.storage.sqlite_store import SQLiteStore

        db_path = tmp_path / "test.sqlite3"
        store = SQLiteStore(db_path)
        store.migrate(Path("migrations"))

        with store.connect() as conn:
            cursor = conn.execute("PRAGMA table_info(generations)")
            columns = {row["name"] for row in cursor.fetchall()}

        assert "duration_seconds" in columns
