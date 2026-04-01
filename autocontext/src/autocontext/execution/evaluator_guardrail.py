"""Live evaluator guardrails for disagreement and bias probes (AC-330)."""

from __future__ import annotations

import logging
from typing import Any

from pydantic import BaseModel, Field

from autocontext.execution.bias_probes import BiasReport, run_position_bias_probe
from autocontext.execution.judge import DisagreementMetrics, JudgeResult
from autocontext.providers.base import LLMProvider

logger = logging.getLogger(__name__)

_BIAS_PROBE_SYSTEM_PROMPT = (
    "You are an impartial judge comparing two candidate outputs. "
    "Avoid favoring one candidate because of position, ordering, or presentation."
)


class EvaluatorGuardrailResult(BaseModel):
    """Outcome of live evaluator disagreement / bias checks."""

    passed: bool
    reason: str
    violations: list[str]
    disagreement: dict[str, Any] | None = None
    bias_report: dict[str, Any] | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return self.model_dump()


def evaluate_evaluator_guardrail(
    judge_result: JudgeResult,
    *,
    provider: LLMProvider | None,
    model: str,
    rubric: str,
    candidate_output: str,
    bias_probes_enabled: bool = False,
) -> EvaluatorGuardrailResult | None:
    """Resolve live evaluator guardrail status from judge output and probes."""
    disagreement = getattr(judge_result, "disagreement", None)
    if not isinstance(disagreement, DisagreementMetrics):
        disagreement = None
    disagreement_payload = (
        disagreement.to_dict()
        if disagreement is not None
        else None
    )
    bias_payload: dict[str, Any] | None = None
    violations: list[str] = []
    metadata: dict[str, Any] = {}

    if disagreement is not None and disagreement.is_high_disagreement:
        violations.append(
            "judge disagreement exceeded threshold "
            f"(std_dev={disagreement.score_std_dev:.4f})"
        )

    if bias_probes_enabled and provider is not None and candidate_output.strip():
        probe_model = model or provider.default_model()
        try:
            position_probe = run_position_bias_probe(
                provider=provider,
                model=probe_model,
                system_prompt=_BIAS_PROBE_SYSTEM_PROMPT,
                candidate_a=candidate_output,
                candidate_b=candidate_output,
                rubric=rubric,
            )
            report = BiasReport(
                probes_run=1,
                probes_failed=0,
                results=[position_probe],
                any_bias_detected=position_probe.detected,
            )
            bias_payload = report.to_dict()
            if position_probe.detected:
                violations.append(
                    "judge bias probe detected position bias "
                    f"(magnitude={position_probe.magnitude:.4f})"
                )
        except Exception as exc:
            logger.warning("judge bias probe failed: %s", exc)
            report = BiasReport(probes_run=1, probes_failed=1)
            bias_payload = report.to_dict()
            metadata["bias_probe_error"] = str(exc)

    if disagreement_payload is None and bias_payload is None:
        return None

    if violations:
        reason = "; ".join(violations)
    else:
        active_checks: list[str] = []
        if disagreement_payload is not None:
            active_checks.append("disagreement")
        if bias_payload is not None:
            active_checks.append("bias")
        reason = f"Evaluator {' + '.join(active_checks)} checks passed"

    return EvaluatorGuardrailResult(
        passed=not violations,
        reason=reason,
        violations=violations,
        disagreement=disagreement_payload,
        bias_report=bias_payload,
        metadata=metadata,
    )
