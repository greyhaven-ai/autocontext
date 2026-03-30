"""Revision helpers for generated agent tasks (AC-280)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from autocontext.config import load_settings
from autocontext.providers.registry import get_provider
from autocontext.scenarios.agent_task import AgentTaskResult

_LEGACY_NOOP_REVISION_MARKER = (
    "# Default revision: return original (llm_fn must be injected at runtime)"
)

_LEGACY_EVALUATE_MARKER = 'raise NotImplementedError("llm_fn must be injected at runtime")'


def build_revision_prompt(
    *,
    original_output: str,
    judge_result: AgentTaskResult,
    task_prompt: str,
    revision_prompt: str | None = None,
    rubric: str = "",
) -> str:
    """Build a revision prompt from judge feedback for LLM-based revision.

    Args:
        original_output: The current agent output to revise.
        judge_result: Judge evaluation result with score, reasoning, dimensions.
        task_prompt: The original task prompt for context.
        revision_prompt: Optional task-specific revision instructions.
        rubric: Optional rubric for context.

    Returns:
        A complete prompt string for requesting a revision from the LLM.
    """
    # Identify weak dimensions (score < 0.7)
    weak_dims = {
        dim: score
        for dim, score in judge_result.dimension_scores.items()
        if score < 0.7
    }

    sections: list[str] = []

    sections.append("You are revising your previous output based on judge feedback.")
    sections.append(f"\n## Current Score\n{judge_result.score:.2f}")
    sections.append(f"\n## Judge Reasoning\n{judge_result.reasoning}")

    if weak_dims:
        dim_lines = "\n".join(f"- {dim}: {score:.2f}" for dim, score in sorted(weak_dims.items(), key=lambda x: x[1]))
        sections.append(f"\n## Weak Dimensions (need improvement)\n{dim_lines}")

    sections.append(f"\n## Original Task\n{task_prompt}")
    sections.append(f"\n## Original Output\n{original_output}")

    if revision_prompt:
        sections.append(f"\n## Revision Instructions\n{revision_prompt}")

    sections.append(
        "\n## Your Task\n"
        "Produce a revised, improved version of the output that addresses "
        "the judge's feedback and improves on the weak dimensions. "
        "Return ONLY the revised output, not commentary about the changes."
    )

    return "\n".join(sections)


def revise_generated_output(
    task: Any,
    output: str,
    judge_result: AgentTaskResult,
    state: dict,
) -> str:
    """Shared revise_output runtime for generated agent tasks."""
    if not task._revision_prompt and task._max_rounds <= 1:
        return output

    settings = load_settings()
    provider = get_provider(settings)
    model = task._judge_model or settings.judge_model or provider.default_model()
    prompt = build_revision_prompt(
        original_output=output,
        judge_result=judge_result,
        task_prompt=task.get_task_prompt(state),
        revision_prompt=task._revision_prompt,
        rubric=task._rubric,
    )
    result = provider.complete(
        "You are a helpful revision assistant.",
        prompt,
        model=model,
    )
    revised = result.text.strip()
    return revised if revised else output


def patch_legacy_generated_revise_output(
    cls: type[Any],
    source_path: Path,
) -> type[Any]:
    """Upgrade legacy generated agent_task classes that still no-op on revision."""
    source = source_path.read_text(encoding="utf-8")
    if _LEGACY_NOOP_REVISION_MARKER not in source:
        return cls

    def _patched_revise_output(
        self: Any,
        output: str,
        judge_result: AgentTaskResult,
        state: dict,
    ) -> str:
        return revise_generated_output(self, output, judge_result, state)

    cls.revise_output = _patched_revise_output
    return cls


def patch_legacy_generated_evaluate_output(
    cls: type[Any],
    source_path: Path,
) -> type[Any]:
    """Upgrade legacy generated agent_task classes with llm_fn placeholder in evaluate_output.

    AC-310: Generated scenarios that still use the broken pattern:
        def llm_fn(system, user):
            raise NotImplementedError("llm_fn must be injected at runtime")
    get their evaluate_output replaced with one that uses load_settings() + get_provider().
    """
    source = source_path.read_text(encoding="utf-8")
    if _LEGACY_EVALUATE_MARKER not in source:
        return cls

    def _patched_evaluate_output(
        self: Any,
        output: str,
        state: dict[str, Any],
        reference_context: str | None = None,
        required_concepts: list[str] | None = None,
        calibration_examples: list[dict[str, Any]] | None = None,
        pinned_dimensions: list[str] | None = None,
    ) -> AgentTaskResult:
        from autocontext.execution.judge import LLMJudge

        settings = load_settings()
        provider = get_provider(settings)
        model = getattr(self, "_judge_model", "") or settings.judge_model or provider.default_model()
        rubric = getattr(self, "_rubric", "") or ""
        judge = LLMJudge(
            model=model,
            rubric=rubric,
            provider=provider,
        )
        task_prompt = self.get_task_prompt(state)
        ref_ctx = reference_context or getattr(self, "_reference_context", None)
        req_con = required_concepts or getattr(self, "_required_concepts", None)
        result = judge.evaluate(
            task_prompt,
            output,
            reference_context=ref_ctx,
            required_concepts=req_con,
            calibration_examples=calibration_examples,
            pinned_dimensions=pinned_dimensions,
        )
        return AgentTaskResult(
            score=result.score,
            reasoning=result.reasoning,
            dimension_scores=result.dimension_scores,
            internal_retries=result.internal_retries,
        )

    cls.evaluate_output = _patched_evaluate_output
    return cls
