"""Stage helpers — exploration (extracted from stages.py, AC-482)."""

from __future__ import annotations

import json

# Avoid circular: import at function call site if needed
import logging
from typing import TYPE_CHECKING, Any

from autocontext.harness.core.types import RoleExecution
from autocontext.harness.evaluation.runner import EvaluationRunner
from autocontext.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from autocontext.harness.evaluation.types import EvaluationLimits as HarnessLimits
from autocontext.loop.exploration import (
    BasinCandidate,
    BranchRecord,
    DivergentCompetitorConfig,
    MultiBasinConfig,
    generate_basin_candidates,
    should_spawn_divergent,
    should_trigger_multi_basin,
)
from autocontext.loop.stage_helpers.tournament_prep import _build_live_opponent_pool
from autocontext.loop.stage_types import GenerationContext

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from autocontext.agents.orchestrator import AgentOrchestrator
    from autocontext.agents.types import AgentOutputs
    from autocontext.execution.supervisor import ExecutionSupervisor
    from autocontext.loop.events import EventStreamEmitter
    from autocontext.storage import SQLiteStore


def _load_recent_numeric_strategies(
    sqlite: SQLiteStore,
    *,
    run_id: str,
    window: int,
) -> list[dict[str, Any]]:
    """Load recent persisted competitor strategies for novelty comparison."""
    try:
        history = sqlite.get_strategy_score_history(run_id)
    except Exception:
        logger.debug("failed to load strategy history for novelty", exc_info=True)
        return []

    recent: list[dict[str, Any]] = []
    for row in history[-window:]:
        if not isinstance(row, dict):
            continue
        raw_content = row.get("content")
        if not isinstance(raw_content, str) or not raw_content.strip():
            continue
        try:
            parsed = json.loads(raw_content)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            recent.append(parsed)
    return recent


def _replace_prompt_section(
    prompt: str,
    *,
    label: str,
    old_value: str,
    new_value: str,
    anchor_label: str | None = None,
) -> str:
    old_block = f"{label}:\n{old_value}\n\n" if old_value else ""
    new_block = f"{label}:\n{new_value}\n\n" if new_value else ""
    if old_block and old_block in prompt:
        return prompt.replace(old_block, new_block, 1)
    if old_block:
        return prompt
    if not new_block:
        return prompt
    if anchor_label:
        anchor = f"{anchor_label}:\n"
        index = prompt.find(anchor)
        if index >= 0:
            block_end = prompt.find("\n\n", index)
            if block_end >= 0:
                insert_at = block_end + 2
                return prompt[:insert_at] + new_block + prompt[insert_at:]
    return prompt


def _build_branch_competitor_prompt(
    ctx: GenerationContext,
    *,
    playbook: str,
    lessons: str,
    note: str = "",
) -> str:
    if ctx.prompts is None:
        raise RuntimeError("stage_knowledge_setup must run first")

    prompt = _replace_prompt_section(
        ctx.prompts.competitor,
        label="Current playbook",
        old_value=ctx.base_playbook,
        new_value=playbook,
    )
    prompt = _replace_prompt_section(
        prompt,
        label="Operational lessons (from prior generations)",
        old_value=ctx.base_lessons,
        new_value=lessons,
        anchor_label="Current playbook",
    )
    if note:
        prompt += f"\n\nExploration branch note:\n{note}"
    return prompt


def _generate_branch_strategy(
    ctx: GenerationContext,
    *,
    orchestrator: AgentOrchestrator,
    prompt: str,
    temperature: float,
) -> tuple[dict[str, Any], RoleExecution, RoleExecution]:
    """Run competitor + translator for a single exploration branch."""
    if ctx.prompts is None:
        raise RuntimeError("stage_knowledge_setup must run first")

    competitor_prompt = prompt
    if ctx.settings.code_strategies_enabled:
        from autocontext.prompts.templates import code_strategy_competitor_suffix

        competitor_prompt += code_strategy_competitor_suffix(ctx.strategy_interface)

    with orchestrator._use_role_runtime(  # noqa: SLF001 - stage needs routed role runtime
        "competitor",
        orchestrator.competitor,
        generation=ctx.generation,
        scenario_name=ctx.scenario_name,
    ):
        raw_text, competitor_exec = orchestrator.competitor.run(
            competitor_prompt,
            tool_context=ctx.tool_context,
            temperature=temperature,
        )
    with orchestrator._use_role_runtime(  # noqa: SLF001 - stage needs routed role runtime
        "translator",
        orchestrator.translator,
        generation=ctx.generation,
        scenario_name=ctx.scenario_name,
    ):
        if ctx.settings.code_strategies_enabled:
            strategy, translator_exec = orchestrator.translator.translate_code(raw_text)
        else:
            strategy, translator_exec = orchestrator.translator.translate(raw_text, ctx.strategy_interface)
    return strategy, competitor_exec, translator_exec


