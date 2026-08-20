"""Context-bundle evaluation boundary for the generation pipeline."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.context_bundles.assembly import bundle_routing_config

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.context_bundles.promotion import ContextBundlePromotionCoordinator
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.loop.stage_types import GenerationContext

logger = logging.getLogger(__name__)


def evaluate_context_candidate(
    ctx: GenerationContext,
    coordinator: ContextBundlePromotionCoordinator | None,
    orchestrator: AgentOrchestrator,
    events: EventStreamEmitter,
) -> None:
    """Evaluate and activate a candidate at the live post-tournament boundary."""

    digest = ctx.candidate_context_bundle_digest
    if coordinator is None or digest is None:
        return
    try:
        result = coordinator.evaluate_candidate(ctx.scenario_name, digest)
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
        return
    promoted = result.promotion is not None
    if promoted:
        active = coordinator.store.active_bundle(ctx.scenario_name)
        if active is None or active.digest != digest:
            raise RuntimeError("context bundle promotion did not activate the evaluated candidate")
        ctx.active_context_bundle_digest = active.digest
        ctx.active_context_routing = bundle_routing_config(active)
        orchestrator.apply_active_context_routing(ctx.settings, ctx.active_context_routing)
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
                result.false_promotion_result.reservation.allocated_alpha
                if result.false_promotion_result is not None
                else None
            ),
        },
    )


__all__ = ["evaluate_context_candidate"]
