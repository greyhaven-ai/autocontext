"""Pre-validation stage — run harness validators and self-play dry-run before tournament.

Catches invalid strategies before wasting tournament compute.
Disabled by default (prevalidation_enabled=False).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from autocontext.analytics.regression_fixtures import FixtureStore, RegressionFixture
from autocontext.execution.strategy_validator import StrategyValidator, ValidationResult
from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.types import EvaluationLimits
from autocontext.knowledge.dead_end_manager import DeadEndEntry
from autocontext.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.execution.harness_loader import HarnessLoader
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import ArtifactStore

logger = logging.getLogger(__name__)


def stage_prevalidation(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
    agents: AgentOrchestrator,
    harness_loader: HarnessLoader | None = None,
    artifacts: ArtifactStore | None = None,
    supervisor: ExecutionSupervisor | None = None,
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
            logger.warning(
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
                    logger.warning("harness revision failed", exc_info=True)
                    break
                harness_result = harness_loader.validate_strategy(ctx.current_strategy, ctx.scenario)
                if harness_result.passed:
                    break
        if harness_result.passed:
            events.emit("harness_validation_passed", {
                "generation": ctx.generation,
            })
        elif ctx.settings.dead_end_tracking_enabled and artifacts is not None:
            reason = f"Harness validation failed after {ctx.settings.prevalidation_max_retries} revisions"
            if harness_result.errors:
                reason += f": {harness_result.errors[0]}"
            _record_dead_end(
                artifacts, ctx.scenario_name, ctx.generation, ctx.current_strategy,
                reason,
            )

    # --- Phase 2: Regression fixtures ---
    if (
        ctx.settings.prevalidation_regression_fixtures_enabled
        and artifacts is not None
        and supervisor is not None
    ):
        fixtures = _load_regression_fixtures(ctx, artifacts=artifacts)
        if fixtures:
            events.emit("regression_fixtures_started", {
                "generation": ctx.generation,
                "fixture_count": len(fixtures),
            })
            validator = StrategyValidator(ctx.scenario, ctx.settings)
            for attempt in range(ctx.settings.prevalidation_max_retries + 1):
                fixture_result = _validate_against_regression_fixtures(
                    ctx,
                    fixtures=fixtures,
                    supervisor=supervisor,
                )
                if fixture_result.passed:
                    events.emit("regression_fixtures_passed", {
                        "generation": ctx.generation,
                        "attempt": attempt,
                        "fixture_count": len(fixtures),
                    })
                    break

                events.emit("regression_fixtures_failed", {
                    "generation": ctx.generation,
                    "attempt": attempt,
                    "errors": fixture_result.errors,
                })

                if attempt < ctx.settings.prevalidation_max_retries:
                    events.emit("regression_fixtures_revision", {
                        "generation": ctx.generation,
                        "attempt": attempt,
                    })
                    revision_prompt = validator.format_revision_prompt(fixture_result, ctx.current_strategy)
                    try:
                        raw_text, _ = agents.competitor.revise(
                            original_prompt=ctx.prompts.competitor if ctx.prompts else "",
                            revision_prompt=revision_prompt,
                            tool_context=ctx.tool_context,
                        )
                        is_code_strategy = "__code__" in ctx.current_strategy
                        if is_code_strategy:
                            revised, _ = agents.translator.translate_code(raw_text)
                        else:
                            revised, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
                        ctx.current_strategy = revised
                    except Exception:
                        logger.warning("regression fixture revision failed, keeping current strategy", exc_info=True)
                elif ctx.settings.dead_end_tracking_enabled and artifacts is not None:
                    reason = (
                        "Regression fixtures failed after "
                        f"{ctx.settings.prevalidation_max_retries} revisions"
                    )
                    if fixture_result.errors:
                        reason += f": {fixture_result.errors[0]}"
                    _record_dead_end(
                        artifacts, ctx.scenario_name, ctx.generation, ctx.current_strategy, reason,
                    )

    # --- Phase 3: Self-play dry-run ---
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
                logger.warning("prevalidation revision failed, keeping current strategy", exc_info=True)

    # All retries exhausted -- fall through to tournament with last strategy
    logger.warning(
        "prevalidation exhausted %d retries, proceeding with last strategy",
        ctx.settings.prevalidation_max_retries,
    )
    if ctx.settings.dead_end_tracking_enabled and artifacts is not None:
        last_errors = result.errors if result else []
        reason = f"Pre-validation failed after {ctx.settings.prevalidation_max_retries} revisions"
        if last_errors:
            reason += f": {last_errors[0]}"
        _record_dead_end(artifacts, ctx.scenario_name, ctx.generation, ctx.current_strategy, reason)

    return ctx


def _load_regression_fixtures(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> list[RegressionFixture]:
    store = FixtureStore(artifacts.knowledge_root / "analytics")
    fixtures = store.list_for_scenario(ctx.scenario_name)
    return fixtures[: ctx.settings.prevalidation_regression_fixture_limit]


def _validate_against_regression_fixtures(
    ctx: GenerationContext,
    *,
    fixtures: list[RegressionFixture],
    supervisor: ExecutionSupervisor,
) -> ValidationResult:
    evaluator = ScenarioEvaluator(ctx.scenario, supervisor)
    limits = EvaluationLimits(timeout_seconds=ctx.settings.harness_timeout_seconds)
    errors: list[str] = []

    for fixture in fixtures:
        try:
            result = evaluator.evaluate(ctx.current_strategy, fixture.seed, limits)
        except Exception as exc:
            errors.append(
                f"{fixture.fixture_id}: regression fixture '{fixture.description}' raised {exc}"
            )
            continue

        if not result.passed:
            if result.errors:
                errors.extend(
                    f"{fixture.fixture_id}: {err}" for err in result.errors
                )
            else:
                errors.append(
                    f"{fixture.fixture_id}: regression fixture '{fixture.description}' failed validation"
                )
            continue

        if result.score < fixture.expected_min_score:
            errors.append(
                f"{fixture.fixture_id}: score {result.score:.4f} below expected minimum "
                f"{fixture.expected_min_score:.4f} on seed {fixture.seed} "
                f"({fixture.description})"
            )

    return ValidationResult(
        passed=not errors,
        errors=errors,
        match_summary=f"validated against {len(fixtures)} regression fixtures",
    )


def _record_dead_end(
    artifacts: ArtifactStore,
    scenario_name: str,
    generation: int,
    strategy: dict[str, object],
    reason: str,
) -> None:
    """Record a dead-end entry from a failed pre-validation."""
    summary = json.dumps(strategy, sort_keys=True)
    entry = DeadEndEntry(
        generation=generation,
        strategy_summary=summary[:120] + "..." if len(summary) > 120 else summary,
        score=0.0,
        reason=reason,
    )
    artifacts.append_dead_end(scenario_name, entry.to_markdown())
