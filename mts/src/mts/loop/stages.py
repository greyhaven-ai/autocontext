"""Decomposed generation pipeline stage functions."""

from __future__ import annotations

import dataclasses
import json
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from mts.agents.architect import parse_dag_changes
from mts.backpressure.trend_gate import ScoreHistory, TrendAwareGate
from mts.harness.evaluation.failure_report import FailureReport
from mts.harness.evaluation.runner import EvaluationRunner
from mts.harness.evaluation.scenario_evaluator import ScenarioEvaluator
from mts.harness.evaluation.types import EvaluationLimits as HarnessLimits
from mts.harness.evaluation.types import EvaluationResult, EvaluationSummary
from mts.harness.pipeline.validity_gate import ValidityGate
from mts.knowledge.dead_end_manager import DeadEndEntry, consolidate_dead_ends
from mts.knowledge.fresh_start import execute_fresh_start
from mts.knowledge.harness_quality import compute_harness_quality
from mts.knowledge.progress import build_progress_snapshot
from mts.knowledge.protocol import parse_research_protocol, validate_tuning_overrides
from mts.knowledge.rapid_gate import rapid_gate, should_transition_to_linear
from mts.knowledge.stagnation import StagnationDetector
from mts.knowledge.tuning import TuningConfig, parse_tuning_proposal
from mts.loop.stage_types import GenerationContext
from mts.prompts.templates import build_prompt_bundle
from mts.storage.artifacts import EMPTY_PLAYBOOK_SENTINEL

if TYPE_CHECKING:
    from mts.agents.curator import KnowledgeCurator
    from mts.agents.orchestrator import AgentOrchestrator
    from mts.backpressure import BackpressureGate
    from mts.execution.supervisor import ExecutionSupervisor
    from mts.knowledge.trajectory import ScoreTrajectoryBuilder
    from mts.loop.events import EventStreamEmitter
    from mts.storage import ArtifactStore, SQLiteStore

LOGGER = logging.getLogger(__name__)


