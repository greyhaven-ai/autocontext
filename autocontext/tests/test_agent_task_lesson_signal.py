"""Tests for domain-aware lesson accumulation.

The evaluator can attach a structured LessonSignal (hint / plateau /
metrics) to its evaluation. accumulate_lessons consumes it to produce
actionable playbook lessons, instead of only score + dimension scores.
"""

from __future__ import annotations


def _judge(score: float = 0.95, reasoning: str = "valid"):
    from autocontext.scenarios.agent_task import AgentTaskResult

    return AgentTaskResult(score=score, reasoning=reasoning, dimension_scores={})


class TestLessonSignalInAccumulate:
    def test_hint_is_included(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            LessonSignal,
            accumulate_lessons,
        )

        lesson = accumulate_lessons(
            _judge(),
            3,
            signal=LessonSignal(hint="demote some members to admit new points"),
        )
        assert "Hint: demote some members to admit new points" in lesson

    def test_plateau_guidance_included(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            LessonSignal,
            accumulate_lessons,
        )

        lesson = accumulate_lessons(_judge(), 5, signal=LessonSignal(plateau=True))
        assert "plateau" in lesson.lower()

    def test_metrics_rendered(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            LessonSignal,
            accumulate_lessons,
        )

        lesson = accumulate_lessons(
            _judge(),
            2,
            signal=LessonSignal(metrics={"size": 224.0, "delta": 0.0}),
        )
        assert "size=224" in lesson
        assert "delta=0" in lesson

    def test_no_signal_matches_legacy_behavior(self) -> None:
        from autocontext.execution.agent_task_evolution import accumulate_lessons

        lesson = accumulate_lessons(_judge(score=0.6, reasoning="needs work"), 1)
        assert "Generation 1 (score: 0.60):" in lesson
        assert "Hint:" not in lesson
        assert "plateau" not in lesson.lower()


class TestRunnerThreadsSignal:
    def test_signal_flows_into_playbook(self) -> None:
        from autocontext.execution.agent_task_evolution import (
            AgentTaskEvolutionRunner,
            AgentTaskGenerationEvaluation,
            LessonSignal,
        )

        def gen(prompt: str, g: int) -> str:
            return "candidate"

        def ev(output: str, g: int) -> AgentTaskGenerationEvaluation:
            return AgentTaskGenerationEvaluation(
                output=output,
                score=0.5,
                reasoning="ok",
                lesson_signal=LessonSignal(hint="try a different family"),
            )

        runner = AgentTaskEvolutionRunner(task_prompt="t", generate_fn=gen, evaluate_fn=ev)
        _, state = runner.run_with_state(1)
        assert "Hint: try a different family" in state.playbook
