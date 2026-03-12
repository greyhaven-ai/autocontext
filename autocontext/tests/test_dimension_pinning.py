"""Tests for dimension pinning across improvement loop rounds (MTS-48).

When the rubric is vague, the judge invents dimension names. These can change
between rounds, making scores incomparable. After the first successful judge
round, we "pin" those dimension names and pass them to subsequent calls so
the same dimensions are used consistently.
"""

from __future__ import annotations

from autocontext.execution.improvement_loop import ImprovementLoop
from autocontext.execution.judge import LLMJudge
from autocontext.scenarios.agent_task import AgentTaskInterface, AgentTaskResult

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

JUDGE_RESPONSE_WITH_DIMS = (
    '<!-- JUDGE_RESULT_START -->'
    '{"score": 0.7, "reasoning": "Decent", '
    '"dimensions": {"creativity": 0.8, "depth": 0.6}}'
    '<!-- JUDGE_RESULT_END -->'
)


def make_mock_llm(response: str = JUDGE_RESPONSE_WITH_DIMS):
    def mock_llm(system: str, user: str) -> str:
        return response

    return mock_llm


class PinningCapture(AgentTaskInterface):
    """Task that captures pinned_dimensions passed to evaluate_output."""

    def __init__(self, scores: list[float] | None = None) -> None:
        self._scores = scores or [0.6, 0.75, 0.95]
        self._call_count = 0
        self.captured_pinned: list[list[str] | None] = []

    def get_task_prompt(self, state: dict) -> str:
        return "test prompt"

    def evaluate_output(
        self,
        output: str,
        state: dict,
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        self.captured_pinned.append(pinned_dimensions)
        idx = min(self._call_count, len(self._scores) - 1)
        score = self._scores[idx]
        self._call_count += 1
        return AgentTaskResult(
            score=score,
            reasoning=f"Score {score}",
            dimension_scores={"creativity": score, "depth": score * 0.8},
        )

    def get_rubric(self) -> str:
        return "test rubric"

    def initial_state(self, seed: int | None = None) -> dict:
        return {}

    def describe_task(self) -> str:
        return "test"

    def revise_output(
        self, output: str, judge_result: AgentTaskResult, state: dict
    ) -> str:
        return output + " [revised]"


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestPinnedDimensionsInJudgePrompt:
    """Verify that pinned dimensions appear in the judge prompt."""

    def test_pinned_dimensions_in_judge_prompt(self) -> None:
        judge = LLMJudge(
            model="test",
            rubric="Be creative",
            llm_fn=make_mock_llm(),
        )
        prompt = judge._build_judge_prompt(
            "task",
            "output",
            pinned_dimensions=["creativity", "depth"],
        )
        assert "## Required Dimensions" in prompt
        assert "creativity" in prompt
        assert "depth" in prompt
        assert "Do not add, remove, or rename dimensions" in prompt

    def test_no_pinned_dimensions_section_when_none(self) -> None:
        judge = LLMJudge(
            model="test",
            rubric="Be creative",
            llm_fn=make_mock_llm(),
        )
        prompt = judge._build_judge_prompt("task", "output", pinned_dimensions=None)
        assert "## Required Dimensions" not in prompt

    def test_pinned_dimensions_passed_to_evaluate(self) -> None:
        """Ensure evaluate() forwards pinned_dimensions to _build_judge_prompt."""
        captured_prompts: list[str] = []
        original_build = LLMJudge._build_judge_prompt

        def capturing_build(self, *args, **kwargs):
            result = original_build(self, *args, **kwargs)
            captured_prompts.append(result)
            return result

        LLMJudge._build_judge_prompt = capturing_build  # type: ignore[assignment]
        try:
            judge = LLMJudge(
                model="test",
                rubric="Be creative",
                llm_fn=make_mock_llm(),
            )
            judge.evaluate(
                "task",
                "output",
                pinned_dimensions=["creativity", "depth"],
            )
            assert len(captured_prompts) == 1
            assert "## Required Dimensions" in captured_prompts[0]
        finally:
            LLMJudge._build_judge_prompt = original_build  # type: ignore[assignment]


class TestImprovementLoopPinning:
    """Verify the improvement loop pins dimensions after the first successful round."""

    def test_improvement_loop_pins_after_first_round(self) -> None:
        task = PinningCapture(scores=[0.6, 0.75, 0.95])
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.99)
        loop.run("initial output", {})

        # First call: no pinning yet
        assert task.captured_pinned[0] is None
        # Subsequent calls: should have pinned dimensions
        for pinned in task.captured_pinned[1:]:
            assert pinned is not None
            assert sorted(pinned) == ["creativity", "depth"]

    def test_no_pinning_when_no_dimension_scores(self) -> None:
        """If first round returns empty dimensions, no pinning occurs."""

        class NoDimsTask(AgentTaskInterface):
            def __init__(self) -> None:
                self._call_count = 0
                self.captured_pinned: list[list[str] | None] = []

            def get_task_prompt(self, state: dict) -> str:
                return "test"

            def evaluate_output(
                self,
                output: str,
                state: dict,
                reference_context: str | None = None,
                required_concepts: list[str] | None = None,
                calibration_examples: list[dict] | None = None,
                pinned_dimensions: list[str] | None = None,
            ) -> AgentTaskResult:
                self.captured_pinned.append(pinned_dimensions)
                self._call_count += 1
                return AgentTaskResult(
                    score=0.5,
                    reasoning="ok",
                    dimension_scores={},
                )

            def get_rubric(self) -> str:
                return "test"

            def initial_state(self, seed: int | None = None) -> dict:
                return {}

            def describe_task(self) -> str:
                return "test"

            def revise_output(
                self, output: str, judge_result: AgentTaskResult, state: dict
            ) -> str:
                return output + " [revised]"

        task = NoDimsTask()
        loop = ImprovementLoop(task, max_rounds=3, quality_threshold=0.99)
        loop.run("initial", {})

        # All calls should have None pinned_dimensions
        assert all(p is None for p in task.captured_pinned)


class TestNoPinningWhenDimensionsExplicit:
    """Verify dimensions_were_generated is False when rubric mentions the dimensions."""

    def test_no_pinning_when_dimensions_explicit(self) -> None:
        # Rubric explicitly mentions "clarity" and "accuracy"
        resp = (
            '<!-- JUDGE_RESULT_START -->'
            '{"score": 0.8, "reasoning": "ok", '
            '"dimensions": {"clarity": 0.9, "accuracy": 0.7}}'
            '<!-- JUDGE_RESULT_END -->'
        )
        judge = LLMJudge(
            model="test",
            rubric="Evaluate clarity and accuracy of the output",
            llm_fn=make_mock_llm(resp),
        )
        result = judge.evaluate("task", "output")
        assert result.dimensions_were_generated is False


class TestSimpleAgentTaskPinnedDimensions:
    """Verify SimpleAgentTask passes pinned_dimensions through to judge."""

    def test_pinned_dimensions_forwarded(self) -> None:
        from autocontext.execution.task_runner import SimpleAgentTask
        from autocontext.providers.callable_wrapper import CallableProvider

        provider = CallableProvider(
            make_mock_llm(JUDGE_RESPONSE_WITH_DIMS),
            model_name="test",
        )
        task = SimpleAgentTask(
            task_prompt="Do task",
            rubric="Be creative",
            provider=provider,
            model="test",
        )
        result = task.evaluate_output(
            "test output",
            {},
            pinned_dimensions=["creativity", "depth"],
        )
        assert result.score > 0
        assert "creativity" in result.dimension_scores