def _select_exploration_strategy(
    ctx: GenerationContext,
    *,
    outputs: AgentOutputs,
    orchestrator: AgentOrchestrator,
    supervisor: ExecutionSupervisor | None,
    sqlite: SQLiteStore,
    events: EventStreamEmitter | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Optionally explore multiple competitor basins and return the selected strategy."""
    settings = ctx.settings
    if supervisor is None:
        return outputs.strategy, {}

    multi_basin_config = MultiBasinConfig(
        enabled=settings.multi_basin_enabled,
        trigger_rollbacks=settings.multi_basin_trigger_rollbacks,
        candidates=settings.multi_basin_candidates,
        periodic_every_n=settings.multi_basin_periodic_every_n,
    )
    divergent_config = DivergentCompetitorConfig(
        enabled=settings.divergent_competitor_enabled,
        rollback_threshold=settings.divergent_rollback_threshold,
        temperature=settings.divergent_temperature,
    )
    multi_basin_triggered = should_trigger_multi_basin(
        ctx.gate_decision_history,
        ctx.generation,
        multi_basin_config,
    )
    divergent_triggered = should_spawn_divergent(ctx.gate_decision_history, divergent_config)

    if not multi_basin_triggered and not divergent_triggered:
        return outputs.strategy, {}

    branch_specs: list[BasinCandidate] = []
    if multi_basin_triggered:
        branch_specs = generate_basin_candidates(
            ctx.base_playbook,
            ctx.base_lessons,
            multi_basin_config,
        )
    else:
        branch_specs = [
            BasinCandidate(
                branch_type="conservative",
                playbook=ctx.base_playbook,
                lessons=ctx.base_lessons,
                temperature=0.2,
            ),
            BasinCandidate(
                branch_type="divergent",
                playbook="",
                lessons=ctx.base_lessons,
                temperature=divergent_config.temperature,
                metadata={"note": "Fresh start with lessons only"},
            ),
        ]

    candidate_entries: list[dict[str, Any]] = [{
        "branch_type": "conservative",
        "strategy": outputs.strategy,
        "temperature": 0.2,
        "metadata": {"source": "base_generation"},
    }]
    seen_strategies = {json.dumps(outputs.strategy, sort_keys=True)}

    if events is not None:
        events.emit("exploration_started", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "multi_basin_triggered": multi_basin_triggered,
            "divergent_triggered": divergent_triggered,
            "gate_history": ctx.gate_decision_history,
        })

    for branch in branch_specs:
        if branch.branch_type == "conservative":
            continue
        branch_temperature = (
            divergent_config.temperature
            if branch.branch_type == "divergent"
            else branch.temperature
        )
        branch_prompt = _build_branch_competitor_prompt(
            ctx,
            playbook=branch.playbook,
            lessons=branch.lessons,
            note=str(branch.metadata.get("note", "")),
        )
        try:
            strategy, _, _ = _generate_branch_strategy(
                ctx,
                orchestrator=orchestrator,
                prompt=branch_prompt,
                temperature=branch_temperature,
            )
        except Exception:
            logger.debug("failed to generate %s exploration branch", branch.branch_type, exc_info=True)
            continue

        serialized = json.dumps(strategy, sort_keys=True)
        if serialized in seen_strategies:
            continue
        if "__code__" not in strategy:
            state = ctx.scenario.initial_state(seed=settings.seed_base + ctx.generation)
            valid, _reason = ctx.scenario.validate_actions(state, "challenger", strategy)
            if not valid:
                continue
        seen_strategies.add(serialized)
        candidate_entries.append({
            "branch_type": branch.branch_type,
            "strategy": strategy,
            "temperature": branch_temperature,
            "metadata": dict(branch.metadata),
        })

    if len(candidate_entries) == 1:
        return outputs.strategy, {}

    _self_play_pool, opponent_pool, planned_self_play_matches = _build_live_opponent_pool(ctx, sqlite=sqlite)
    evaluator = ScenarioEvaluator(ctx.scenario, supervisor)
    runner = EvaluationRunner(evaluator, scoring_backend=settings.scoring_backend)
    selection_results: list[dict[str, Any]] = []

    for candidate in candidate_entries:
        tournament = runner.run(
            candidate=candidate["strategy"],
            seed_base=settings.seed_base + (ctx.generation * 100),
            trials=settings.matches_per_generation,
            limits=HarnessLimits(),
            challenger_elo=ctx.challenger_elo,
            challenger_uncertainty=ctx.challenger_uncertainty,
            opponent_pool=opponent_pool,
        )
        selection_results.append({
            "branch_type": candidate["branch_type"],
            "best_score": tournament.best_score,
            "mean_score": tournament.mean_score,
            "strategy": candidate["strategy"],
            "temperature": candidate["temperature"],
            "metadata": dict(candidate.get("metadata", {})),
        })

    selected = max(
        selection_results,
        key=lambda item: (float(item["best_score"]), float(item["mean_score"])),
    )
    branch_record = BranchRecord(
        generation=ctx.generation,
        branch_type=str(selected["branch_type"]),
        score=float(selected["best_score"]),
        advanced=False,
        metadata={
            "selection_mean_score": float(selected["mean_score"]),
            "selection_match_count": settings.matches_per_generation,
            "self_play_matches_planned": planned_self_play_matches,
            "multi_basin_triggered": multi_basin_triggered,
            "divergent_triggered": divergent_triggered,
        },
    )
    metadata = {
        "selected_branch": branch_record.to_dict(),
        "candidates": [
            {
                "branch_type": str(item["branch_type"]),
                "best_score": float(item["best_score"]),
                "mean_score": float(item["mean_score"]),
                "temperature": float(item["temperature"]),
                "metadata": dict(item["metadata"]),
            }
            for item in selection_results
        ],
    }
    if events is not None:
        events.emit("exploration_selected", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            **metadata,
        })
    return dict(selected["strategy"]), metadata
