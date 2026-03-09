"""Pre-validation stage — run self-play dry-run before tournament.

Catches invalid strategies before wasting tournament compute.
Disabled by default (prevalidation_enabled=False).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from mts.execution.strategy_validator import StrategyValidator
from mts.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.loop.events import EventStreamEmitter

LOGGER = logging.getLogger(__name__)


def stage_prevalidation(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
    agents: AgentOrchestrator,
) -> GenerationContext:
    """Pre-validate strategy via self-play dry-run. Retry up to max_retries."""
    if not ctx.settings.prevalidation_enabled:
        return ctx

    events.emit("prevalidation_started", {
        "generation": ctx.generation,
    })

    validator = StrategyValidator(ctx.scenario, ctx.settings)

    for attempt in range(ctx.settings.prevalidation_max_retries + 1):
        result = validator.validate(ctx.current_strategy)

        if result.passed:
            events.emit("prevalidation_passed", {
                "generation": ctx.generation,
                "attempt": attempt,
            })
            return ctx

        # Validation failed
        events.emit("prevalidation_failed", {
            "generation": ctx.generation,
            "attempt": attempt,
            "errors": result.errors,
        })

        if attempt < ctx.settings.prevalidation_max_retries:
            # Get revision from competitor
            events.emit("prevalidation_revision", {
                "generation": ctx.generation,
                "attempt": attempt,
            })

            revision_prompt = validator.format_revision_prompt(result, ctx.current_strategy)
            try:
                raw_text, _ = agents.competitor.revise(
                    original_prompt=ctx.prompts.competitor if ctx.prompts else "",
                    revision_prompt=revision_prompt,
                    tool_context=ctx.tool_context,
                )
                # Re-translate the revised output
                is_code_strategy = "__code__" in ctx.current_strategy
                if is_code_strategy:
                    revised, _ = agents.translator.translate_code(raw_text)
                else:
                    revised, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
                ctx.current_strategy = revised
            except Exception:
                LOGGER.warning("prevalidation revision failed, keeping current strategy", exc_info=True)

    # All retries exhausted -- fall through to tournament with last strategy
    LOGGER.warning(
        "prevalidation exhausted %d retries, proceeding with last strategy",
        ctx.settings.prevalidation_max_retries,
    )
    return ctx
