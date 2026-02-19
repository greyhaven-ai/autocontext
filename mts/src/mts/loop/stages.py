"""Decomposed generation pipeline stage functions."""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from typing import TYPE_CHECKING, Any

from mts.backpressure.trend_gate import ScoreHistory, TrendAwareGate
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.harness.evaluation.types import EvaluationResult
from mts.loop.stage_types import GenerationContext
from mts.prompts.templates import build_prompt_bundle

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.execution.supervisor import ExecutionSupervisor
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)


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
    events: EventStreamEmitter | None = None,
) -> GenerationContext:
    """Stage 2: Run agent orchestration and validate strategy."""
    assert ctx.prompts is not None, "stage_knowledge_setup must run first"

    if events is not None:
        roles = ["competitor", "analyst", "coach", "architect"]
        if orchestrator.curator is not None:
            roles.append("curator")
        events.emit("agents_started", {
            "run_id": ctx.run_id, "generation": ctx.generation, "roles": roles,
        })

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
    if events is not None:
        for role_execution in outputs.role_executions:
            events.emit("role_completed", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
                "role": role_execution.role,
                "latency_ms": role_execution.usage.latency_ms,
                "tokens": role_execution.usage.input_tokens + role_execution.usage.output_tokens,
            })
    created_tools = artifacts.persist_tools(ctx.scenario_name, ctx.generation, outputs.architect_tools)

    ctx.outputs = outputs
    ctx.current_strategy = outputs.strategy
    ctx.created_tools = created_tools
    return ctx


def stage_tournament(
    ctx: GenerationContext,
    *,
    supervisor: ExecutionSupervisor,
    gate: BackpressureGate | TrendAwareGate,
    events: EventStreamEmitter,
    sqlite: SQLiteStore,
    artifacts: ArtifactStore,
    agents: AgentOrchestrator | None = None,
) -> GenerationContext:
    """Stage 3: Run tournament matches, evaluate gate, retry if needed."""
    assert ctx.outputs is not None, "stage_agent_generation must run first"

    settings = ctx.settings
    scenario = ctx.scenario
    current_strategy = dict(ctx.current_strategy)
    attempt = 0
    gate_decision = "rollback"
    tournament = None

    while True:
        events.emit("tournament_started", {
            "run_id": ctx.run_id,
            "generation": ctx.generation,
            "matches": settings.matches_per_generation,
        })

        def _on_match(match_index: int, score: float, _gen: int = ctx.generation) -> None:
            events.emit("match_completed", {
                "run_id": ctx.run_id, "generation": _gen,
                "match_index": match_index, "score": score,
            })

        try:
            evaluator = ScenarioEvaluator(scenario, supervisor)
            harness_limits = HarnessLimits()

            def _on_result(idx: int, result: EvaluationResult) -> None:
                _on_match(idx, result.score)

            runner = EvaluationRunner(evaluator)
            tournament = runner.run(
                candidate=current_strategy,
                seed_base=settings.seed_base + (ctx.generation * 100) + (attempt * 10),
                trials=settings.matches_per_generation,
                limits=harness_limits,
                challenger_elo=ctx.challenger_elo,
                on_result=_on_result,
            )
        except Exception:
            attempt += 1
            if attempt > settings.max_retries:
                raise
            time.sleep(settings.retry_backoff_seconds * attempt)
            continue

        if isinstance(gate, TrendAwareGate):
            best_eval = max(tournament.results, key=lambda r: r.score)
            best_exec = best_eval.metadata["execution_output"]
            custom_metrics = scenario.custom_backpressure(best_exec.result)
            gate_result = gate.evaluate(
                ctx.previous_best,
                tournament.best_score,
                retry_count=attempt,
                max_retries=settings.max_retries,
                history=ScoreHistory(
                    scores=tuple(ctx.score_history),
                    gate_decisions=tuple(ctx.gate_decision_history),
                ),
                custom_metrics=custom_metrics,
            )
        else:
            gate_result = gate.evaluate(
                ctx.previous_best,
                tournament.best_score,
                retry_count=attempt,
                max_retries=settings.max_retries,
            )

        gate_decision = gate_result.decision

        if gate_decision == "retry":
            attempt += 1
            sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
            if attempt > settings.max_retries:
                gate_decision = "rollback"
                break
            # Retry learning: re-invoke competitor with failure context
            if agents is not None and ctx.prompts is not None:
                retry_prompt = (
                    ctx.prompts.competitor
                    + f"\n\n--- RETRY ATTEMPT {attempt} ---\n"
                    f"Your previous strategy scored {tournament.best_score:.4f} "
                    f"but needed delta >= {settings.backpressure_min_delta} over {ctx.previous_best:.4f}.\n"
                    f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
                    f"Adjust your strategy to improve. Do not repeat the same approach.\n"
                )
                try:
                    raw_text, _ = agents.competitor.run(retry_prompt, tool_context=ctx.tool_context)
                    revised_strategy, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
                    state = scenario.initial_state(seed=settings.seed_base + ctx.generation)
                    valid, reason = scenario.validate_actions(state, "challenger", revised_strategy)
                    if valid:
                        current_strategy = revised_strategy
                except Exception:
                    pass  # Fall back to current strategy
            time.sleep(settings.retry_backoff_seconds * attempt)
            continue

        sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
        break

    assert tournament is not None

    events.emit("tournament_completed", {
        "run_id": ctx.run_id, "generation": ctx.generation,
        "mean_score": tournament.mean_score, "best_score": tournament.best_score,
        "wins": tournament.wins, "losses": tournament.losses,
    })

    gate_delta = round(tournament.best_score - ctx.previous_best, 6)
    events.emit("gate_decided", {
        "run_id": ctx.run_id, "generation": ctx.generation,
        "decision": gate_decision, "delta": gate_delta,
    })

    # Generate replay narrative from best match for next generation
    best_eval = max(tournament.results, key=lambda r: r.score)
    best_exec = best_eval.metadata["execution_output"]
    replay_narrative = scenario.replay_to_narrative(best_exec.result.replay)
    gen_dir = artifacts.generation_dir(ctx.run_id, ctx.generation)
    artifacts.write_markdown(gen_dir / "narrative.md", replay_narrative)

    # Accumulate history for trend-aware gate
    ctx.score_history.append(tournament.best_score)
    ctx.gate_decision_history.append(gate_decision)

    if gate_decision == "advance":
        ctx.previous_best = max(ctx.previous_best, tournament.best_score)
        ctx.challenger_elo = tournament.elo_after

    ctx.tournament = tournament
    ctx.gate_decision = gate_decision
    ctx.gate_delta = gate_delta
    ctx.replay_narrative = replay_narrative
    ctx.current_strategy = current_strategy
    ctx.attempt = attempt
    return ctx


