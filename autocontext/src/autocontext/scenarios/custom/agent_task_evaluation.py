"""Evaluation runtime shared by generated agent tasks."""

from __future__ import annotations

from typing import Any

from autocontext.scenarios.agent_task import AgentTaskResult


def evaluate_generated_output(
    task: Any,
    output: str,
    state: dict[str, Any],
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
    calibration_examples: list[dict[str, Any]] | None = None,
    pinned_dimensions: list[str] | None = None,
) -> AgentTaskResult:
    """Shared evaluate_output runtime for generated agent tasks.

    Returns the judge's full serving identity (AC-1022). Generated and scaffolded classes delegate here
    so judge changes reach tasks already persisted as source. Settings and the provider resolve at call time
    through ``autocontext.config`` and ``autocontext.providers.registry``, as generated code always
    did; the execution validator patches those names.
    """
    from autocontext.config import load_settings
    from autocontext.execution.evaluator_guardrail import evaluate_evaluator_guardrail
    from autocontext.execution.judge import LLMJudge
    from autocontext.providers.registry import get_provider

    settings = load_settings()
    provider = get_provider(settings)
    runtime_judge_model = (
        settings.judge_model
        if isinstance(getattr(settings, "judge_model", None), str)
        else ""
    )
    judge_samples = (
        settings.judge_samples
        if isinstance(getattr(settings, "judge_samples", None), int)
        else 1
    )
    judge_temperature = (
        float(settings.judge_temperature)
        if isinstance(getattr(settings, "judge_temperature", None), int | float)
        else 0.0
    )
    judge_disagreement_threshold = (
        float(settings.judge_disagreement_threshold)
        if isinstance(getattr(settings, "judge_disagreement_threshold", None), int | float)
        else 0.15
    )
    judge_bias_probes_enabled = (
        settings.judge_bias_probes_enabled
        if isinstance(getattr(settings, "judge_bias_probes_enabled", None), bool)
        else False
    )
    effective_model = task._judge_model or runtime_judge_model or provider.default_model()
    judge = LLMJudge(
        model=effective_model,
        rubric=task._rubric,
        provider=provider,
        samples=judge_samples,
        max_tokens=int(getattr(settings, "judge_max_tokens", 4096)),
        temperature=judge_temperature,
        disagreement_threshold=judge_disagreement_threshold,
    )
    # Context passed by the caller wins over the task's generated defaults. Scaffolded template tasks
    # also default their calibration examples and pinned dimensions, and store an absent reference
    # context as "".
    ref_ctx = reference_context or task._reference_context or None
    req_con = required_concepts or task._required_concepts
    result = judge.evaluate(
        task.get_task_prompt(state),
        output,
        reference_context=ref_ctx,
        required_concepts=req_con,
        calibration_examples=calibration_examples or getattr(task, "_calibration_examples", None),
        pinned_dimensions=pinned_dimensions or getattr(task, "_pinned_dimensions", None),
    )
    evaluator_guardrail = evaluate_evaluator_guardrail(
        result,
        provider=provider,
        model=effective_model,
        rubric=task._rubric,
        candidate_output=output,
        bias_probes_enabled=judge_bias_probes_enabled,
    )
    return AgentTaskResult(
        score=result.score,
        reasoning=result.reasoning,
        dimension_scores=result.dimension_scores,
        internal_retries=result.internal_retries,
        evaluator_guardrail=(
            evaluator_guardrail.to_dict()
            if evaluator_guardrail is not None
            else None
        ),
        evaluator_epoch=result.evaluator_epoch,
        evaluator_spec=result.evaluator_spec,
        execution_provenance=result.execution_provenance,
        fixture_provenance=result.fixture_provenance,
    )
