"""Staged validation stage — progressive candidate checks before tournament.

Runs the staged validation pipeline (AC-197/AC-198) against the current
strategy.  Stages execute sequentially with early-exit on failure.  Results
and metrics are attached to the GenerationContext and persisted to SQLite.

This stage is pure: it does NOT own retries or revision.  When validation
fails, it sets ``ctx.gate_decision = "retry"`` so the caller can decide
whether to revise or proceed.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.harness.validation import ValidationPipeline
from autocontext.harness.validation.stages import ValidationRunner, default_pipeline
from autocontext.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import SQLiteStore

logger = logging.getLogger(__name__)


def stage_staged_validation(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
    sqlite: SQLiteStore,
) -> GenerationContext:
    """Run staged validation pipeline on ``ctx.current_strategy``.

    When ``staged_validation_enabled`` is False, returns immediately.
    On failure, sets ``ctx.gate_decision = "retry"`` to signal that the
    strategy should be revised before tournament.
    """
    if not ctx.settings.staged_validation_enabled:
        return ctx

    events.emit("staged_validation_started", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
    })

    runner = ValidationRunner(pipeline=default_pipeline())
    candidate = _candidate_for_validation(ctx.current_strategy)
    results = runner.validate(candidate=candidate, scenario=ctx.scenario)

    # Attach to context
    ctx.staged_validation_results = results
    ctx.staged_validation_metrics = runner.metrics.to_event_payload()

    all_passed = ValidationPipeline.all_passed(results)
    failed_stage = ValidationPipeline.failed_stage(results)

    events.emit("staged_validation_completed", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "passed": all_passed,
        "failed_stage": failed_stage,
        "stages": [
            {
                "stage": r.stage,
                "name": r.name,
                "status": r.status.value,
                "duration_ms": round(r.duration_ms, 2),
                "error": r.error,
                "error_code": r.error_code,
            }
            for r in results
        ],
        "metrics": ctx.staged_validation_metrics,
    })

    # Persist to SQLite
    try:
        sqlite.insert_staged_validation_results(
            ctx.run_id,
            ctx.generation,
            [
                {
                    "stage_order": r.stage,
                    "stage_name": r.name,
                    "status": r.status.value,
                    "duration_ms": r.duration_ms,
                    "error": r.error,
                    "error_code": r.error_code,
                }
                for r in results
            ],
        )
    except Exception:
        logger.warning("failed to persist staged validation results", exc_info=True)

    # On failure, set gate_decision to trigger retry
    if not all_passed:
        logger.info(
            "staged validation failed at stage '%s' for generation %d",
            failed_stage, ctx.generation,
        )
        ctx.gate_decision = "retry"

    return ctx


def _candidate_for_validation(candidate: object) -> object:
    """Normalize strategy wrappers into the artifact shape expected by the runner."""
    if isinstance(candidate, dict):
        code = candidate.get("__code__")
        if isinstance(code, str):
            return code
    return candidate