def stage_curator_gate(
    ctx: GenerationContext,
    *,
    curator: KnowledgeCurator | None,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
    sqlite: SQLiteStore,
    events: EventStreamEmitter,
) -> GenerationContext:
    """Stage 4: Curator quality gate — assess playbook before persisting."""
    if ctx.gate_decision != "advance":
        return ctx
    if curator is None:
        return ctx
    if not ctx.outputs or not ctx.outputs.coach_playbook:
        return ctx
    if ctx.settings.ablation_no_feedback:
        return ctx

    current_pb = artifacts.read_playbook(ctx.scenario_name)
    if not current_pb or current_pb == "No playbook yet. Start from scenario rules and observation.":
        return ctx

    events.emit("curator_started", {
        "run_id": ctx.run_id, "generation": ctx.generation,
    })

    curator_trajectory = trajectory_builder.build_trajectory(ctx.run_id)
    curator_analysis = artifacts.read_latest_advance_analysis(ctx.scenario_name, ctx.generation)

    curator_decision, curator_exec = curator.assess_playbook_quality(
        current_playbook=current_pb,
        proposed_playbook=ctx.outputs.coach_playbook,
        score_trajectory=curator_trajectory,
        recent_analysis=curator_analysis,
    )

    sqlite.append_agent_output(
        ctx.run_id, ctx.generation, "curator", curator_exec.content,
    )
    sqlite.append_agent_role_metric(
        ctx.run_id, ctx.generation, curator_exec.role, curator_exec.usage.model,
        curator_exec.usage.input_tokens, curator_exec.usage.output_tokens,
        curator_exec.usage.latency_ms, curator_exec.subagent_id, curator_exec.status,
    )

    if curator_decision.decision == "reject":
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook="")
    elif curator_decision.decision == "merge" and curator_decision.playbook:
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook=curator_decision.playbook)
    # "accept" -> no change to outputs

    events.emit("curator_completed", {
        "run_id": ctx.run_id, "generation": ctx.generation,
        "decision": curator_decision.decision,
    })

    return ctx


