"""Tests for Gap 5: Comparative failure recovery lessons with score/strategy details."""
from __future__ import annotations

from pathlib import Path

from autocontext.config import AppSettings
from autocontext.loop import GenerationRunner


def test_rollback_lesson_includes_score_details(tmp_path: Path) -> None:
    """Rollback lesson mentions actual score and delta."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        backpressure_min_delta=0.4,
        max_retries=0,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=2, run_id="rollback_lesson")

    skill_path = tmp_path / "skills" / "grid-ctf-ops" / "SKILL.md"
    assert skill_path.exists()
    content = skill_path.read_text(encoding="utf-8")
    # Must contain actual score information, not just generic "did not improve"
    assert "ROLLBACK" in content
    assert "score=" in content
    assert "delta=" in content


def test_rollback_lesson_includes_strategy_summary(tmp_path: Path) -> None:
    """Rollback lesson references strategy parameters."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        backpressure_min_delta=0.4,
        max_retries=0,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=2, run_id="rollback_strat")

    skill_path = tmp_path / "skills" / "grid-ctf-ops" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # Should reference the actual strategy that failed
    assert "aggression" in content.lower() or "strategy" in content.lower()


def test_advance_lesson_unchanged(tmp_path: Path) -> None:
    """Advance path still uses coach_lessons (not the rollback format)."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=1, run_id="advance_lesson")

    skill_path = tmp_path / "skills" / "grid-ctf-ops" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # Gen 1 always advances (from 0.0); should have coach lessons, not ROLLBACK format
    assert "ROLLBACK" not in content
    # Coach lessons from DeterministicDevClient include defensive strategy advice
    assert "aggression" in content.lower() or "defense" in content.lower()


def test_retry_then_rollback_lesson_mentions_retries(tmp_path: Path) -> None:
    """Lesson notes retry count when retry preceded rollback."""
    settings = AppSettings(
        db_path=tmp_path / "runs" / "autocontext.sqlite3",
        runs_root=tmp_path / "runs",
        knowledge_root=tmp_path / "knowledge",
        skills_root=tmp_path / "skills",
        event_stream_path=tmp_path / "runs" / "events.ndjson",
        seed_base=2000,
        agent_provider="deterministic",
        matches_per_generation=2,
        backpressure_min_delta=0.4,
        max_retries=1,
    )
    runner = GenerationRunner(settings)
    migrations_dir = Path(__file__).resolve().parents[1] / "migrations"
    runner.migrate(migrations_dir)

    runner.run(scenario_name="grid_ctf", generations=2, run_id="retry_rollback")

    skill_path = tmp_path / "skills" / "grid-ctf-ops" / "SKILL.md"
    content = skill_path.read_text(encoding="utf-8")
    # Should mention retries in the lesson
    assert "retr" in content.lower()
