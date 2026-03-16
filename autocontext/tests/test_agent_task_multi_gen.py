"""Tests for AC-281: multi-generation support for AgentTask scenarios.

Covers: AgentTaskGenerationState, accumulate_lessons, build_enriched_prompt,
AgentTaskTrajectory, ScenarioFamilyGuide, AgentTaskEvolutionRunner.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_judge_result(
    score: float = 0.6,
    reasoning: str = "Needs more depth and examples",
    dimension_scores: dict[str, float] | None = None,
) -> Any:
    from autocontext.scenarios.agent_task import AgentTaskResult

    return AgentTaskResult(
        score=score,
        reasoning=reasoning,
        dimension_scores=dimension_scores or {},
    )


# ===========================================================================
# AgentTaskGenerationState
# ===========================================================================


class TestAgentTaskGenerationState:
    def test_construction(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskGenerationState

        state = AgentTaskGenerationState(
            generation=0,
            best_output="Initial output",
            best_score=0.0,
            playbook="",
            score_history=[],
            lesson_history=[],
        )
        assert state.generation == 0
        assert state.best_output == "Initial output"
        assert state.playbook == ""

    def test_roundtrip(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskGenerationState

        state = AgentTaskGenerationState(
            generation=3,
            best_output="Improved output v3",
            best_score=0.82,
            playbook="## Lessons\n- Depth matters",
            score_history=[0.5, 0.65, 0.82],
            lesson_history=["Add examples", "Improve structure", "More depth"],
        )
        d = state.to_dict()
        restored = AgentTaskGenerationState.from_dict(d)
        assert restored.generation == 3
        assert restored.best_score == 0.82
        assert len(restored.score_history) == 3


# ===========================================================================
# accumulate_lessons
# ===========================================================================


class TestAccumulateLessons:
    def test_extracts_lesson_from_judge_feedback(self) -> None:
        from autocontext.execution.agent_task_evolution import accumulate_lessons

        result = _make_judge_result(
            score=0.6,
            reasoning="Good structure but lacking concrete examples and evidence",
            dimension_scores={"depth": 0.4, "evidence": 0.3, "clarity": 0.9},
        )
        lesson = accumulate_lessons(result, generation=1)
        assert len(lesson) > 0
        assert "0.60" in lesson or "0.6" in lesson

    def test_includes_weak_dimensions(self) -> None:
        from autocontext.execution.agent_task_evolution import accumulate_lessons

        result = _make_judge_result(
            score=0.5,
            reasoning="Weak on accuracy",
            dimension_scores={"accuracy": 0.3, "style": 0.8},
        )
        lesson = accumulate_lessons(result, generation=2)
        assert "accuracy" in lesson.lower()

    def test_empty_reasoning_still_produces_lesson(self) -> None:
        from autocontext.execution.agent_task_evolution import accumulate_lessons

        result = _make_judge_result(score=0.4, reasoning="", dimension_scores={})
        lesson = accumulate_lessons(result, generation=0)
        assert len(lesson) > 0

    def test_high_score_produces_positive_lesson(self) -> None:
        from autocontext.execution.agent_task_evolution import accumulate_lessons

        result = _make_judge_result(
            score=0.92,
            reasoning="Excellent work, thorough and well-structured",
        )
        lesson = accumulate_lessons(result, generation=5)
        assert "0.92" in lesson


# ===========================================================================
# build_enriched_prompt
# ===========================================================================


class TestBuildEnrichedPrompt:
    def test_includes_task_prompt(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        prompt = build_enriched_prompt(
            task_prompt="Write a security audit report.",
            playbook="",
            generation=0,
            best_output="",
            best_score=0.0,
        )
        assert "security audit report" in prompt.lower()

    def test_includes_playbook_when_present(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        prompt = build_enriched_prompt(
            task_prompt="Write a report.",
            playbook="## Lessons\n- Always cite sources\n- Use specific examples",
            generation=3,
            best_output="Previous best output here",
            best_score=0.75,
        )
        assert "cite sources" in prompt.lower()
        assert "specific examples" in prompt.lower()

    def test_includes_best_output_reference(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        prompt = build_enriched_prompt(
            task_prompt="Write a report.",
            playbook="Some lessons",
            generation=2,
            best_output="The best output from gen 1",
            best_score=0.7,
        )
        assert "best output from gen 1" in prompt.lower()

    def test_empty_playbook_omits_section(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        prompt = build_enriched_prompt(
            task_prompt="Write something.",
            playbook="",
            generation=0,
            best_output="",
            best_score=0.0,
        )
        assert "accumulated lessons" not in prompt.lower()

    def test_includes_generation_number(self) -> None:
        from autocontext.execution.agent_task_evolution import build_enriched_prompt

        prompt = build_enriched_prompt(
            task_prompt="Task.",
            playbook="Lessons here",
            generation=5,
            best_output="output",
            best_score=0.8,
        )
        assert "5" in prompt or "generation 5" in prompt.lower()


# ===========================================================================
# AgentTaskTrajectory
# ===========================================================================


class TestAgentTaskTrajectory:
    def test_construction(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskTrajectory

        traj = AgentTaskTrajectory(
            task_name="security_audit",
            total_generations=5,
            score_history=[0.45, 0.58, 0.67, 0.75, 0.82],
            lessons_per_generation=[1, 1, 1, 1, 1],
            cold_start_score=0.45,
            final_score=0.82,
            improvement_delta=0.37,
        )
        assert traj.total_generations == 5
        assert traj.improvement_delta == 0.37

    def test_roundtrip(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskTrajectory

        traj = AgentTaskTrajectory(
            task_name="test",
            total_generations=3,
            score_history=[0.5, 0.6, 0.7],
            lessons_per_generation=[1, 1, 1],
            cold_start_score=0.5,
            final_score=0.7,
            improvement_delta=0.2,
        )
        d = traj.to_dict()
        restored = AgentTaskTrajectory.from_dict(d)
        assert restored.cold_start_score == 0.5
        assert restored.final_score == 0.7

    def test_cold_vs_warm_comparison(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskTrajectory

        traj = AgentTaskTrajectory(
            task_name="test",
            total_generations=5,
            score_history=[0.40, 0.55, 0.65, 0.72, 0.80],
            lessons_per_generation=[1, 1, 1, 1, 1],
            cold_start_score=0.40,
            final_score=0.80,
            improvement_delta=0.40,
        )
        comparison = traj.cold_vs_warm_summary()
        assert "0.40" in comparison or "0.4" in comparison
        assert "0.80" in comparison or "0.8" in comparison


# ===========================================================================
# ScenarioFamilyGuide
# ===========================================================================


class TestScenarioFamilyGuide:
    def test_construction(self) -> None:
        from autocontext.execution.agent_task_evolution import ScenarioFamilyGuide

        guide = ScenarioFamilyGuide()
        assert len(guide.families) > 0

    def test_includes_agent_task(self) -> None:
        from autocontext.execution.agent_task_evolution import ScenarioFamilyGuide

        guide = ScenarioFamilyGuide()
        assert "agent_task" in guide.families

    def test_includes_simulation(self) -> None:
        from autocontext.execution.agent_task_evolution import ScenarioFamilyGuide

        guide = ScenarioFamilyGuide()
        assert "simulation" in guide.families

    def test_each_family_has_when_to_use(self) -> None:
        from autocontext.execution.agent_task_evolution import ScenarioFamilyGuide

        guide = ScenarioFamilyGuide()
        for family, info in guide.families.items():
            assert "when_to_use" in info, f"{family} missing when_to_use"
            assert len(info["when_to_use"]) > 0

    def test_to_markdown(self) -> None:
        from autocontext.execution.agent_task_evolution import ScenarioFamilyGuide

        guide = ScenarioFamilyGuide()
        md = guide.to_markdown()
        assert "agent_task" in md.lower()
        assert "simulation" in md.lower()


# ===========================================================================
# AgentTaskEvolutionRunner
# ===========================================================================


class TestAgentTaskEvolutionRunner:
    def test_single_generation(self) -> None:
        """Run one generation and get trajectory."""
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationState,
        )

        call_count = 0

        def mock_evaluate(output: str, generation: int) -> tuple[float, str, dict[str, float]]:
            nonlocal call_count
            call_count += 1
            return 0.75, "Good work", {"depth": 0.8}

        runner = AgentTaskEvolutionRunner(
            task_prompt="Write an essay.",
            evaluate_fn=mock_evaluate,
            initial_output="My first essay draft.",
        )
        state = runner.run_generation(AgentTaskGenerationState(
            generation=0, best_output="My first essay draft.",
            best_score=0.0, playbook="", score_history=[], lesson_history=[],
        ))
        assert state.best_score == 0.75
        assert state.generation == 1
        assert len(state.score_history) == 1

    def test_multi_generation_accumulates_lessons(self) -> None:
        """Multiple generations should grow the playbook."""
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
        )

        scores = [0.5, 0.65, 0.78]
        gen_idx = 0

        def mock_evaluate(output: str, generation: int) -> tuple[float, str, dict[str, float]]:
            nonlocal gen_idx
            score = scores[min(gen_idx, len(scores) - 1)]
            gen_idx += 1
            return score, f"Gen {generation} feedback", {}

        runner = AgentTaskEvolutionRunner(
            task_prompt="Write an essay.",
            evaluate_fn=mock_evaluate,
            initial_output="Draft.",
        )
        trajectory = runner.run(num_generations=3)

        assert trajectory.total_generations == 3
        assert len(trajectory.score_history) == 3
        assert trajectory.cold_start_score == 0.5
        assert trajectory.final_score == 0.78

    def test_trajectory_shows_improvement(self) -> None:
        from autocontext.execution.agent_task_evolution import AgentTaskEvolutionRunner

        gen_idx = 0

        def mock_evaluate(output: str, generation: int) -> tuple[float, str, dict[str, float]]:
            nonlocal gen_idx
            score = 0.4 + gen_idx * 0.1
            gen_idx += 1
            return min(score, 1.0), "Improving", {}

        runner = AgentTaskEvolutionRunner(
            task_prompt="Task.",
            evaluate_fn=mock_evaluate,
            initial_output="Start.",
        )
        trajectory = runner.run(num_generations=5)

        assert trajectory.improvement_delta > 0
        assert trajectory.final_score > trajectory.cold_start_score