def _load_validity_harness_loader(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> Any | None:
    """Load harness validators for two-tier validity checks when enabled."""
    if not ctx.settings.harness_validators_enabled:
        return None

    from mts.execution.harness_loader import HarnessLoader

    harness_dir = artifacts.harness_dir(ctx.scenario_name)
    if not harness_dir.exists():
        return None

    loader = HarnessLoader(
        harness_dir,
        timeout_seconds=ctx.settings.harness_timeout_seconds,
    )
    loader.load()
    return loader


def _build_empty_tournament(ctx: GenerationContext) -> EvaluationSummary:
    """Create a zero-match summary for rollback paths that skip execution."""
    return EvaluationSummary(
        mean_score=0.0,
        best_score=0.0,
        wins=0,
        losses=0,
        elo_after=ctx.challenger_elo,
        results=[],
    )


def _revise_strategy_for_validity_failure(
    ctx: GenerationContext,
    *,
    current_strategy: dict[str, Any],
    errors: list[str],
    retry_attempt: int,
    agents: AgentOrchestrator | None,
) -> dict[str, Any] | None:
    """Ask the competitor to fix an invalid strategy before running matches."""
    if agents is None or ctx.prompts is None:
        return None

    is_code_strategy = "__code__" in current_strategy
    retry_prompt = (
        ctx.prompts.competitor
        + f"\n\n--- VALIDITY RETRY ATTEMPT {retry_attempt} ---\n"
        + "Your previous strategy failed pre-tournament validation.\n"
        + "Validation errors:\n"
        + "\n".join(f"- {error}" for error in errors)
        + "\n"
    )
    if is_code_strategy:
        retry_prompt += "Adjust your code so it satisfies the harness and scenario contracts.\n"
        if ctx.settings.code_strategies_enabled:
            from mts.prompts.templates import code_strategy_competitor_suffix

            retry_prompt += code_strategy_competitor_suffix(ctx.strategy_interface)
    else:
        retry_prompt += (
            f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
            "Return a revised valid strategy. Do not repeat the same invalid approach.\n"
        )

    try:
        raw_text, _ = agents.competitor.run(retry_prompt, tool_context=ctx.tool_context)
        if is_code_strategy:
            revised_strategy, _ = agents.translator.translate_code(raw_text)
        else:
            revised_strategy, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
        return revised_strategy
    except Exception:
        LOGGER.debug("validity retry competitor re-invocation failed", exc_info=True)
        return None


def _apply_tuning_to_settings(
    ctx: GenerationContext,
    parameters: dict[str, float | int],
) -> None:
    """Apply validated tuning parameters to ctx.settings (Pydantic model copy)."""
    if not parameters:
        return
    update: dict[str, Any] = {}
    for key, value in parameters.items():
        if hasattr(ctx.settings, key):
            update[key] = value
    if update:
        ctx.settings = ctx.settings.model_copy(update=update)


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

    progress_json_str = ""
    if not ablation and ctx.settings.progress_json_enabled:
        progress_data = artifacts.read_progress(ctx.scenario_name)
        if progress_data:
            progress_json_str = json.dumps(progress_data, indent=2, sort_keys=True)

    # #185 - Load tuning.json when config_adaptive_enabled
    if ctx.settings.config_adaptive_enabled:
        raw_tuning = artifacts.read_tuning(ctx.scenario_name)
        if raw_tuning:
            try:
                tuning_config = TuningConfig.from_json(raw_tuning)
                _apply_tuning_to_settings(ctx, tuning_config.parameters)
            except (json.JSONDecodeError, ValueError):
                LOGGER.warning("Failed to parse tuning.json for %s", ctx.scenario_name)

    # #166 - Apply protocol tuning overrides when protocol_enabled
    if ctx.settings.protocol_enabled:
        raw_protocol = artifacts.read_research_protocol(ctx.scenario_name)
        if raw_protocol:
            protocol = parse_research_protocol(raw_protocol)
            # Apply exploration mode from protocol
            if protocol.exploration_mode != ctx.settings.exploration_mode:
                ctx.settings = ctx.settings.model_copy(
                    update={"exploration_mode": protocol.exploration_mode},
                )
            # Apply tuning overrides from protocol
            if protocol.tuning_overrides:
                # Cast to dict[str, object] for validate_tuning_overrides signature
                raw_overrides: dict[str, object] = dict(protocol.tuning_overrides)
                validated = validate_tuning_overrides(raw_overrides)
                _apply_tuning_to_settings(ctx, validated)

    experiment_log = "" if ablation else trajectory_builder.build_experiment_log(ctx.run_id)

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
        progress_json=progress_json_str,
        experiment_log=experiment_log,
        constraint_mode=ctx.settings.constraint_prompts_enabled,
        context_budget_tokens=ctx.settings.context_budget_tokens,
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
    on_role_event: Callable[[str, str], None] | None = None,
    events: EventStreamEmitter | None = None,
) -> GenerationContext:
    """Stage 2: Run agent orchestration and validate strategy."""
    if ctx.prompts is None:
        raise RuntimeError("stage_knowledge_setup must run first")

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
        scenario_rules=ctx.scenario.describe_rules(),
        current_strategy=ctx.current_strategy or None,
    )

    if "__code__" not in outputs.strategy:
        state = ctx.scenario.initial_state(seed=ctx.settings.seed_base + ctx.generation)
        valid, reason = ctx.scenario.validate_actions(state, "challenger", outputs.strategy)
        if not valid:
            raise ValueError(f"competitor strategy validation failed: {reason}")

    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[
            ("competitor", json.dumps(outputs.strategy, sort_keys=True)),
            ("analyst", outputs.analysis_markdown),
            ("coach", outputs.coach_markdown),
            ("architect", outputs.architect_markdown),
        ],
        role_metrics=[
            (
                role_execution.role,
                role_execution.usage.model,
                role_execution.usage.input_tokens,
                role_execution.usage.output_tokens,
                role_execution.usage.latency_ms,
                role_execution.subagent_id,
                role_execution.status,
            )
            for role_execution in outputs.role_executions
        ],
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

    # Persist harness validators if enabled
    if ctx.settings.harness_validators_enabled and outputs.architect_harness_specs:
        artifacts.persist_harness(ctx.scenario_name, ctx.generation, outputs.architect_harness_specs)

    # Parse DAG change directives from architect output
    ctx.dag_changes = parse_dag_changes(outputs.architect_markdown)

    # #186 - Parse tuning proposal from architect output when config_adaptive_enabled
    if ctx.settings.config_adaptive_enabled:
        ctx.tuning_proposal = parse_tuning_proposal(outputs.architect_markdown)

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
    if ctx.outputs is None:
        raise RuntimeError("stage_agent_generation must run first")

    settings = ctx.settings
    scenario = ctx.scenario
    current_strategy = dict(ctx.current_strategy)
    attempt = 0
    gate_decision = "rollback"
    tournament = None
    use_rapid = settings.exploration_mode == "rapid"
    validity_retry_attempt = 0
    validity_gate = None

    # --- Tier 1: Validity gate (AC-160) ---
    if settings.two_tier_gating_enabled:
        harness_loader = _load_validity_harness_loader(ctx, artifacts=artifacts)
        validity_gate = ValidityGate(
            harness_loader=harness_loader,
            scenario=scenario,
            max_retries=settings.validity_max_retries,
        )

    while True:
        if validity_gate is not None:
            validity_result = validity_gate.check(current_strategy)
            if not validity_result.passed:
                events.emit("validity_check_failed", {
                    "run_id": ctx.run_id,
                    "generation": ctx.generation,
                    "errors": validity_result.errors,
                    "retry_budget_remaining": validity_result.retry_budget_remaining,
                })
                can_retry = validity_gate.consume_retry()
                if can_retry:
                    validity_retry_attempt += 1
                    revised_strategy = _revise_strategy_for_validity_failure(
                        ctx,
                        current_strategy=current_strategy,
                        errors=validity_result.errors,
                        retry_attempt=validity_retry_attempt,
                        agents=agents,
                    )
                    if revised_strategy is not None:
                        current_strategy = revised_strategy
                    time.sleep(settings.retry_backoff_seconds * validity_retry_attempt)
                    continue

                # Validity budget exhausted: rollback without tournament
                gate_decision = "rollback"
                gate_delta = 0.0
                tournament = _build_empty_tournament(ctx)
                events.emit("gate_decided", {
                    "run_id": ctx.run_id,
                    "generation": ctx.generation,
                    "decision": gate_decision,
                    "delta": gate_delta,
                    "tier": "validity",
                })
                ctx.score_history.append(0.0)
                ctx.gate_decision_history.append(gate_decision)
                ctx.gate_decision = gate_decision
                ctx.gate_delta = gate_delta
                ctx.current_strategy = current_strategy
                ctx.attempt = validity_retry_attempt
                ctx.tournament = tournament
                return ctx

            events.emit("validity_check_passed", {
                "run_id": ctx.run_id,
                "generation": ctx.generation,
            })

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

        # #168 + #172 - Exploration mode gate selection
        if use_rapid:
            gate_result_rapid = rapid_gate(tournament.best_score, ctx.previous_best)
            gate_decision = gate_result_rapid.decision
            # Rapid mode: no retry, only advance or rollback
        elif isinstance(gate, TrendAwareGate):
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
            gate_decision = gate_result.decision
        else:
            gate_result = gate.evaluate(
                ctx.previous_best,
                tournament.best_score,
                retry_count=attempt,
                max_retries=settings.max_retries,
            )
            gate_decision = gate_result.decision

        if gate_decision == "retry" and not use_rapid:
            attempt += 1
            sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
            if attempt > settings.max_retries:
                gate_decision = "rollback"
                break
            # Retry learning: re-invoke competitor with failure context
            if agents is not None and ctx.prompts is not None:
                is_code_strategy = "__code__" in current_strategy
                retry_prompt = (
                    ctx.prompts.competitor
                    + f"\n\n--- RETRY ATTEMPT {attempt} ---\n"
                    f"Your previous strategy scored {tournament.best_score:.4f} "
                    f"but needed delta >= {settings.backpressure_min_delta} over {ctx.previous_best:.4f}.\n"
                )
                if is_code_strategy:
                    retry_prompt += "Adjust your code to improve. Do not repeat the same approach.\n"
                    if settings.code_strategies_enabled:
                        from mts.prompts.templates import code_strategy_competitor_suffix
                        retry_prompt += code_strategy_competitor_suffix(ctx.strategy_interface)
                else:
                    retry_prompt += (
                        f"Previous strategy: {json.dumps(current_strategy, sort_keys=True)}\n"
                        f"Adjust your strategy to improve. Do not repeat the same approach.\n"
                    )
                # Enrich retry prompt with structured failure analysis
                failure_report = FailureReport.from_tournament(
                    tournament,
                    previous_best=ctx.previous_best,
                    threshold=settings.backpressure_min_delta,
                    strategy=current_strategy,
                )
                retry_prompt += "\n" + failure_report.to_prompt_context()
                try:
                    raw_text, _ = agents.competitor.run(retry_prompt, tool_context=ctx.tool_context)
                    if is_code_strategy:
                        revised_strategy, _ = agents.translator.translate_code(raw_text)
                    else:
                        revised_strategy, _ = agents.translator.translate(raw_text, ctx.strategy_interface)
                    if "__code__" not in revised_strategy:
                        state = scenario.initial_state(seed=settings.seed_base + ctx.generation)
                        valid, reason = scenario.validate_actions(state, "challenger", revised_strategy)
                        if valid:
                            current_strategy = revised_strategy
                            sqlite.append_agent_output(
                                ctx.run_id,
                                ctx.generation,
                                "competitor",
                                json.dumps(revised_strategy, sort_keys=True),
                            )
                    else:
                        current_strategy = revised_strategy
                        sqlite.append_agent_output(
                            ctx.run_id,
                            ctx.generation,
                            "competitor",
                            json.dumps(revised_strategy, sort_keys=True),
                        )
                except Exception:
                    LOGGER.debug("retry-learning competitor re-invocation failed", exc_info=True)
            time.sleep(settings.retry_backoff_seconds * attempt)
            continue

        if not use_rapid:
            sqlite.append_recovery_marker(ctx.run_id, ctx.generation, gate_decision, gate_result.reason, attempt)
        break

    if tournament is None:
        raise RuntimeError("tournament was not initialized")

    # #173 - Auto-transition from rapid to linear
    if use_rapid and should_transition_to_linear(ctx.generation, settings.rapid_gens):
        ctx.settings = ctx.settings.model_copy(update={"exploration_mode": "linear"})

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
    artifacts.buffered_write_markdown(gen_dir / "narrative.md", replay_narrative)

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


