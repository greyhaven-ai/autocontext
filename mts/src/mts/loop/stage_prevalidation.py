"""Pre-validation stage — run harness validators and self-play dry-run before tournament.

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
    from mts.execution.harness_loader import HarnessLoader
    from mts.loop.events import EventStreamEmitter

LOGGER = logging.getLogger(__name__)


def stage_prevalidation(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
    agents: AgentOrchestrator,
    harness_loader: HarnessLoader | None = None,
) -> GenerationContext:
    """Pre-validate strategy via harness validators and self-play dry-run.

    Harness validation runs first (if enabled), then self-play dry-run
    (if prevalidation_dry_run_enabled). Retry up to max_retries.
    """
    if not ctx.settings.prevalidation_enabled:
        return ctx

    # --- Phase 1: Harness validation ---
    if harness_loader is not None:
        events.emit("harness_validation_started", {
            "generation": ctx.generation,
        })
        harness_result = harness_loader.validate_strategy(ctx.current_strategy, ctx.scenario)
        if not harness_result.passed:
            events.emit("harness_validation_failed", {
                "generation": ctx.generation,
                "errors": harness_result.errors,
            })
            LOGGER.warning(
                "harness validation failed for generation %d: %s",
                ctx.generation, harness_result.errors,
            )
            # Attempt revision loop for harness failures
            for _attempt in range(ctx.settings.prevalidation_max_retries):
                revision_prompt = (
                    "Your strategy failed harness validation. Fix the issues:\n\n"
                    + "\n".join(f"- {e}" for e in harness_result.errors)
                )
                try:
                    raw_text, _ = agents.competitor.revise(
                        original_prompt=ctx.prompts.competitor if ctx.prompts else "",
                        revision_prompt=revision_prompt,
                        tool_context=ctx.tool_context,
                    )
                    is_code = "__code__" in ctx.current_strategy
                    if is_code:
                        revised, _ = agents.translator.translate_code(raw_text)
                    else:
                        revised, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
                    ctx.current_strategy = revised
                except Exception:
                    LOGGER.warning("harness revision failed", exc_info=True)
                    break
                harness_result = harness_loader.validate_strategy(ctx.current_strategy, ctx.scenario)
                if harness_result.passed:
                    break
        if harness_result.passed:
            events.emit("harness_validation_passed", {
                "generation": ctx.generation,
            })

    # --- Phase 2: Self-play dry-run ---
    if not ctx.settings.prevalidation_dry_run_enabled:
        return ctx

    events.emit("dry_run_started", {
        "generation": ctx.generation,
    })

    validator = StrategyValidator(ctx.scenario, ctx.settings)

    for attempt in range(ctx.settings.prevalidation_max_retries + 1):
        result = validator.validate(ctx.current_strategy)

        if result.passed:
            events.emit("dry_run_passed", {
                "generation": ctx.generation,
                "attempt": attempt,
            })
            return ctx

        # Validation failed
        events.emit("dry_run_failed", {
            "generation": ctx.generation,
            "attempt": attempt,
            "errors": result.errors,
        })

        if attempt < ctx.settings.prevalidation_max_retries:
            # Get revision from competitor
            events.emit("dry_run_revision", {
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
