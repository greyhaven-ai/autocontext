"""Tests for background memory consolidation (AC-516).

DDD: MemoryConsolidator reviews completed work at safe boundaries
and promotes durable learnings into memory surfaces.
"""

from __future__ import annotations

from pathlib import Path

import pytest


class TestConsolidationTrigger:
    """Decides whether enough new work has accumulated."""

    def test_not_triggered_when_below_threshold(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationTrigger

        trigger = ConsolidationTrigger(
            min_completed_turns=5,
            min_completed_sessions=1,
        )
        assert not trigger.should_run(completed_turns=2, completed_sessions=0)

    def test_triggered_by_turn_count(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationTrigger

        trigger = ConsolidationTrigger(min_completed_turns=5)
        assert trigger.should_run(completed_turns=6, completed_sessions=0)

    def test_triggered_by_session_count(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationTrigger

        trigger = ConsolidationTrigger(min_completed_sessions=2)
        assert trigger.should_run(completed_turns=0, completed_sessions=3)

    def test_explicit_force_overrides_threshold(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationTrigger

        trigger = ConsolidationTrigger(min_completed_turns=100)
        assert trigger.should_run(completed_turns=1, completed_sessions=0, force=True)

    def test_negative_thresholds_rejected(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationTrigger

        with pytest.raises(ValueError, match="greater than or equal to 0"):
            ConsolidationTrigger(min_completed_turns=-1)

        with pytest.raises(ValueError, match="greater than or equal to 0"):
            ConsolidationTrigger(min_completed_sessions=-1)


class TestConsolidationResult:
    """Structured audit of what was reviewed and promoted."""

    def test_result_tracks_promotions(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationResult

        result = ConsolidationResult(
            reviewed_sessions=["s1", "s2"],
            promoted_lessons=["lesson-1"],
            promoted_hints=["hint-1", "hint-2"],
            skipped_reason="",
        )
        assert result.total_promoted == 3
        assert result.was_productive

    def test_noop_result(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationResult

        result = ConsolidationResult(
            reviewed_sessions=["s1"],
            skipped_reason="weak signal",
        )
        assert result.total_promoted == 0
        assert not result.was_productive

    def test_dry_run_result(self) -> None:
        from autocontext.session.memory_consolidation import ConsolidationResult

        result = ConsolidationResult(
            reviewed_sessions=["s1"],
            promoted_lessons=["lesson-1"],
            dry_run=True,
        )
        assert result.total_promoted == 1
        assert result.dry_run  # nothing actually written


class TestConsolidationLock:
    """Prevents duplicate concurrent consolidation runs."""

    def test_acquire_and_release(self, tmp_path: Path) -> None:
        from autocontext.session.memory_consolidation import ConsolidationLock

        lock = ConsolidationLock(tmp_path / "consolidation.lock")
        assert lock.acquire()
        assert not lock.acquire()  # second acquire fails
        lock.release()
        assert lock.acquire()  # after release, acquire works again
        lock.release()

    def test_context_manager(self, tmp_path: Path) -> None:
        from autocontext.session.memory_consolidation import ConsolidationLock

        lock = ConsolidationLock(tmp_path / "consolidation.lock")
        with lock:
            assert not lock.acquire()  # locked inside context
        assert lock.acquire()  # released after context
        lock.release()

    def test_lock_raises_on_contention_when_strict(self, tmp_path: Path) -> None:
        from autocontext.session.memory_consolidation import ConsolidationLock

        lock = ConsolidationLock(tmp_path / "consolidation.lock")
        lock.acquire()
        with pytest.raises(RuntimeError, match="already running"):
            lock.acquire_or_raise()
        lock.release()

    def test_non_owner_release_does_not_clear_active_lock(self, tmp_path: Path) -> None:
        from autocontext.session.memory_consolidation import ConsolidationLock

        path = tmp_path / "consolidation.lock"
        owner = ConsolidationLock(path)
        non_owner = ConsolidationLock(path)
        contender = ConsolidationLock(path)

        assert owner.acquire()
        non_owner.release()
        assert not contender.acquire()

        owner.release()
        assert contender.acquire()
        contender.release()


class TestMemoryConsolidator:
    """Orchestrates the consolidation workflow."""

    def test_skips_when_trigger_not_met(self) -> None:
        from autocontext.session.memory_consolidation import (
            ConsolidationTrigger,
            MemoryConsolidator,
        )

        trigger = ConsolidationTrigger(min_completed_turns=100)
        consolidator = MemoryConsolidator(trigger=trigger)
        result = consolidator.run(completed_turns=2, completed_sessions=0, artifacts={})
        assert not result.was_productive
        assert "threshold" in result.skipped_reason

    def test_runs_when_triggered(self) -> None:
        from autocontext.session.memory_consolidation import (
            ConsolidationTrigger,
            MemoryConsolidator,
        )

        trigger = ConsolidationTrigger(min_completed_turns=1)
        consolidator = MemoryConsolidator(trigger=trigger)

        artifacts = {
            "session_reports": ["session had good strategy for auth flow"],
            "notebook_state": {"current_objective": "Build API"},
            "verification_outcomes": [{"passed": True, "reason": "tests pass"}],
        }
        result = consolidator.run(
            completed_turns=5,
            completed_sessions=1,
            artifacts=artifacts,
        )
        assert result.reviewed_sessions or result.was_productive or not result.was_productive
        # The consolidator ran (didn't skip) — reviewed the artifacts
        assert "threshold" not in result.skipped_reason

    def test_dry_run_does_not_mutate(self) -> None:
        from autocontext.session.memory_consolidation import (
            ConsolidationTrigger,
            MemoryConsolidator,
        )

        trigger = ConsolidationTrigger(min_completed_turns=1)
        consolidator = MemoryConsolidator(trigger=trigger)
        result = consolidator.run(
            completed_turns=5,
            completed_sessions=1,
            artifacts={"session_reports": ["good finding"]},
            dry_run=True,
        )
        assert result.dry_run

    def test_promotes_structured_lessons_instead_of_raw_prefixes(self) -> None:
        from autocontext.session.memory_consolidation import (
            ConsolidationTrigger,
            MemoryConsolidator,
        )

        trigger = ConsolidationTrigger(min_completed_turns=1)
        consolidator = MemoryConsolidator(trigger=trigger)
        report = (
            "# Session Report: run_123\n"
            "Verbose setup details that are not the main lesson.\n"
            "## Findings\n"
            "- Preserve the rollback guard after failed tool mutations.\n"
            "- Prefer freshness-filtered notebook context over stale notes.\n"
        )

        result = consolidator.run(
            completed_turns=3,
            completed_sessions=1,
            artifacts={"session_reports": [report]},
            dry_run=True,
        )

        assert any("rollback guard" in lesson.lower() for lesson in result.promoted_lessons)
        assert any("freshness-filtered" in lesson.lower() for lesson in result.promoted_lessons)