def stage_persistence(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    sqlite: SQLiteStore,
    trajectory_builder: ScoreTrajectoryBuilder,
    events: EventStreamEmitter,
    curator: KnowledgeCurator | None,
) -> GenerationContext:
    """Stage 5: Persist generation results, metrics, and knowledge artifacts."""
    assert ctx.tournament is not None, "stage_tournament must run first"
    assert ctx.outputs is not None, "stage_agent_generation must run first"

    tournament = ctx.tournament
    outputs = ctx.outputs
    generation = ctx.generation
    settings = ctx.settings
    scenario_name = ctx.scenario_name
    run_id = ctx.run_id
    gate_decision = ctx.gate_decision
    gate_delta = ctx.gate_delta

    # 1. Build metrics dict
    metrics = {
        "generation_index": generation,
        "mean_score": tournament.mean_score,
        "best_score": ctx.previous_best,
        "elo": ctx.challenger_elo,
        "wins": tournament.wins,
        "losses": tournament.losses,
        "runs": settings.matches_per_generation,
        "gate_decision": gate_decision,
        "gate_delta": gate_delta,
        "gate_threshold": settings.backpressure_min_delta,
    }

    # 2. Insert matches into sqlite
    for idx, eval_result in enumerate(tournament.results):
        match_output = eval_result.metadata["execution_output"]
        sqlite.insert_match(
            run_id, generation,
            settings.seed_base + (generation * 100) + idx,
            match_output.result.score,
            match_output.result.passed_validation,
            json.dumps(match_output.result.validation_errors),
        )

    # 3. Upsert generation
    sqlite.upsert_generation(
        run_id, generation,
        mean_score=tournament.mean_score,
        best_score=ctx.previous_best,
        elo=ctx.challenger_elo,
        wins=tournament.wins,
        losses=tournament.losses,
        gate_decision=gate_decision,
        status="completed",
    )

    # 4. Persist generation artifacts
    artifacts.persist_generation(
        run_id=run_id,
        generation_index=generation,
        metrics=metrics,
        replay_payload=tournament.results[0].metadata["execution_output"].replay.model_dump(),
        analysis_md=outputs.analysis_markdown,
        coach_md=outputs.coach_markdown,
        architect_md=outputs.architect_markdown,
        scenario_name=scenario_name,
        coach_playbook=outputs.coach_playbook if gate_decision == "advance" else "",
    )

    # 5. Write skill note
    if gate_decision == "advance":
        skill_lessons = outputs.coach_lessons
    else:
        retry_note = f" after {ctx.attempt} retries" if ctx.attempt > 0 else ""
        skill_lessons = (
            f"- Generation {generation} ROLLBACK{retry_note} "
            f"(score={tournament.best_score:.4f}, "
            f"delta={gate_delta:+.4f}, threshold={settings.backpressure_min_delta}). "
            f"Strategy: {json.dumps(ctx.current_strategy, sort_keys=True)[:200]}. "
            f"Narrative: {ctx.replay_narrative[:150]}. "
            f"Avoid this approach."
        )
    artifacts.persist_skill_note(
        scenario_name=scenario_name,
        generation_index=generation,
        decision=gate_decision,
        lessons=skill_lessons,
    )

    # 6. Curator lesson consolidation
    existing_lessons_check = artifacts.read_skill_lessons_raw(scenario_name)
    severely_over = len(existing_lessons_check) > settings.skill_max_lessons * 2
    if (
        curator is not None
        and settings.curator_enabled
        and (generation % settings.curator_consolidate_every_n_gens == 0 or severely_over)
        and not settings.ablation_no_feedback
    ):
        existing_lessons = artifacts.read_skill_lessons_raw(scenario_name)
        if len(existing_lessons) > settings.skill_max_lessons:
            consolidation_trajectory = trajectory_builder.build_trajectory(run_id)
            lesson_result, lesson_exec = curator.consolidate_lessons(
                existing_lessons, settings.skill_max_lessons, consolidation_trajectory,
            )
            artifacts.replace_skill_lessons(scenario_name, lesson_result.consolidated_lessons)
            sqlite.append_agent_output(
                run_id, generation, "curator_consolidation", lesson_exec.content,
            )
            sqlite.append_agent_role_metric(
                run_id, generation, lesson_exec.role, lesson_exec.usage.model,
                lesson_exec.usage.input_tokens, lesson_exec.usage.output_tokens,
                lesson_exec.usage.latency_ms, lesson_exec.subagent_id, lesson_exec.status,
            )

    # 7. Carry forward coach hints
    coach_competitor_hints = outputs.coach_competitor_hints
    ctx.coach_competitor_hints = coach_competitor_hints
    if gate_decision == "advance" and coach_competitor_hints:
        artifacts.write_hints(scenario_name, coach_competitor_hints)

    # 8. Emit generation_completed event
    events.emit("generation_completed", {
        "run_id": run_id,
        "generation": generation,
        "mean_score": tournament.mean_score,
        "best_score": ctx.previous_best,
        "elo": ctx.challenger_elo,
        "gate_decision": gate_decision,
        "gate_delta": gate_delta,
        "created_tools": ctx.created_tools,
    })

    return ctx
