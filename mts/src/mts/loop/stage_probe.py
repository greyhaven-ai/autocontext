"""Probe stage — run a small number of matches before the full tournament.

The competitor observes probe results and refines its strategy before
the full evaluation. Disabled by default (probe_matches=0).
"""
from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING

from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.execution.supervisor import ExecutionSupervisor
    from mts.loop.events import EventStreamEmitter

LOGGER = logging.getLogger(__name__)


def stage_probe(
    ctx: GenerationContext,
    *,
    agents: AgentOrchestrator,
    events: EventStreamEmitter,
    supervisor: ExecutionSupervisor,
) -> GenerationContext:
    """Stage 2.5: Run probe matches and refine strategy before full tournament."""
    if ctx.settings.probe_matches < 1:
        return ctx
    assert ctx.prompts is not None, "stage_knowledge_setup must run first"

    events.emit("probe_started", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "probe_matches": ctx.settings.probe_matches,
    })

    # Run probe matches
    evaluator = ScenarioEvaluator(ctx.scenario, supervisor)
    runner = EvaluationRunner(evaluator)
    probe_result = runner.run(
        candidate=ctx.current_strategy,
        seed_base=ctx.settings.seed_base + (ctx.generation * 100) + 90,
        trials=ctx.settings.probe_matches,
        limits=HarnessLimits(),
        challenger_elo=ctx.challenger_elo,
    )

    # Build refinement prompt with probe observations
    best_eval = max(probe_result.results, key=lambda r: r.score)
    best_exec = best_eval.metadata["execution_output"]
    probe_narrative = ctx.scenario.replay_to_narrative(best_exec.result.replay)

    is_code_strategy = "__code__" in ctx.current_strategy

    refinement_prompt = (
        ctx.prompts.competitor
        + f"\n\n--- PROBE OBSERVATION ---\n"
        f"You ran {ctx.settings.probe_matches} probe match(es). "
        f"Best probe score: {probe_result.best_score:.4f}.\n"
        f"Replay narrative:\n{probe_narrative}\n\n"
        f"Based on this observation, refine your strategy. "
        f"You may keep your approach if the probe looks promising, "
        f"or adjust based on what you observed.\n"
    )
    if is_code_strategy:
        refinement_prompt += "Emit refined Python code.\n"
    else:
        refinement_prompt += (
            f"Previous strategy: {json.dumps(ctx.current_strategy, sort_keys=True)}\n"
        )

    # Attempt refinement
    probe_usage: dict[str, object] = {}
    try:
        raw_text, refinement_exec = agents.competitor.run(refinement_prompt, tool_context=ctx.tool_context)
        probe_usage = {
            "input_tokens": refinement_exec.usage.input_tokens,
            "output_tokens": refinement_exec.usage.output_tokens,
        }
        if is_code_strategy:
            revised, _ = agents.translator.translate_code(raw_text)
        else:
            revised, _ = agents.translator.translate(raw_text, ctx.strategy_interface)

        # Validate non-code strategies
        if "__code__" not in revised:
            state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
            valid, reason = ctx.scenario.validate_actions(state, "challenger", revised)
            if not valid:
                LOGGER.warning("probe refinement produced invalid strategy: %s", reason)
                raise ValueError(reason)

        ctx.current_strategy = revised
        ctx.probe_refinement_applied = True
        LOGGER.info("probe refinement applied (probe_score=%.4f)", probe_result.best_score)
    except Exception:
        LOGGER.warning("probe refinement failed, keeping original strategy", exc_info=True)

    events.emit("probe_completed", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "probe_score": probe_result.best_score,
        "refined": ctx.probe_refinement_applied,
        **({} if not probe_usage else {"refinement_usage": probe_usage}),
    })

    return ctx