def stage_stagnation_check(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
    events: EventStreamEmitter,
) -> GenerationContext:
    """Stage 3b: Check for stagnation and trigger fresh start if needed."""
    if not ctx.settings.stagnation_reset_enabled:
        return ctx
    if ctx.settings.ablation_no_feedback:
        return ctx

    detector = StagnationDetector(
        rollback_threshold=ctx.settings.stagnation_rollback_threshold,
        plateau_window=ctx.settings.stagnation_plateau_window,
        plateau_epsilon=ctx.settings.stagnation_plateau_epsilon,
    )
    report = detector.detect(ctx.gate_decision_history, ctx.score_history)

    if not report.is_stagnated:
        return ctx

    lessons = artifacts.read_skill_lessons_raw(ctx.scenario_name)
    hint = execute_fresh_start(
        artifacts=artifacts,
        scenario_name=ctx.scenario_name,
        current_strategy=ctx.current_strategy,
        lessons=lessons,
        top_n=ctx.settings.stagnation_distill_top_lessons,
    )

    ctx.coach_competitor_hints = hint
    ctx.fresh_start_triggered = True

    events.emit("fresh_start", {
        "run_id": ctx.run_id,
        "generation": ctx.generation,
        "trigger": report.trigger,
        "detail": report.detail,
    })

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
    if not current_pb or current_pb == EMPTY_PLAYBOOK_SENTINEL:
        return ctx

    events.emit("curator_started", {
        "run_id": ctx.run_id, "generation": ctx.generation,
    })

    curator_trajectory = trajectory_builder.build_trajectory(ctx.run_id)
    curator_analysis = artifacts.read_latest_advance_analysis(ctx.scenario_name, ctx.generation)

    # Compute harness quality signal if harness validators are enabled
    harness_quality_section = ""
    if ctx.settings.harness_validators_enabled and ctx.tournament is not None:
        quality = compute_harness_quality(ctx.tournament.results)
        harness_quality_section = quality.to_prompt_section()

    curator_decision, curator_exec = curator.assess_playbook_quality(
        current_playbook=current_pb,
        proposed_playbook=ctx.outputs.coach_playbook,
        score_trajectory=curator_trajectory,
        recent_analysis=curator_analysis,
        constraint_mode=ctx.settings.constraint_prompts_enabled,
        harness_quality_section=harness_quality_section,
    )

    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[("curator", curator_exec.content)],
        role_metrics=[(
            curator_exec.role,
            curator_exec.usage.model,
            curator_exec.usage.input_tokens,
            curator_exec.usage.output_tokens,
            curator_exec.usage.latency_ms,
            curator_exec.subagent_id,
            curator_exec.status,
        )],
    )

    if curator_decision.decision == "reject":
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook="")
        # Roll back harness files on reject when harness inheritance is active
        if ctx.settings.harness_validators_enabled and ctx.settings.harness_inheritance_enabled:
            for name in artifacts.list_harness(ctx.scenario_name):
                artifacts.rollback_harness(ctx.scenario_name, name)
    elif curator_decision.decision == "merge" and curator_decision.playbook:
        ctx.outputs = dataclasses.replace(ctx.outputs, coach_playbook=curator_decision.playbook)
    # "accept" -> no change to outputs

    events.emit("curator_completed", {
        "run_id": ctx.run_id, "generation": ctx.generation,
        "decision": curator_decision.decision,
    })

    return ctx


