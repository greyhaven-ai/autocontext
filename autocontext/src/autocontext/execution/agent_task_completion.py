"""Shared payload/guardrail/threshold assembly for agent-task completion.

Both the queued-task runner (``execution/task_runner.py``) and the direct
CLI agent-task path (``cli.py::_run_agent_task``) run an ``ImprovementLoop``
and then have to decide whether the result actually clears the quality bar
once objective and evaluator guardrails are taken into account. This module
is the single place that assembly happens (AC-848) so the two call sites
cannot drift apart.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from autocontext.execution.objective_verification import (
    ObjectiveVerificationConfig,
    run_objective_verification,
)
from autocontext.execution.rubric_calibration import run_judge_calibration
from autocontext.execution.verification_dataset import enrich_objective_payload
from autocontext.harness.pipeline.objective_guardrail import (
    evaluate_objective_guardrail,
    resolve_objective_guardrail_policy,
)
from autocontext.simplicity import normalize_simplicity_mode

if TYPE_CHECKING:
    from autocontext.execution.task_queue_store import TaskQueueStore
    from autocontext.providers.base import LLMProvider
    from autocontext.scenarios.agent_task import AgentTaskInterface


@dataclass(slots=True)
class TaskConfig:
    """Configuration for a queued task run."""

    generations: int = 1
    max_rounds: int = 5
    quality_threshold: float = 0.9
    min_rounds: int = 1
    reference_context: str | None = None
    browser_url: str | None = None
    required_concepts: list[str] | None = None
    calibration_examples: list[dict] | None = None
    initial_output: str | None = None
    rubric: str | None = None
    task_prompt: str | None = None
    revision_prompt: str | None = None
    objective_verification: dict[str, Any] | None = None
    judge_samples: int = 1
    judge_temperature: float = 0.0
    judge_disagreement_threshold: float = 0.15
    judge_bias_probes_enabled: bool = False
    simplicity_mode: str = "off"

    @classmethod
    def from_json(cls, data: str | None) -> TaskConfig:
        if not data:
            return cls()
        parsed = json.loads(data)
        return cls(
            generations=parsed.get("generations", 1),
            max_rounds=parsed.get("max_rounds", 5),
            quality_threshold=parsed.get("quality_threshold", 0.9),
            min_rounds=parsed.get("min_rounds", 1),
            reference_context=parsed.get("reference_context"),
            browser_url=parsed.get("browser_url"),
            required_concepts=parsed.get("required_concepts"),
            calibration_examples=parsed.get("calibration_examples"),
            initial_output=parsed.get("initial_output"),
            rubric=parsed.get("rubric"),
            task_prompt=parsed.get("task_prompt"),
            revision_prompt=parsed.get("revision_prompt"),
            objective_verification=parsed.get("objective_verification"),
            judge_samples=parsed.get("judge_samples", 1),
            judge_temperature=parsed.get("judge_temperature", 0.0),
            judge_disagreement_threshold=parsed.get("judge_disagreement_threshold", 0.15),
            judge_bias_probes_enabled=parsed.get("judge_bias_probes_enabled", False),
            simplicity_mode=normalize_simplicity_mode(parsed.get("simplicity_mode")),
        )


def build_objective_payload(
    output: str,
    rubric_score: float,
    config: TaskConfig,
    *,
    run_id: str | None = None,
) -> dict[str, Any] | None:
    """Run optional objective verification for a task result."""
    if not config.objective_verification:
        return None
    verification_config = ObjectiveVerificationConfig.from_dict(config.objective_verification)
    if not verification_config.ground_truth:
        dataset_id = config.objective_verification.get("dataset_id")
        if dataset_id:
            msg = f"Queued task objective_verification references dataset '{dataset_id}' but was not resolved before execution"
            raise ValueError(msg)
        return None
    payload = run_objective_verification(
        output=output,
        rubric_score=rubric_score,
        config=verification_config,
    )
    return enrich_objective_payload(
        payload,
        run_id=run_id,
        created_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    )


def build_objective_revision_feedback(
    output: str,
    rubric_score: float,
    config: TaskConfig,
) -> str | None:
    """Build optional oracle-derived revision context for the next loop round."""
    payload = build_objective_payload(output, rubric_score, config)
    if not payload:
        return None
    revision_feedback = payload.get("revision_feedback")
    if not isinstance(revision_feedback, dict):
        return None
    context = revision_feedback.get("revision_prompt_context")
    if not isinstance(context, str) or not context.strip():
        return None
    return context


def build_objective_guardrail_payload(
    objective_payload: dict[str, Any] | None,
    config: TaskConfig,
) -> dict[str, Any] | None:
    """Build optional binding guardrail results from an objective payload."""
    if objective_payload is None or not config.objective_verification:
        return None
    policy = resolve_objective_guardrail_policy(config.objective_verification)
    result = evaluate_objective_guardrail(objective_payload, policy)
    return result.to_dict() if result is not None else None


def build_evaluator_guardrail_payload(
    task: AgentTaskInterface,
    output: str,
    config: TaskConfig,
    *,
    reference_context: str | None = None,
) -> dict[str, Any] | None:
    """Run live evaluator guardrails on the best output when enabled."""
    if config.judge_samples <= 1 and not config.judge_bias_probes_enabled:
        return None
    evaluation = task.evaluate_output(
        output,
        task.initial_state(),
        reference_context=reference_context,
        required_concepts=config.required_concepts,
        calibration_examples=config.calibration_examples,
    )
    return evaluation.evaluator_guardrail


def build_rubric_calibration_payload(
    *,
    store: TaskQueueStore,
    spec_name: str,
    task_prompt: str,
    rubric: str,
    provider: LLMProvider,
    model: str,
    reference_context: str | None = None,
    required_concepts: list[str] | None = None,
) -> dict[str, Any] | None:
    """Run live rubric calibration against stored human feedback anchors."""
    calibration_examples = store.get_calibration_examples(spec_name, limit=5)
    report = run_judge_calibration(
        domain=spec_name,
        task_prompt=task_prompt,
        rubric=rubric,
        provider=provider,
        model=model or provider.default_model(),
        calibration_examples=calibration_examples,
        reference_context=reference_context,
        required_concepts=required_concepts,
    )
    return report.to_dict() if report is not None else None


def compute_effective_met_threshold(
    met_threshold: bool,
    objective_guardrail: dict[str, Any] | None,
    evaluator_guardrail: dict[str, Any] | None,
) -> bool:
    """Apply guardrail-adjusted gating to a raw met_threshold result.

    A guardrail that did not run (``None``) never vetoes; a guardrail that
    ran must have ``passed`` for the threshold to hold.
    """
    return (
        met_threshold
        and (objective_guardrail is None or bool(objective_guardrail.get("passed")))
        and (evaluator_guardrail is None or bool(evaluator_guardrail.get("passed")))
    )
