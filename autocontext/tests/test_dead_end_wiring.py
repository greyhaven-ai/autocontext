"""Tests for dead-end pipeline wiring (Issues #158 and #160).

Covers:
- #158: Dead-end entry created on rollback in stage_persistence
- #160: Curator consolidation of dead ends during lesson consolidation
"""
from __future__ import annotations

from unittest.mock import MagicMock

# Break circular import (see test_dead_end_registry.py)
import autocontext.agents  # noqa: F401
from autocontext.agents.types import AgentOutputs
from autocontext.config.settings import AppSettings
from autocontext.harness.evaluation.types import EvaluationResult, EvaluationSummary
from autocontext.loop.stage_types import GenerationContext
from autocontext.loop.stages import stage_persistence

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_persistence_ctx(
    gate_decision: str = "advance",
    coach_playbook: str = "Updated playbook",
    coach_lessons: str = "- Lesson one\n- Lesson two",
    coach_competitor_hints: str = "try aggression=0.9",
    replay_narrative: str = "Player captured the flag at step 5",
    current_strategy: dict | None = None,
    generation: int = 3,
    dead_end_tracking_enabled: bool = False,
    dead_end_max_entries: int = 20,
    curator_enabled: bool = False,
    curator_consolidate_every_n_gens: int = 3,
    skill_max_lessons: int = 30,
) -> GenerationContext:
    """Build a GenerationContext pre-populated for persistence stage tests."""
    settings = AppSettings(
        agent_provider="deterministic",
        dead_end_tracking_enabled=dead_end_tracking_enabled,
        dead_end_max_entries=dead_end_max_entries,
        curator_enabled=curator_enabled,
        curator_consolidate_every_n_gens=curator_consolidate_every_n_gens,
        skill_max_lessons=skill_max_lessons,
    )
    outputs = MagicMock(spec=AgentOutputs)
    outputs.analysis_markdown = "## Analysis output"
    outputs.coach_markdown = "## Coach output"
    outputs.coach_playbook = coach_playbook
    outputs.coach_lessons = coach_lessons
    outputs.coach_competitor_hints = coach_competitor_hints
    outputs.architect_markdown = "## Architect output"

    exec_output_1 = MagicMock()
    exec_output_1.result.score = 0.75
    exec_output_1.result.passed_validation = True
    exec_output_1.result.validation_errors = []
    exec_output_1.replay.model_dump.return_value = {"scenario": "test", "seed": 1001, "timeline": []}

    exec_output_2 = MagicMock()
    exec_output_2.result.score = 0.82
    exec_output_2.result.passed_validation = True
    exec_output_2.result.validation_errors = []
    exec_output_2.replay.model_dump.return_value = {"scenario": "test", "seed": 1002, "timeline": []}

    eval_result_1 = EvaluationResult(
        score=0.75,
        passed=True,
        errors=[],
        metadata={"execution_output": exec_output_1},
    )
    eval_result_2 = EvaluationResult(
        score=0.82,
        passed=True,
        errors=[],
        metadata={"execution_output": exec_output_2},
    )

    tournament = EvaluationSummary(
        mean_score=0.785,
        best_score=0.82,
        wins=2,
        losses=0,
        elo_after=1020.0,
        results=[eval_result_1, eval_result_2],
    )

    strategy = current_strategy or {"aggression": 0.8}

    return GenerationContext(
        run_id="run_persist",
        scenario_name="test_scenario",
        scenario=MagicMock(),
        generation=generation,
        settings=settings,
        previous_best=0.7,
        challenger_elo=1010.0,
        score_history=[0.5, 0.7],
        gate_decision_history=["advance", "advance"],
        coach_competitor_hints="old hints",
        replay_narrative=replay_narrative,
        gate_decision=gate_decision,
        gate_delta=0.12,
        current_strategy=strategy,
        outputs=outputs,
        tournament=tournament,
    )


def _run_stage_persistence(
    ctx: GenerationContext,
    artifacts: MagicMock | None = None,
    curator: MagicMock | None = None,
) -> GenerationContext:
    """Run stage_persistence with mock dependencies."""
    if artifacts is None:
        artifacts = MagicMock()
    artifacts.read_skill_lessons_raw.return_value = []
    sqlite = MagicMock()
    events = MagicMock()
    trajectory = MagicMock()
    return stage_persistence(
        ctx,
        artifacts=artifacts,
        sqlite=sqlite,
        trajectory_builder=trajectory,
        events=events,
        curator=curator,
    )


# ---------------------------------------------------------------------------
# Issue #158: Populate dead-end registry on rollback
# ---------------------------------------------------------------------------


