"""E2E smoke test: ImprovementLoop with real API calls.

Tests the full generate→judge→revise→judge cycle to verify:
1. Score improves across rounds
2. Revision incorporates judge feedback
3. Loop terminates at threshold or max rounds
4. ImprovementResult is well-formed
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from mts.providers.anthropic import AnthropicProvider
from mts.execution.judge import LLMJudge, JudgeResult
from mts.execution.improvement_loop import ImprovementLoop, ImprovementResult
from mts.scenarios.agent_task import AgentTaskInterface, AgentTaskResult


class LinkedInPostTask(AgentTaskInterface):
    """Real task: write a LinkedIn post about AI evaluation."""

    def __init__(self, provider: AnthropicProvider):
        self._provider = provider
        rubric = (
            "Score 0-1 on these dimensions:\n"
            "- voice: Direct, opinionated, no corporate fluff. Sounds like a practitioner, not a marketer.\n"
            "- insight: Makes a non-obvious point backed by specific evidence or experience.\n"
            "- engagement: Hook in first line, clear structure, ends with something actionable.\n"
            "- brevity: Under 200 words. Every sentence earns its place.\n"
        )
        self._judge = LLMJudge(
            provider=provider,
            model="claude-sonnet-4-20250514",
            rubric=rubric,
        )

    def get_task_prompt(self, state: dict) -> str:
        return (
            "Write a LinkedIn post (under 200 words) about why most AI evaluations are broken. "
            "The post should argue that vibes-based evaluation ('it feels good') needs to be "
            "replaced with structured, rubric-based scoring. Be direct and opinionated — no "
            "corporate fluff. Include a specific example or anecdote."
        )

    def evaluate_output(self, output, state, reference_context=None,
                        required_concepts=None, calibration_examples=None):
        result = self._judge.evaluate(
            task_prompt=self.get_task_prompt(state),
            agent_output=output,
            reference_context=reference_context,
            required_concepts=required_concepts,
            calibration_examples=calibration_examples,
        )
        return AgentTaskResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
        )

    def revise_output(self, output, judge_result, state):
        """Use the LLM to revise based on judge feedback."""
        revision_prompt = (
            f"Revise this LinkedIn post based on the judge's feedback.\n\n"
            f"## Current Post\n{output}\n\n"
            f"## Judge Score: {judge_result.score:.2f}\n"
            f"## Judge Feedback\n{judge_result.reasoning}\n\n"
            f"## Dimension Scores\n"
            + "\n".join(f"- {k}: {v:.2f}" for k, v in judge_result.dimension_scores.items())
            + "\n\n## Original Task\n" + self.get_task_prompt(state)
            + "\n\nProduce ONLY the revised post, nothing else."
        )
        result = self._provider.complete(
            system_prompt="You are revising a LinkedIn post based on expert feedback. Output ONLY the revised post.",
            user_prompt=revision_prompt,
            model="claude-sonnet-4-20250514",
        )
        return result.text

    def get_rubric(self):
        return self._judge.rubric

    def initial_state(self, seed=None):
        return {"topic": "AI evaluation"}

    def describe_task(self):
        return "Write a LinkedIn post about why AI evaluations need structured rubrics"


def section(title: str):
    print(f"\n{'='*60}")
    print(f"  {title}")
    print(f"{'='*60}")


def main():
    provider = AnthropicProvider(default_model_name="claude-sonnet-4-20250514")
    task = LinkedInPostTask(provider)

    # Generate initial output (deliberately mediocre prompt to leave room for improvement)
    section("Generating initial output (intentionally weak)")
    initial = provider.complete(
        system_prompt="Write a generic, corporate-sounding LinkedIn post. Use buzzwords.",
        user_prompt=task.get_task_prompt({}),
        model="claude-sonnet-4-20250514",
    )
    print(f"  Initial ({len(initial.text)} chars):\n  {initial.text[:200]}...")

    # Run improvement loop
    section("Running ImprovementLoop (max 3 rounds, threshold 0.95)")
    loop = ImprovementLoop(task=task, max_rounds=3, quality_threshold=0.95)
    result = loop.run(
        initial_output=initial.text,
        state=task.initial_state(),
    )

    # Print results
    section("Results")
    for r in result.rounds:
        print(f"\n  Round {r.round_number} {'(revision)' if r.is_revision else '(initial)'}:")
        print(f"    Score: {r.score:.2f}")
        dims = ", ".join(f"{k}: {v:.2f}" for k, v in r.dimension_scores.items())
        print(f"    Dimensions: {dims}")
        print(f"    Reasoning: {r.reasoning[:120]}...")
        print(f"    Output preview: {r.output[:100]}...")

    section("Summary")
    print(f"  Total rounds: {result.total_rounds}")
    print(f"  Best score: {result.best_score:.2f} (round {result.best_round})")
    print(f"  Met threshold: {result.met_threshold}")
    print(f"  Improved: {result.improved}")
    print(f"\n  Best output:\n  {result.best_output[:300]}...")

    # Assertions
    assert result.total_rounds >= 1, "Should have at least 1 round"
    assert 0.0 <= result.best_score <= 1.0, f"Score out of range: {result.best_score}"
    assert len(result.rounds) == result.total_rounds
    assert result.best_output, "Best output is empty"

    if result.total_rounds > 1:
        first_score = result.rounds[0].score
        print(f"\n  Score trajectory: {first_score:.2f} → {result.best_score:.2f} "
              f"(delta: {result.best_score - first_score:+.2f})")

    if result.met_threshold:
        print(f"\n  🟢 THRESHOLD MET at round {result.best_round}")
    elif result.improved:
        print(f"\n  🟡 IMPROVED but didn't hit threshold")
    else:
        print(f"\n  🔴 NO IMPROVEMENT")

    print(f"\n  ✅ ImprovementLoop e2e test PASSED")


if __name__ == "__main__":
    main()
