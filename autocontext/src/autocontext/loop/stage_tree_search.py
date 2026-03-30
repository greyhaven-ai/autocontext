"""Tree search stage — multi-hypothesis strategy search with Thompson sampling (AC-80)."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from autocontext.agents.architect import parse_architect_harness_specs, parse_architect_tool_specs, parse_dag_changes
from autocontext.agents.coach import parse_coach_sections
from autocontext.agents.parsers import parse_analyst_output, parse_architect_output, parse_coach_output, parse_competitor_output
from autocontext.agents.types import AgentOutputs
from autocontext.harness.evaluation.runner import EvaluationRunner
from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits
from autocontext.knowledge.rapid_gate import rapid_gate
from autocontext.loop.hypothesis_tree import HypothesisTree
from autocontext.loop.refinement_prompt import build_refinement_prompt
from autocontext.loop.stage_types import GenerationContext

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import ArtifactStore, SQLiteStore

logger = logging.getLogger(__name__)

# Max seed hypotheses to generate at the start of tree search
_MAX_INITIAL_SEEDS = 3


def stage_tree_search(
    ctx: GenerationContext,
    *,
    orchestrator: AgentOrchestrator,
    supervisor: ExecutionSupervisor,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    events: EventStreamEmitter,
    on_role_event: Any | None = None,
) -> GenerationContext:
    """Combined agent-generation + tournament stage for tree search mode.

    Replaces ``stage_agent_generation`` + ``stage_tournament`` when
    ``exploration_mode == "tree"``.  Generates multiple seed strategies,
    refines them via Thompson-sampling selection, runs mini-tournaments,
    and finally runs analyst/coach/architect with the best strategy.
    """
    assert ctx.prompts is not None, "stage_knowledge_setup must run first"

    settings = ctx.settings
    scenario = ctx.scenario
    strategy_interface = ctx.strategy_interface

    tree = HypothesisTree(
        max_hypotheses=settings.tree_max_hypotheses,
        temperature=settings.tree_sampling_temperature,
    )

    events.emit("tree_search_start", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "max_hypotheses": settings.tree_max_hypotheses,
    })

    # ── Phase 1: Seed hypotheses ─────────────────────────────────────
    initial_seeds = min(settings.tree_max_hypotheses, _MAX_INITIAL_SEEDS)
    trials_per_seed = max(1, settings.matches_per_generation // 2)

    for seed_idx in range(initial_seeds):
        try:
            strategy = _generate_and_translate(
                orchestrator, ctx.prompts.competitor, strategy_interface,
                ctx.tool_context, settings.code_strategies_enabled,
            )
        except Exception:
            logger.debug("seed %d generation failed", seed_idx, exc_info=True)
            continue

        if not _validate_strategy(strategy, scenario, settings.seed_base + ctx.generation + seed_idx):
            continue

        node = tree.add(strategy, generation=ctx.generation)
        tournament = _run_mini_tournament(
            scenario, supervisor, strategy,
            seed_base=settings.seed_base + (ctx.generation * 100) + (seed_idx * 10),
            trials=trials_per_seed,
            challenger_elo=ctx.challenger_elo,
            challenger_uncertainty=ctx.challenger_uncertainty,
            scoring_backend=settings.scoring_backend,
        )
        tree.update(node.id, [r.score for r in tournament.results], tournament.elo_after)

    # Fallback: if no seeds survived, run one more attempt with the base prompt
    if tree.size() == 0:
        logger.warning("all seed hypotheses failed; falling back to single attempt")
        raw_text, competitor_exec = orchestrator.competitor.run(
            ctx.prompts.competitor, tool_context=ctx.tool_context,
        )
        if settings.code_strategies_enabled:
            strategy, _ = orchestrator.translator.translate_code(raw_text)
        else:
            strategy, _ = orchestrator.translator.translate(raw_text, strategy_interface)
        tree.add(strategy, generation=ctx.generation)

    # ── Phase 2: Refinement loop ─────────────────────────────────────
    max_rounds = settings.tree_max_hypotheses * 2
    for round_idx in range(max_rounds):
        if tree.converged() or tree.size() < 2:
            events.emit("tree_converged", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "round": round_idx,
            })
            break

        selected = tree.select()
        events.emit("hypothesis_selected", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "node_id": selected.id,
            "elo": selected.elo,
        })

        # Build refinement prompt
        recent_scores = selected.scores[-5:] if selected.scores else []
        match_feedback = f"Recent scores: {recent_scores}, Elo: {selected.elo:.0f}"
        refinement_prompt = build_refinement_prompt(
            scenario_rules=scenario.describe_rules(),
            strategy_interface=strategy_interface,
            evaluation_criteria=scenario.describe_evaluation_criteria(),
            parent_strategy=json.dumps(selected.strategy, sort_keys=True),
            match_feedback=match_feedback,
        )

        try:
            refined_strategy = _generate_and_translate(
                orchestrator, refinement_prompt, strategy_interface,
                ctx.tool_context, settings.code_strategies_enabled,
            )
        except Exception:
            logger.debug("refinement round %d failed", round_idx, exc_info=True)
            continue

        if not _validate_strategy(refined_strategy, scenario, settings.seed_base + ctx.generation):
            continue

        refined_node = tree.add(refined_strategy, parent_id=selected.id, generation=ctx.generation)
        tournament = _run_mini_tournament(
            scenario, supervisor, refined_strategy,
            seed_base=settings.seed_base + (ctx.generation * 100) + 50 + round_idx,
            trials=trials_per_seed,
            challenger_elo=ctx.challenger_elo,
            challenger_uncertainty=ctx.challenger_uncertainty,
            scoring_backend=settings.scoring_backend,
        )
        tree.update(refined_node.id, [r.score for r in tournament.results], tournament.elo_after)

        events.emit("hypothesis_refined", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "parent_id": selected.id,
            "child_id": refined_node.id,
            "score": tournament.best_score,
        })

    # ── Phase 3: Final tournament with best strategy ─────────────────
    best_node = tree.best()
    best_strategy = best_node.strategy

    evaluator = ScenarioEvaluator(scenario, supervisor)
    runner = EvaluationRunner(evaluator, scoring_backend=settings.scoring_backend)

    def _on_match(match_index: int, result: Any) -> None:
        events.emit("match_completed", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "match_index": match_index,
            "score": result.score,
        })

    final_tournament = runner.run(
        candidate=best_strategy,
        seed_base=settings.seed_base + (ctx.generation * 100) + 90,
        trials=settings.matches_per_generation,
        limits=HarnessLimits(),
        challenger_elo=ctx.challenger_elo,
        challenger_uncertainty=ctx.challenger_uncertainty,
        on_result=_on_match,
    )

    # ── Phase 4: Gate decision (rapid-style: advance or rollback) ────
    gate_result = rapid_gate(final_tournament.best_score, ctx.previous_best)
    gate_decision = gate_result.decision
    gate_delta = round(final_tournament.best_score - ctx.previous_best, 6)

    events.emit("tournament_completed", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "mean_score": final_tournament.mean_score,
        "best_score": final_tournament.best_score,
        "wins": final_tournament.wins,
        "losses": final_tournament.losses,
        "scoring_backend": final_tournament.scoring_backend,
        "rating_uncertainty": final_tournament.uncertainty_after,
    })
    events.emit("gate_decided", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "decision": gate_decision,
        "delta": gate_delta,
        "scoring_backend": final_tournament.scoring_backend,
        "rating_uncertainty": final_tournament.uncertainty_after,
    })

    # ── Phase 5: Run analyst / coach / architect ─────────────────────
    def _notify(role: str, status: str) -> None:
        if on_role_event:
            on_role_event(role, status)

    _notify("analyst", "started")
    analyst_exec = orchestrator.analyst.run(ctx.prompts.analyst)
    _notify("analyst", "completed")

    enriched_coach = ctx.prompts.coach + f"\n\n--- Analyst findings (this generation) ---\n{analyst_exec.content}\n"
    _notify("coach", "started")
    coach_exec = orchestrator.coach.run(enriched_coach)
    _notify("coach", "completed")

    architect_prompt = ctx.prompts.architect
    if ctx.generation % settings.architect_every_n_gens != 0:
        architect_prompt += "\n\nArchitect cadence note: no major intervention; return minimal status + empty tools array."
    _notify("architect", "started")
    architect_exec = orchestrator.architect.run(architect_prompt)
    _notify("architect", "completed")

    tools = parse_architect_tool_specs(architect_exec.content)
    harness_specs = parse_architect_harness_specs(architect_exec.content)
    coach_playbook, coach_lessons, coach_hints = parse_coach_sections(coach_exec.content)

    competitor_typed = parse_competitor_output(
        json.dumps(best_strategy, sort_keys=True), best_strategy,
        is_code_strategy=settings.code_strategies_enabled,
    )
    analyst_typed = parse_analyst_output(analyst_exec.content)
    coach_typed = parse_coach_output(coach_exec.content)
    architect_typed = parse_architect_output(architect_exec.content)

    # Build a synthetic competitor RoleExecution for the tree search phase
    from autocontext.harness.core.types import RoleExecution, RoleUsage

    tree_competitor_exec = RoleExecution(
        role="competitor",
        content=json.dumps(best_strategy, sort_keys=True),
        usage=RoleUsage(model=settings.model_competitor, input_tokens=0, output_tokens=0, latency_ms=0),
        subagent_id="",
        status="completed",
    )
    translator_exec = RoleExecution(
        role="translator",
        content=json.dumps(best_strategy, sort_keys=True),
        usage=RoleUsage(model=settings.model_translator, input_tokens=0, output_tokens=0, latency_ms=0),
        subagent_id="",
        status="completed",
    )

    outputs = AgentOutputs(
        strategy=best_strategy,
        analysis_markdown=analyst_exec.content,
        coach_markdown=coach_exec.content,
        coach_playbook=coach_playbook,
        coach_lessons=coach_lessons,
        coach_competitor_hints=coach_hints,
        architect_markdown=architect_exec.content,
        architect_tools=tools,
        architect_harness_specs=harness_specs,
        role_executions=[tree_competitor_exec, translator_exec, analyst_exec, coach_exec, architect_exec],
        competitor_output=competitor_typed,
        analyst_output=analyst_typed,
        coach_output=coach_typed,
        architect_output=architect_typed,
    )

    # ── Persist agent outputs to sqlite ──────────────────────────────
    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[
            ("competitor", json.dumps(best_strategy, sort_keys=True)),
            ("analyst", analyst_exec.content),
            ("coach", coach_exec.content),
            ("architect", architect_exec.content),
        ],
        role_metrics=[
            (
                role_exec.role,
                role_exec.usage.model,
                role_exec.usage.input_tokens,
                role_exec.usage.output_tokens,
                role_exec.usage.latency_ms,
                role_exec.subagent_id,
                role_exec.status,
            )
            for role_exec in outputs.role_executions
        ],
    )

    created_tools = artifacts.persist_tools(ctx.scenario_name, ctx.generation, tools)
    if settings.harness_validators_enabled and harness_specs:
        artifacts.persist_harness(ctx.scenario_name, ctx.generation, harness_specs)

    ctx.dag_changes = parse_dag_changes(architect_exec.content)

    if settings.config_adaptive_enabled:
        from autocontext.knowledge.tuning import parse_tuning_proposal

        ctx.tuning_proposal = parse_tuning_proposal(architect_exec.content)

    # ── Replay narrative from best match ─────────────────────────────
    best_eval = max(final_tournament.results, key=lambda r: r.score)
    best_exec_output = best_eval.metadata["execution_output"]
    replay_narrative = scenario.replay_to_narrative(best_exec_output.result.replay)
    gen_dir = artifacts.generation_dir(ctx.run_id, ctx.generation)
    artifacts.buffered_write_markdown(gen_dir / "narrative.md", replay_narrative)

    # ── Update ctx for downstream stages ─────────────────────────────
    ctx.outputs = outputs
    ctx.current_strategy = best_strategy
    ctx.created_tools = created_tools
    ctx.strategy_interface = strategy_interface
    ctx.tool_context = ctx.tool_context
    ctx.tournament = final_tournament
    ctx.gate_decision = gate_decision
    ctx.gate_delta = gate_delta
    ctx.replay_narrative = replay_narrative
    ctx.attempt = 0
    ctx.score_history.append(final_tournament.best_score)
    ctx.gate_decision_history.append(gate_decision)

    if gate_decision == "advance":
        ctx.previous_best = max(ctx.previous_best, final_tournament.best_score)
        ctx.challenger_elo = final_tournament.elo_after
        ctx.challenger_uncertainty = final_tournament.uncertainty_after

    return ctx


# ── Helper functions ─────────────────────────────────────────────────


def _generate_and_translate(
    orchestrator: AgentOrchestrator,
    prompt: str,
    strategy_interface: str,
    tool_context: str,
    code_strategies: bool,
) -> dict[str, Any]:
    """Run competitor + translator and return the parsed strategy dict."""
    if code_strategies:
        from autocontext.prompts.templates import code_strategy_competitor_suffix

        prompt = prompt + code_strategy_competitor_suffix(strategy_interface)

    raw_text, _ = orchestrator.competitor.run(prompt, tool_context=tool_context)

    if code_strategies:
        strategy, _ = orchestrator.translator.translate_code(raw_text)
    else:
        strategy, _ = orchestrator.translator.translate(raw_text, strategy_interface)
    return strategy


def _validate_strategy(
    strategy: dict[str, Any],
    scenario: Any,
    seed: int,
) -> bool:
    """Validate a non-code strategy against the scenario. Returns True if valid."""
    if "__code__" in strategy:
        return True
    state = scenario.initial_state(seed=seed)
    valid, _ = scenario.validate_actions(state, "challenger", strategy)
    return bool(valid)


def _run_mini_tournament(
    scenario: Any,
    supervisor: Any,
    strategy: dict[str, Any],
    *,
    seed_base: int,
    trials: int,
    challenger_elo: float,
    challenger_uncertainty: float | None,
    scoring_backend: str,
) -> Any:
    """Run a small tournament for a single hypothesis."""
    evaluator = ScenarioEvaluator(scenario, supervisor)
    runner = EvaluationRunner(evaluator, scoring_backend=scoring_backend)
    return runner.run(
        candidate=strategy,
        seed_base=seed_base,
        trials=trials,
        limits=HarnessLimits(),
        challenger_elo=challenger_elo,
        challenger_uncertainty=challenger_uncertainty,
    )