def _persist_skill_note(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> None:
    """Write skill note — advance lessons or rollback warning."""
    tournament = ctx.tournament
    assert tournament is not None  # caller guarantees
    outputs = ctx.outputs
    assert outputs is not None
    gate_decision = ctx.gate_decision
    gate_delta = ctx.gate_delta
    generation = ctx.generation
    settings = ctx.settings

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
        scenario_name=ctx.scenario_name,
        generation_index=generation,
        decision=gate_decision,
        lessons=skill_lessons,
    )

    # Dead-end registry: record rollback as dead end
    if gate_decision == "rollback" and settings.dead_end_tracking_enabled:
        strategy_json = json.dumps(ctx.current_strategy, sort_keys=True)
        entry = DeadEndEntry.from_rollback(
            generation=generation,
            strategy=strategy_json,
            score=tournament.best_score,
        )
        artifacts.append_dead_end(ctx.scenario_name, entry.to_markdown())


def _run_curator_consolidation(
    ctx: GenerationContext,
    *,
    curator: KnowledgeCurator,
    artifacts: ArtifactStore,
    trajectory_builder: ScoreTrajectoryBuilder,
    sqlite: SQLiteStore,
) -> None:
    """Consolidate lessons and dead-ends via curator."""
    settings = ctx.settings
    scenario_name = ctx.scenario_name

    existing_lessons = artifacts.read_skill_lessons_raw(scenario_name)
    if len(existing_lessons) <= settings.skill_max_lessons:
        return

    consolidation_trajectory = trajectory_builder.build_trajectory(ctx.run_id)
    lesson_result, lesson_exec = curator.consolidate_lessons(
        existing_lessons, settings.skill_max_lessons, consolidation_trajectory,
        constraint_mode=settings.constraint_prompts_enabled,
    )
    artifacts.replace_skill_lessons(scenario_name, lesson_result.consolidated_lessons)
    sqlite.append_generation_agent_activity(
        ctx.run_id,
        ctx.generation,
        outputs=[("curator_consolidation", lesson_exec.content)],
        role_metrics=[(
            lesson_exec.role,
            lesson_exec.usage.model,
            lesson_exec.usage.input_tokens,
            lesson_exec.usage.output_tokens,
            lesson_exec.usage.latency_ms,
            lesson_exec.subagent_id,
            lesson_exec.status,
        )],
    )

    # Dead-end consolidation
    if settings.dead_end_tracking_enabled:
        dead_end_text = artifacts.read_dead_ends(scenario_name)
        if dead_end_text:
            consolidated = consolidate_dead_ends(dead_end_text, max_entries=settings.dead_end_max_entries)
            artifacts.replace_dead_ends(scenario_name, consolidated)


