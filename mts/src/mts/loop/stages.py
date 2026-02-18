"""Decomposed generation pipeline stage functions."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from mts.loop.stage_types import GenerationContext
from mts.prompts.templates import build_prompt_bundle

if TYPE_CHECKING:
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.storage import ArtifactStore, SQLiteStore


def stage_knowledge_setup(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
) -> GenerationContext:
    """Stage 1: Load knowledge context and build prompts."""
    scenario = ctx.scenario
    ablation = ctx.settings.ablation_no_feedback

    state = scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
    observation = scenario.get_observation(state, player_id="challenger")

    playbook = "" if ablation else artifacts.read_playbook(ctx.scenario_name)
    tool_context = "" if ablation else artifacts.read_tool_context(ctx.scenario_name)
    skills_context = "" if ablation else artifacts.read_skills(ctx.scenario_name)
    recent_analysis = "" if ablation else artifacts.read_latest_advance_analysis(ctx.scenario_name, ctx.generation)
    score_trajectory = "" if ablation else trajectory_builder.build_trajectory(ctx.run_id)
    strategy_registry = "" if ablation else trajectory_builder.build_strategy_registry(ctx.run_id)

    summary_text = f"best score so far: {ctx.previous_best:.4f}"
    strategy_interface = scenario.describe_strategy_interface()

    prompts = build_prompt_bundle(
        scenario_rules=scenario.describe_rules(),
        strategy_interface=strategy_interface,
        evaluation_criteria=scenario.describe_evaluation_criteria(),
        previous_summary=summary_text,
        observation=observation,
        current_playbook=playbook,
        available_tools=tool_context,
        operational_lessons=skills_context,
        replay_narrative="" if ablation else ctx.replay_narrative,
        coach_competitor_hints="" if ablation else ctx.coach_competitor_hints,
        recent_analysis=recent_analysis,
        score_trajectory=score_trajectory,
        strategy_registry=strategy_registry,
    )

    ctx.prompts = prompts
    ctx.strategy_interface = strategy_interface
    ctx.tool_context = tool_context
    return ctx


def stage_agent_generation(
    ctx: GenerationContext,
    *,
    orchestrator: AgentOrchestrator,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    on_role_event: Any | None = None,
) -> GenerationContext:
    """Stage 2: Run agent orchestration and validate strategy."""
    assert ctx.prompts is not None, "stage_knowledge_setup must run first"

    outputs = orchestrator.run_generation(
        ctx.prompts,
        generation_index=ctx.generation,
        tool_context=ctx.tool_context,
        run_id=ctx.run_id,
        scenario_name=ctx.scenario_name,
        strategy_interface=ctx.strategy_interface,
        on_role_event=on_role_event,
    )

    state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
    valid, reason = ctx.scenario.validate_actions(state, "challenger", outputs.strategy)
    if not valid:
        raise ValueError(f"competitor strategy validation failed: {reason}")

    sqlite.append_agent_output(ctx.run_id, ctx.generation, "competitor", json.dumps(outputs.strategy, sort_keys=True))
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "analyst", outputs.analysis_markdown)
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "coach", outputs.coach_markdown)
    sqlite.append_agent_output(ctx.run_id, ctx.generation, "architect", outputs.architect_markdown)
    for role_execution in outputs.role_executions:
        sqlite.append_agent_role_metric(
            ctx.run_id,
            ctx.generation,
            role_execution.role,
            role_execution.usage.model,
            role_execution.usage.input_tokens,
            role_execution.usage.output_tokens,
            role_execution.usage.latency_ms,
            role_execution.subagent_id,
            role_execution.status,
        )
    created_tools = artifacts.persist_tools(ctx.scenario_name, ctx.generation, outputs.architect_tools)

    ctx.outputs = outputs
    ctx.current_strategy = outputs.strategy
    ctx.created_tools = created_tools
    return ctx
