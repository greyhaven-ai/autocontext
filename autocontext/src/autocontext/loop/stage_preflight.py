"""Pre-flight harness synthesis stage (AC-150).

Runs once before Generation 1 to synthesize a harness validator using the
HarnessSynthesizer. Skips if disabled, if generation != 1, or if a harness
already exists (unless force mode is enabled).
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from autocontext.execution.harness_synthesizer import HarnessSynthesizer
from autocontext.execution.sample_states import SampleStateGenerator
from autocontext.loop.stage_types import GenerationContext
from autocontext.providers.registry import get_provider

if TYPE_CHECKING:
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import ArtifactStore

LOGGER = logging.getLogger(__name__)


def stage_preflight(
    ctx: GenerationContext,
    *,
    events: EventStreamEmitter,
    artifacts: ArtifactStore,
) -> GenerationContext:
    """Stage 0.5: Pre-flight harness synthesis (before generation 1 only).

    Skips if:
    - ``harness_preflight_enabled`` is False
    - ``ctx.generation`` != 1
    - Harness already exists at ``preflight_synthesized.py`` (unless force=True)

    When it runs, creates a ``HarnessSynthesizer``, generates sample states,
    runs synthesis, and saves the result.
    """
    settings = ctx.settings

    # Gate: disabled
    if not settings.harness_preflight_enabled:
        return ctx

    # Gate: not generation 1
    if ctx.generation != 1:
        return ctx

    harness_dir = artifacts.harness_dir(ctx.scenario_name)
    harness_path = harness_dir / "preflight_synthesized.py"

    # Gate: harness already exists (unless force)
    if harness_path.exists() and not settings.harness_preflight_force:
        events.emit("preflight_skipped", {
            "run_id": ctx.run_id,
            "scenario": ctx.scenario_name,
            "reason": "harness already exists",
        })
        return ctx

    # --- Run synthesis ---
    events.emit("preflight_start", {
        "run_id": ctx.run_id,
        "scenario": ctx.scenario_name,
    })

    provider = get_provider(settings)
    state_gen = SampleStateGenerator(ctx.scenario)
    sample_states = state_gen.generate_with_ground_truth()

    synthesizer = HarnessSynthesizer(
        ctx.scenario,
        provider,
        max_iterations=settings.harness_preflight_max_iterations,
        accuracy_target=settings.harness_preflight_target_accuracy,
    )

    result = synthesizer.synthesize(sample_states)

    # Save output
    harness_dir.mkdir(parents=True, exist_ok=True)
    harness_path.write_text(result.harness_source, encoding="utf-8")

    LOGGER.info(
        "preflight synthesis %s: accuracy=%.2f, iterations=%d",
        "converged" if result.converged else "incomplete",
        result.accuracy,
        result.iterations,
    )

    # Emit completion event
    event_name = "preflight_complete" if result.converged else "preflight_incomplete"
    events.emit(event_name, {
        "run_id": ctx.run_id,
        "scenario": ctx.scenario_name,
        "converged": result.converged,
        "accuracy": result.accuracy,
        "iterations": result.iterations,
    })

    return ctx