def _persist_progress_snapshot(
    ctx: GenerationContext,
    *,
    artifacts: ArtifactStore,
) -> None:
    """Write progress JSON snapshot if enabled."""
    tournament = ctx.tournament
    assert tournament is not None  # caller guarantees
    scenario_name = ctx.scenario_name

    progress_lessons = artifacts.read_skill_lessons_raw(scenario_name)
    snapshot = build_progress_snapshot(
        generation=ctx.generation,
        best_score=ctx.previous_best,
        best_elo=ctx.challenger_elo,
        mean_score=tournament.mean_score,
        gate_history=ctx.gate_decision_history,
        score_history=ctx.score_history,
        current_strategy=ctx.current_strategy,
        lessons=[lesson.lstrip("- ") for lesson in progress_lessons],
    )
    artifacts.write_progress(scenario_name, snapshot.to_dict())


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
    if ctx.tournament is None:
        raise RuntimeError("stage_tournament must run first")
    if ctx.outputs is None:
        raise RuntimeError("stage_agent_generation must run first")

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
    replay_payload: dict[str, object] = {}
    if tournament.results:
        replay_payload = tournament.results[0].metadata["execution_output"].replay.model_dump()

    artifacts.persist_generation(
        run_id=run_id,
        generation_index=generation,
        metrics=metrics,
        replay_payload=replay_payload,
        analysis_md=outputs.analysis_markdown,
        coach_md=outputs.coach_markdown,
        architect_md=outputs.architect_markdown,
        scenario_name=scenario_name,
        coach_playbook=outputs.coach_playbook if gate_decision == "advance" else "",
    )

    # 5. Write skill note + dead-end tracking
    _persist_skill_note(ctx, artifacts=artifacts)

    # 6. Curator lesson consolidation
    existing_lessons_check = artifacts.read_skill_lessons_raw(scenario_name)
    severely_over = len(existing_lessons_check) > settings.skill_max_lessons * 2
    if (
        curator is not None
        and settings.curator_enabled
        and (generation % settings.curator_consolidate_every_n_gens == 0 or severely_over)
        and not settings.ablation_no_feedback
    ):
        _run_curator_consolidation(
            ctx, curator=curator, artifacts=artifacts,
            trajectory_builder=trajectory_builder, sqlite=sqlite,
        )

    # 7. Carry forward coach hints
    coach_competitor_hints = outputs.coach_competitor_hints
    ctx.coach_competitor_hints = coach_competitor_hints
    if gate_decision == "advance" and coach_competitor_hints:
        artifacts.write_hints(scenario_name, coach_competitor_hints)

    # 7b. Write progress snapshot
    if settings.progress_json_enabled and not settings.ablation_no_feedback:
        _persist_progress_snapshot(ctx, artifacts=artifacts)

    # 8. Persist tuning proposal on advance
    if (
        ctx.tuning_proposal is not None
        and settings.config_adaptive_enabled
        and gate_decision == "advance"
    ):
        artifacts.write_tuning(scenario_name, ctx.tuning_proposal.to_json())

    # 9. Emit generation_completed event
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
