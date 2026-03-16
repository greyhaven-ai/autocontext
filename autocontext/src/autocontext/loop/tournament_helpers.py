"""Extracted retry, gate, and side-effect helpers for stage_tournament (AC-145).

Pure functions factored out of stage_tournament() to enable focused unit
testing without end-to-end stage scaffolding. Each helper encapsulates
one responsibility: gate resolution, retry prompt assembly, outcome
application, or validity rollback construction.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

from autocontext.harness.evaluation.types import EvaluationSummary
from autocontext.harness.pipeline.gate import BackpressureGate
from autocontext.harness.pipeline.trend_gate import ScoreHistory, TrendAwareGate
from autocontext.knowledge.rapid_gate import rapid_gate


@dataclass(slots=True)
class GateDecisionResult:
    """Resolved gate decision with context."""

    decision: str  # advance, retry, rollback
    delta: float
    reason: str
    is_rapid: bool


def resolve_gate_decision(
    *,
    tournament_best_score: float,
    previous_best: float,
    gate: BackpressureGate | TrendAwareGate | None,
    score_history: list[float],
    gate_decision_history: list[str],
    retry_count: int,
    max_retries: int,
    use_rapid: bool,
    custom_metrics: dict[str, float] | None = None,
) -> GateDecisionResult:
    """Select gate mode (rapid/trend-aware/standard) and evaluate decision.

    This is the multi-mode dispatcher extracted from stage_tournament's
    gate evaluation block.
    """
    delta = round(tournament_best_score - previous_best, 6)

    if use_rapid:
        result = rapid_gate(tournament_best_score, previous_best)
        return GateDecisionResult(
            decision=result.decision,
            delta=result.delta,
            reason=result.reason,
            is_rapid=True,
        )

    if gate is None:
        return GateDecisionResult(
            decision="rollback",
            delta=delta,
            reason="no gate configured",
            is_rapid=False,
        )

    if isinstance(gate, TrendAwareGate):
        gate_result = gate.evaluate(
            previous_best,
            tournament_best_score,
            retry_count=retry_count,
            max_retries=max_retries,
            history=ScoreHistory(
                scores=tuple(score_history),
                gate_decisions=tuple(gate_decision_history),
            ),
            custom_metrics=custom_metrics or {},
        )
    else:
        gate_result = gate.evaluate(
            previous_best,
            tournament_best_score,
            retry_count=retry_count,
            max_retries=max_retries,
        )

    return GateDecisionResult(
        decision=gate_result.decision,
        delta=gate_result.delta,
        reason=gate_result.reason,
        is_rapid=False,
    )


def build_retry_prompt(
    *,
    base_prompt: str,
    tournament_best_score: float,
    previous_best: float,
    min_delta: float,
    current_strategy: dict[str, Any],
    attempt: int,
    is_code_strategy: bool,
    include_code_strategy_suffix: bool = False,
    strategy_interface: str = "",
    failure_report_context: str = "",
) -> str:
    """Build retry-learning prompt with failure context.

    Extracted from the retry branch in stage_tournament's while loop.
    """
    prompt = (
        base_prompt
        + f"\n\n--- RETRY ATTEMPT {attempt} ---\n"
        f"Your previous strategy scored {tournament_best_score:.4f} "
        f"but needed delta >= {min_delta} over {previous_best:.4f}.\n"
    )

    if is_code_strategy:
        prompt += "Adjust your code to improve. Do not repeat the same approach.\n"
        if include_code_strategy_suffix:
            from autocontext.prompts.templates import code_strategy_competitor_suffix

            prompt += code_strategy_competitor_suffix(strategy_interface)
    else:
        prompt += (
            f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
            f"Adjust your strategy to improve. Do not repeat the same approach.\n"
        )

    if failure_report_context:
        prompt += "\n" + failure_report_context

    return prompt


def apply_tournament_outcome(
    *,
    gate_decision: str,
    tournament: EvaluationSummary,
    previous_best: float,
    challenger_elo: float,
    score_history: list[float],
    gate_decision_history: list[str],
) -> dict[str, Any]:
    """Apply tournament outcome to context fields.

    Returns a dict of updated context values. The caller applies these
    to GenerationContext or equivalent state.
    """
    gate_delta = round(tournament.best_score - previous_best, 6)

    new_score_history = [*score_history, tournament.best_score]
    new_gate_history = [*gate_decision_history, gate_decision]

    updated_previous_best = previous_best
    updated_elo = challenger_elo

    if gate_decision == "advance":
        updated_previous_best = max(previous_best, tournament.best_score)
        updated_elo = tournament.elo_after

    return {
        "gate_decision": gate_decision,
        "gate_delta": gate_delta,
        "previous_best": updated_previous_best,
        "challenger_elo": updated_elo,
        "score_history": new_score_history,
        "gate_decision_history": new_gate_history,
    }


def build_validity_rollback(
    *,
    current_strategy: dict[str, Any],
    validity_retry_attempts: int,
    score_history: list[float],
    gate_decision_history: list[str],
    tournament: EvaluationSummary,
) -> dict[str, Any]:
    """Build validity rollback state when validity budget is exhausted.

    Returns a dict of context values for a validity-gated rollback.
    """
    return {
        "gate_decision": "rollback",
        "gate_delta": 0.0,
        "score": 0.0,
        "attempt": validity_retry_attempts,
        "current_strategy": current_strategy,
        "score_history": [*score_history, 0.0],
        "gate_decision_history": [*gate_decision_history, "rollback"],
        "tournament": tournament,
    }
