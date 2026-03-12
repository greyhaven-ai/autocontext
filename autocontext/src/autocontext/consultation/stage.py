"""Pipeline stage for provider consultation (AC-212).

Optionally consults a secondary provider when triggers indicate a stall
or uncertainty condition. Results are persisted and attached to the context.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.consultation.runner import ConsultationRunner
from autocontext.consultation.triggers import detect_consultation_triggers
from autocontext.consultation.types import ConsultationRequest
from autocontext.loop.stage_types import GenerationContext
from autocontext.providers.registry import create_provider
from autocontext.providers.retry import RetryProvider

if TYPE_CHECKING:
    from autocontext.harness.core.events import EventStreamEmitter
    from autocontext.providers.base import LLMProvider
    from autocontext.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)


def stage_consultation(
    ctx: GenerationContext,
    *,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
    events: EventStreamEmitter,
) -> GenerationContext:
    """Optionally consult secondary provider when triggers are active."""
    if not ctx.settings.consultation_enabled:
        return ctx

    triggers = detect_consultation_triggers(
        gate_history=ctx.gate_decision_history,
        score_history=ctx.score_history,
        settings=ctx.settings,
    )
    if not triggers:
        return ctx

    events.emit("consultation_triggered", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "triggers": [t.value for t in triggers],
    })

    # Check cost budget
    if ctx.settings.consultation_cost_budget > 0:
        spent = sqlite.get_total_consultation_cost(ctx.run_id)
        if spent >= ctx.settings.consultation_cost_budget:
            events.emit("consultation_skipped_budget", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "spent": spent,
                "budget": ctx.settings.consultation_cost_budget,
            })
            return ctx

    # Build provider
    provider = _create_consultation_provider(ctx)
    if provider is None:
        events.emit("consultation_skipped_unconfigured", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "provider": ctx.settings.consultation_provider,
        })
        return ctx

    runner = ConsultationRunner(RetryProvider(provider))

    request = ConsultationRequest(
        run_id=ctx.run_id,
        generation=ctx.generation,
        trigger=triggers[0],
        context_summary=f"Triggers: {', '.join(t.value for t in triggers)}",
        current_strategy_summary=str(ctx.current_strategy)[:500] if ctx.current_strategy else "",
        score_history=ctx.score_history,
        gate_history=ctx.gate_decision_history,
    )

    try:
        result = runner.consult(request)
    except Exception:
        LOGGER.warning("consultation call failed", exc_info=True)
        return ctx

    # Persist
    sqlite.insert_consultation(
        run_id=ctx.run_id,
        generation_index=ctx.generation,
        trigger=triggers[0].value,
        context_summary=request.context_summary,
        critique=result.critique,
        alternative_hypothesis=result.alternative_hypothesis,
        tiebreak_recommendation=result.tiebreak_recommendation,
        suggested_next_action=result.suggested_next_action,
        raw_response=result.raw_response,
        model_used=result.model_used,
        cost_usd=result.cost_usd,
    )
    advisory_path = artifacts.generation_dir(ctx.run_id, ctx.generation) / "consultation.md"
    artifacts.write_markdown(advisory_path, result.to_advisory_markdown())

    events.emit("consultation_completed", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "trigger": triggers[0].value,
        "model_used": result.model_used,
        "cost_usd": result.cost_usd,
    })

    ctx.consultation_result = result
    return ctx


def _create_consultation_provider(ctx: GenerationContext) -> LLMProvider | None:
    """Create provider for consultation calls, or None if consultation is not configured."""
    settings = ctx.settings
    if not settings.consultation_api_key:
        return None
    return create_provider(
        provider_type=settings.consultation_provider,
        api_key=settings.consultation_api_key,
        base_url=settings.consultation_base_url or None,
        model=settings.consultation_model,
    )
