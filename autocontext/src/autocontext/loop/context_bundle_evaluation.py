"""Context-bundle evaluation boundary for the generation pipeline."""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING

from autocontext.context_bundles.assembly import bundle_routing_config

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.context_bundles.promotion import ContextBundlePromotionCoordinator
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.loop.stage_types import GenerationContext

logger = logging.getLogger(__name__)


class ContextBundleEvaluationDeferred(RuntimeError):
    """Signal that durable candidate evidence must be resumed before advancing."""


def evaluate_context_candidate(
    ctx: GenerationContext,
    coordinator: ContextBundlePromotionCoordinator | None,
    orchestrator: AgentOrchestrator,
    events: EventStreamEmitter,
    *,
    cancellation_check: Callable[[], bool] | None = None,
) -> None:
    """Evaluate and activate a candidate at the live post-tournament boundary."""

    digest = ctx.candidate_context_bundle_digest
    if coordinator is None or digest is None:
        return
    budget = ctx.settings.generation_time_budget_seconds
    recorded_start = float(getattr(ctx, "generation_start_time", 0.0))
    started_at = recorded_start if recorded_start > 0 else time.monotonic()
    deadline = started_at + budget if budget > 0 else None
    try:
        if deadline is None and cancellation_check is None:
            result = coordinator.evaluate_candidate(ctx.scenario_name, digest)
        else:
            result = coordinator.evaluate_candidate(
                ctx.scenario_name,
                digest,
                deadline=deadline,
                cancellation_check=cancellation_check,
            )
    except Exception as exc:
        logger.warning("context bundle matched evaluation failed closed", exc_info=True)
        events.emit(
            "context_bundle_evaluation_failed",
            {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "candidate_digest": digest,
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
        raise ContextBundleEvaluationDeferred(
            f"context candidate {digest} evaluation failed and must be resumed: {type(exc).__name__}: {exc}"
        ) from exc
    promoted = result.promotion is not None
    if promoted:
        active = coordinator.store.active_bundle(ctx.scenario_name)
        if active is None or active.digest != digest:
            raise RuntimeError("context bundle promotion did not activate the evaluated candidate")
        ctx.active_context_bundle_digest = active.digest
        ctx.active_context_routing = bundle_routing_config(active)
        ctx.settings = orchestrator.apply_active_context_routing(ctx.settings, ctx.active_context_routing)
    events.emit(
        "context_bundle_evaluated",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "candidate_digest": digest,
            "decision": result.comparison.decision.value,
            "evaluated_pairs": result.evaluated_pairs,
            "promoted": promoted,
            "promotion_id": result.promotion.promotion_id if result.promotion is not None else None,
            "audit_policy_outcome": result.audit_policy_outcome,
            "false_promotion_authorized": (
                result.false_promotion_result.authorized if result.false_promotion_result is not None else None
            ),
            "false_promotion_reason": (
                result.false_promotion_result.reason if result.false_promotion_result is not None else None
            ),
            "campaign_alpha_allocation": (
                result.false_promotion_result.reservation.allocated_alpha if result.false_promotion_result is not None else None
            ),
        },
    )
    if result.audit_policy_outcome in {"review_required", "safe_pause_recommended"}:
        raise ContextBundleEvaluationDeferred(
            f"context candidate {digest} is held for operator review ({result.audit_policy_outcome})"
        )
    if not promoted and result.false_promotion_result is None and result.comparison.decision.value.startswith("needs_"):
        raise ContextBundleEvaluationDeferred(
            f"context candidate {digest} exhausted this evaluation pass before a terminal decision"
        )


def resume_pending_context_candidate(
    ctx: GenerationContext,
    coordinator: ContextBundlePromotionCoordinator | None,
    orchestrator: AgentOrchestrator,
    events: EventStreamEmitter,
) -> bool:
    """Resume one durable candidate from a failed generation before regenerating it."""

    if coordinator is None:
        return False
    pending = coordinator.store.pending_candidates(ctx.scenario_name, ctx.run_id, ctx.generation)
    if not pending:
        return False
    if len(pending) != 1:
        digests = ", ".join(record.bundle_digest for record in pending)
        raise ContextBundleEvaluationDeferred(
            f"generation has multiple pending context candidates and cannot resume unambiguously: {digests}"
        )
    digest = pending[0].bundle_digest
    ctx.candidate_context_bundle_digest = digest
    events.emit(
        "context_bundle_resume_started",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "candidate_digest": digest,
            "lifecycle": pending[0].lifecycle.value,
        },
    )
    evaluate_context_candidate(ctx, coordinator, orchestrator, events)
    ctx.candidate_context_bundle_digest = None
    events.emit(
        "context_bundle_resume_completed",
        {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "candidate_digest": digest,
            "active_context_bundle_digest": ctx.active_context_bundle_digest,
        },
    )
    return True


__all__ = [
    "ContextBundleEvaluationDeferred",
    "evaluate_context_candidate",
    "resume_pending_context_candidate",
]