class TestDeadEndOnRollback:
    """Verify dead-end entries are created on rollback when tracking is enabled."""

    def test_dead_end_entry_created_on_rollback_when_enabled(self) -> None:
        """When gate_decision is 'rollback' and dead_end_tracking_enabled is True,
        a DeadEndEntry should be created and appended via artifacts."""
        ctx = _make_persistence_ctx(
            gate_decision="rollback",
            dead_end_tracking_enabled=True,
            current_strategy={"aggression": 0.8, "defense": 0.2},
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        artifacts.append_dead_end.assert_called_once()
        call_args = artifacts.append_dead_end.call_args
        assert call_args[0][0] == "test_scenario"
        # The entry markdown should mention the generation
        entry_md = call_args[0][1]
        assert "Gen 3" in entry_md

    def test_dead_end_not_created_when_tracking_disabled(self) -> None:
        """When dead_end_tracking_enabled is False, no dead-end entry should be created."""
        ctx = _make_persistence_ctx(
            gate_decision="rollback",
            dead_end_tracking_enabled=False,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        artifacts.append_dead_end.assert_not_called()

    def test_dead_end_not_created_on_advance(self) -> None:
        """When gate_decision is 'advance', no dead-end entry should be created."""
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            dead_end_tracking_enabled=True,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        artifacts.append_dead_end.assert_not_called()

    def test_dead_end_not_created_on_retry(self) -> None:
        """When gate_decision is 'retry', no dead-end entry should be created.
        (Retry means we are still trying -- not a confirmed dead end.)"""
        ctx = _make_persistence_ctx(
            gate_decision="retry",
            dead_end_tracking_enabled=True,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        artifacts.append_dead_end.assert_not_called()

    def test_dead_end_entry_contains_strategy_summary(self) -> None:
        """The dead-end entry markdown should contain a summary of the strategy."""
        ctx = _make_persistence_ctx(
            gate_decision="rollback",
            dead_end_tracking_enabled=True,
            current_strategy={"tactic": "rush_center", "intensity": 0.9},
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        entry_md = artifacts.append_dead_end.call_args[0][1]
        # Strategy should be serialized (JSON) into the entry
        assert "rush_center" in entry_md or "tactic" in entry_md

    def test_dead_end_entry_contains_score(self) -> None:
        """The dead-end entry should include the tournament best score."""
        ctx = _make_persistence_ctx(
            gate_decision="rollback",
            dead_end_tracking_enabled=True,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        entry_md = artifacts.append_dead_end.call_args[0][1]
        # tournament.best_score is 0.82 in our mock
        assert "0.82" in entry_md

    def test_dead_end_strategy_summary_truncated_for_long_strategy(self) -> None:
        """Long strategy JSON should be truncated in the dead-end entry."""
        long_strategy = {f"key_{i}": f"value_{i}" for i in range(50)}
        ctx = _make_persistence_ctx(
            gate_decision="rollback",
            dead_end_tracking_enabled=True,
            current_strategy=long_strategy,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        _run_stage_persistence(ctx, artifacts=artifacts)

        entry_md = artifacts.append_dead_end.call_args[0][1]
        # DeadEndEntry.from_rollback truncates at 80 chars + "..."
        # The markdown representation should not be excessively long
        assert "..." in entry_md


# ---------------------------------------------------------------------------
# Issue #160: Curator consolidation of dead ends
# ---------------------------------------------------------------------------


class TestDeadEndConsolidation:
    """Verify dead-end consolidation happens during curator lesson consolidation."""

    def test_dead_ends_consolidated_during_curator_consolidation(self) -> None:
        """When curator lesson consolidation triggers, dead ends should also be consolidated."""
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            generation=3,
            dead_end_tracking_enabled=True,
            dead_end_max_entries=5,
            curator_enabled=True,
            curator_consolidate_every_n_gens=3,
            skill_max_lessons=2,
        )
        artifacts = MagicMock()
        # Return enough lessons to trigger consolidation (> skill_max_lessons)
        artifacts.read_skill_lessons_raw.return_value = ["- lesson 1", "- lesson 2", "- lesson 3"]
        # Return some dead-end entries
        dead_end_content = (
            "# Dead-End Registry\n\n"
            "- **Gen 1**: strat_1 (score=0.1000) -- rolled back\n"
            "- **Gen 2**: strat_2 (score=0.2000) -- rolled back\n"
        )
        artifacts.read_dead_ends.return_value = dead_end_content

        # Mock the curator
        curator = MagicMock()
        lesson_result = MagicMock()
        lesson_result.consolidated_lessons = ["- consolidated lesson"]
        lesson_exec = MagicMock()
        lesson_exec.role = "curator_consolidation"
        lesson_exec.content = "consolidation output"
        lesson_exec.usage.model = "test-model"
        lesson_exec.usage.input_tokens = 100
        lesson_exec.usage.output_tokens = 50
        lesson_exec.usage.latency_ms = 500
        lesson_exec.subagent_id = None
        lesson_exec.status = "completed"
        curator.consolidate_lessons.return_value = (lesson_result, lesson_exec)

        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=curator,
        )

        # Dead ends should have been read
        artifacts.read_dead_ends.assert_called_once_with("test_scenario")
        # And consolidated + written back
        artifacts.replace_dead_ends.assert_called_once()
        replace_args = artifacts.replace_dead_ends.call_args
        assert replace_args[0][0] == "test_scenario"

    def test_dead_end_consolidation_respects_max_entries(self) -> None:
        """Consolidation should use dead_end_max_entries from settings."""
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            generation=3,
            dead_end_tracking_enabled=True,
            dead_end_max_entries=2,
            curator_enabled=True,
            curator_consolidate_every_n_gens=3,
            skill_max_lessons=2,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = ["- l1", "- l2", "- l3"]
        # Create more entries than max_entries
        lines = [
            f"- **Gen {i}**: strat_{i} (score=0.{i:04d}) -- rolled back"
            for i in range(5)
        ]
        dead_end_content = "# Dead-End Registry\n\n" + "\n".join(lines) + "\n"
        artifacts.read_dead_ends.return_value = dead_end_content

        curator = MagicMock()
        lesson_result = MagicMock()
        lesson_result.consolidated_lessons = ["- consolidated"]
        lesson_exec = MagicMock()
        lesson_exec.role = "curator_consolidation"
        lesson_exec.content = "output"
        lesson_exec.usage.model = "test-model"
        lesson_exec.usage.input_tokens = 10
        lesson_exec.usage.output_tokens = 10
        lesson_exec.usage.latency_ms = 100
        lesson_exec.subagent_id = None
        lesson_exec.status = "completed"
        curator.consolidate_lessons.return_value = (lesson_result, lesson_exec)

        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=curator,
        )

        # The consolidated content should only keep the most recent entries (max_entries=2)
        replace_args = artifacts.replace_dead_ends.call_args
        consolidated_content = replace_args[0][1]
        assert "strat_3" in consolidated_content
        assert "strat_4" in consolidated_content
        assert "strat_0" not in consolidated_content

    def test_dead_end_consolidation_skipped_when_tracking_disabled(self) -> None:
        """When dead_end_tracking_enabled is False, no dead-end consolidation."""
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            generation=3,
            dead_end_tracking_enabled=False,
            curator_enabled=True,
            curator_consolidate_every_n_gens=3,
            skill_max_lessons=2,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = ["- l1", "- l2", "- l3"]

        curator = MagicMock()
        lesson_result = MagicMock()
        lesson_result.consolidated_lessons = ["- consolidated"]
        lesson_exec = MagicMock()
        lesson_exec.role = "curator_consolidation"
        lesson_exec.content = "output"
        lesson_exec.usage.model = "test-model"
        lesson_exec.usage.input_tokens = 10
        lesson_exec.usage.output_tokens = 10
        lesson_exec.usage.latency_ms = 100
        lesson_exec.subagent_id = None
        lesson_exec.status = "completed"
        curator.consolidate_lessons.return_value = (lesson_result, lesson_exec)

        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=curator,
        )

        # read_dead_ends and replace_dead_ends should NOT be called
        artifacts.read_dead_ends.assert_not_called()
        artifacts.replace_dead_ends.assert_not_called()

    def test_dead_end_consolidation_skipped_when_no_curator_consolidation(self) -> None:
        """When curator consolidation does not trigger, skip dead ends too."""
        # Generation 4, with consolidate_every_n_gens=3 and not severely over
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            generation=4,
            dead_end_tracking_enabled=True,
            curator_enabled=True,
            curator_consolidate_every_n_gens=3,
            skill_max_lessons=30,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = []

        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=MagicMock(),
        )

        artifacts.read_dead_ends.assert_not_called()
        artifacts.replace_dead_ends.assert_not_called()

    def test_dead_end_consolidation_with_empty_registry(self) -> None:
        """When dead-end registry is empty, consolidation is a no-op."""
        ctx = _make_persistence_ctx(
            gate_decision="advance",
            generation=3,
            dead_end_tracking_enabled=True,
            dead_end_max_entries=5,
            curator_enabled=True,
            curator_consolidate_every_n_gens=3,
            skill_max_lessons=2,
        )
        artifacts = MagicMock()
        artifacts.read_skill_lessons_raw.return_value = ["- l1", "- l2", "- l3"]
        artifacts.read_dead_ends.return_value = ""

        curator = MagicMock()
        lesson_result = MagicMock()
        lesson_result.consolidated_lessons = ["- consolidated"]
        lesson_exec = MagicMock()
        lesson_exec.role = "curator_consolidation"
        lesson_exec.content = "output"
        lesson_exec.usage.model = "test-model"
        lesson_exec.usage.input_tokens = 10
        lesson_exec.usage.output_tokens = 10
        lesson_exec.usage.latency_ms = 100
        lesson_exec.subagent_id = None
        lesson_exec.status = "completed"
        curator.consolidate_lessons.return_value = (lesson_result, lesson_exec)

        sqlite = MagicMock()
        events = MagicMock()
        trajectory = MagicMock()

        stage_persistence(
            ctx,
            artifacts=artifacts,
            sqlite=sqlite,
            trajectory_builder=trajectory,
            events=events,
            curator=curator,
        )

        # read_dead_ends should be called, but replace_dead_ends should NOT
        # because there is nothing to consolidate
        artifacts.read_dead_ends.assert_called_once_with("test_scenario")
        artifacts.replace_dead_ends.assert_not_called()
